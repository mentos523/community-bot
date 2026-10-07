"""Discourse 论坛适配器

API:
  GET  /latest.json        轻量主题列表
  GET  /t/{id}.json        主题详情（含 post_stream）
  POST /posts.json         发布回复（topic_id + raw）
认证：请求头 Api-Key + Api-Username
"""
import html as _html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import Message, PlatformAdapter

TIMEOUT = 20


def _conf(config, key, env_var=""):
    v = config.get(key) or (os.environ.get(env_var) if env_var else "")
    return (v or "").strip()


def _strip_html(s):
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return _html.unescape(s).strip()


class Adapter(PlatformAdapter):
    """Discourse 平台适配器"""

    def __init__(self, config):
        super().__init__(config)
        self.base = _conf(config, "url").rstrip("/")
        self._key = _conf(config, "api_key", "DISCOURSE_API_KEY")
        self._username = _conf(config, "api_username", "DISCOURSE_API_USERNAME")
        self._category = _conf(config, "category_filter")

    def _request(self, method, path, payload=None):
        try:
            data = None
            headers = {"Accept": "application/json"}
            if self._key:
                headers["Api-Key"] = self._key
            if self._username:
                headers["Api-Username"] = self._username
            if payload is not None:
                data = urllib.parse.urlencode(payload).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            req = urllib.request.Request(
                self.base + path, data=data, headers=headers, method=method
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception:
            return 0, None

    def fetch_updates(self):
        """轻量拉取最新主题列表"""
        if not self.base:
            return []
        path = "/latest.json"
        if self._category:
            path = "/c/{}.json".format(urllib.parse.quote(self._category))
        code, data = self._request("GET", path)
        if code != 200 or not data:
            return []
        topics = ((data.get("topic_list") or {}).get("topics")) or []
        out = []
        for t in topics:
            out.append(
                Message(
                    id=str(t.get("id")),
                    platform="discourse",
                    conversation_id=str(t.get("id")),
                    conversation_title=t.get("title", ""),
                    author_name=t.get("last_poster_username", ""),
                    created_at=t.get("last_posted_at", "") or t.get("created_at", ""),
                    raw={"posts_count": t.get("posts_count", 0)},
                )
            )
        return out

    def fetch_detail(self, conversation_id):
        """拉取主题的全部帖子"""
        code, data = self._request("GET", "/t/{}.json".format(conversation_id))
        if code != 200 or not data:
            return []
        title = data.get("title", "")
        out = []
        for p in ((data.get("post_stream") or {}).get("posts")) or []:
            content = _strip_html(p.get("cooked") or "")
            out.append(
                Message(
                    id=str(p.get("id")),
                    platform="discourse",
                    conversation_id=str(conversation_id),
                    conversation_title=title,
                    author_id=str(p.get("user_id", "")),
                    author_name=p.get("username", ""),
                    content=content,
                    created_at=p.get("created_at", ""),
                    raw={"post_number": p.get("post_number", 0)},
                )
            )
        out.sort(key=lambda m: m.raw.get("post_number", 0))
        return out

    def reply(self, conversation_id, content):
        """在主题下发布回复"""
        code, data = self._request(
            "POST", "/posts.json", {"topic_id": conversation_id, "raw": content}
        )
        return code in (200, 201) and bool(data) and bool(data.get("id"))
