"""MyBB 论坛适配器

MyBB 官方没有 REST API，本适配器依赖第三方 MyBB API 插件
（如 MyBB-API / mybb-rest-api），约定以下端点：
- 主题列表：GET /api/threads?fid={fid}&order=desc
- 帖子列表：GET /api/threads/{tid}/posts
- 发表回复：POST /api/threads/{tid}/posts  {"message": "..."}

认证：请求头 X-API-Key（或按插件文档调整）。
"""
import sys
import os
import json
import html as _html
import re
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message, AuthError
from src.core.resilience import ResilienceMixin

REQUEST_TIMEOUT = 20


def _strip_html(s):
    """R-M1：去掉 HTML 标签，转义字符还原（参考 flarum 实现）。"""
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return _html.unescape(s).strip()


def _to_iso(ts):
    """unix 时间戳（秒）/ ISO 字符串 -> ISO 8601"""
    if not ts:
        return ""
    if isinstance(ts, (int, float)) or str(ts).isdigit():
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        except Exception:
            return str(ts)
    return str(ts)


class Adapter(PlatformAdapter, ResilienceMixin):
    def __init__(self, config: dict):
        self._init_resilience()
        super().__init__(config)
        self.base_url = (config.get("url") or "").rstrip("/")
        self.api_key = config.get("api_key") or os.environ.get("MYBB_API_KEY", "")
        self.fid = config.get("fid", "")

    def refresh_auth(self) -> bool:
        """R-M4：API Key 类型无法自动刷新，返回 False 并记日志。"""
        import logging
        logging.getLogger(__name__).warning(
            "MyBB 使用 API Key 认证，无法自动刷新；请人工更换后更新配置")
        return False

    def _headers(self):
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "CommunityBot/1.0",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _get(self, path: str, params: dict = None):
        try:
            url = f"{self.base_url}{path}"
            if params:
                url += "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError("MyBB 认证失效（401/403）")
            return None
        except Exception:
            return None

    def _post(self, path: str, payload: dict):
        try:
            req = urllib.request.Request(
                f"{self.base_url}{path}",
                data=json.dumps(payload).encode(),
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                return r.status in (200, 201)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError("MyBB 认证失效（401/403）")
            return False
        except Exception:
            return False

    def _as_list(self, data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in ("threads", "posts", "data", "items"):
                if isinstance(data.get(k), list):
                    return data[k]
        return []

    def fetch_updates(self) -> list[Message]:
        """轻量拉取主题列表"""
        if self._in_backoff() or self._rate_limited():
            return []
        try:
            params = {"order": "desc"}
            if self.fid:
                params["fid"] = self.fid
            data = self._get("/api/threads", params)
            msgs = []
            for t in self._as_list(data):
                msgs.append(Message(
                    id=str(t.get("tid", t.get("id", ""))),
                    platform="mybb",
                    conversation_id=str(t.get("tid", t.get("id", ""))),
                    conversation_title=t.get("subject", t.get("title", "")),
                    author_id=str(t.get("uid", t.get("author_id", ""))),
                    author_name=t.get("username", t.get("author", "")),
                    content=t.get("subject", t.get("title", "")),
                    # M7 修复：用最后回复时间做水位，转 ISO
                    created_at=_to_iso(
                        t.get("lastpost", t.get("last_post_time",
                               t.get("dateline", t.get("created_at", ""))))
                    ),
                    raw=t,
                ))
            self._note_success()
            return msgs
        except Exception:
            self._note_failure()
            return []

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        """拉取主题下的帖子"""
        # N1 修复：先拿主题标题
        title = ""
        tdata = self._get(f"/api/threads/{conversation_id}")
        if isinstance(tdata, dict):
            t = tdata
            for k in ("thread", "data"):
                if isinstance(tdata.get(k), dict):
                    t = tdata[k]
                    break
            title = t.get("subject", t.get("title", ""))
        data = self._get(f"/api/threads/{conversation_id}/posts")
        msgs = []
        for p in self._as_list(data):
            msgs.append(Message(
                id=str(p.get("pid", p.get("id", ""))),
                platform="mybb",
                conversation_id=str(conversation_id),
                conversation_title=title,
                author_id=str(p.get("uid", p.get("author_id", ""))),
                author_name=p.get("username", p.get("author", "")),
                # R-M1：帖子内容去 HTML 标签
                content=_strip_html(p.get("message", p.get("content", ""))),
                created_at=_to_iso(p.get("dateline", p.get("created_at", ""))),
                raw=p,
            ))
        return msgs

    def reply(self, conversation_id: str, content: str) -> bool:
        """发表回复"""
        return self._post(
            f"/api/threads/{conversation_id}/posts",
            {"message": content},
        )
