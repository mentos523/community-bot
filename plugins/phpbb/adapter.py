"""phpBB 论坛适配器

注意：phpBB 核心没有内置 REST API，需要先安装第三方 API 扩展。
本适配器按常见扩展的端点约定实现（api_path 可配，默认 /api）：

  GET  {api_path}/topics?limit=15&sort=last_post_time&order=desc   轻量主题列表
  GET  {api_path}/topics/{id}/posts                               主题帖子
  POST {api_path}/topics/{id}/posts  {"content": ...}             发布回复
认证：请求头 X-API-Key（部分扩展用 Authorization: Bearer，按需调整 header）
"""
import html as _html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import Message, PlatformAdapter, AuthError

TIMEOUT = 20


def _conf(config, key, env_var="", default=""):
    v = config.get(key) or (os.environ.get(env_var) if env_var else "") or default
    return (v or "").strip()


def _strip_html(s):
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return _html.unescape(s).strip()


def _to_iso(ts):
    """unix 时间戳 / ISO 字符串 -> ISO 字符串"""
    if not ts:
        return ""
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            return ""
    return str(ts)


class Adapter(PlatformAdapter):
    """phpBB 平台适配器"""

    def __init__(self, config):
        super().__init__(config)
        self.base = _conf(config, "url").rstrip("/")
        self.api = _conf(config, "api_path", default="/api").rstrip("/")
        self._key = _conf(config, "api_key", "PHPBB_API_KEY")
        self._forum = _conf(config, "forum_filter")

    def _request(self, method, path, payload=None):
        try:
            data = json.dumps(payload).encode() if payload is not None else None
            headers = {"Accept": "application/json"}
            if self._key:
                headers["X-API-Key"] = self._key
            if data:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(
                self.base + self.api + path, data=data, headers=headers, method=method
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError("phpBB 认证失效（401/403）")
            return e.code, None
        except Exception:
            return 0, None

    @staticmethod
    def _topics_of(data):
        if not data:
            return []
        if isinstance(data.get("topics"), list):
            return data["topics"]
        if isinstance(data.get("data"), list):
            return data["data"]
        return []

    def fetch_updates(self):
        """轻量拉取主题列表"""
        if not self.base:
            return []
        path = "/topics?limit=15&sort=last_post_time&order=desc"
        if self._forum:
            path += "&forum_id=" + urllib.parse.quote(self._forum)
        code, data = self._request("GET", path)
        if code != 200:
            return []
        out = []
        for t in self._topics_of(data):
            tid = t.get("topic_id", t.get("id", ""))
            out.append(
                Message(
                    id=str(tid),
                    platform="phpbb",
                    conversation_id=str(tid),
                    conversation_title=t.get("topic_title", t.get("title", "")),
                    author_name=t.get("last_poster", t.get("topic_last_poster_name", "")),
                    created_at=_to_iso(
                        t.get("last_post_time", t.get("topic_last_post_time", ""))
                    ),
                    raw={
                        "post_count": t.get(
                            "topic_posts_count", t.get("post_count", 0)
                        )
                    },
                )
            )
        return out

    def fetch_detail(self, conversation_id):
        """拉取主题的全部帖子"""
        # N1 修复：先拿主题标题
        title = ""
        tcode, tdata = self._request("GET", "/topics/{}".format(conversation_id))
        if tcode == 200 and isinstance(tdata, dict):
            t = tdata.get("topic", tdata)
            if isinstance(t, dict):
                title = t.get("topic_title", t.get("title", ""))
        code, data = self._request("GET", "/topics/{}/posts".format(conversation_id))
        if code != 200 or not data:
            return []
        posts = data.get("posts", data.get("data", []))
        out = []
        for p in posts or []:
            content = p.get("post_text", p.get("content", ""))
            content = _strip_html(content)
            out.append(
                Message(
                    id=str(p.get("post_id", p.get("id", ""))),
                    platform="phpbb",
                    conversation_id=str(conversation_id),
                    conversation_title=title,
                    author_id=str(p.get("poster_id", p.get("user_id", ""))),
                    author_name=p.get("username", p.get("poster_name", "")),
                    content=content,
                    created_at=_to_iso(p.get("post_time", "")),
                    raw={},
                )
            )
        # N4 修复：按 post_id 排序
        out.sort(key=lambda m: int(m.id) if m.id.isdigit() else 0)
        return out

    def reply(self, conversation_id, content):
        """在主题下发布回复"""
        code, data = self._request(
            "POST", "/topics/{}/posts".format(conversation_id), {"content": content}
        )
        # M11 修复：只看状态码，空 body 也算成功，避免重复发帖
        return code in (200, 201)
