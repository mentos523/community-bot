"""Flarum 论坛适配器

API:
  GET  /api/discussions?sort=-lastPostedAt            轻量讨论列表
  GET  /api/discussions/{id}?include=posts,posts.user 帖子详情
  POST /api/posts                                     发布回复
  POST /api/token                                     账号密码换 token（401 自动重登）
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
from src.core.base import Message, PlatformAdapter, AuthError

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
    """Flarum 平台适配器"""

    def __init__(self, config):
        super().__init__(config)
        self.base = _conf(config, "url").rstrip("/")
        self._token = _conf(config, "token", "FLARUM_TOKEN")
        self._username = _conf(config, "username", "FLARUM_USERNAME")
        self._password = _conf(config, "password", "FLARUM_PASSWORD")
        self._tag = _conf(config, "tag_filter")

    def refresh_auth(self) -> bool:
        """N11 修复：401 时调度器调用此方法，用账号密码全新登录换 token"""
        if not (self.base and self._username and self._password):
            return False
        try:
            data = json.dumps(
                {"identification": self._username, "password": self._password}
            ).encode()
            req = urllib.request.Request(
                self.base + "/api/token",
                data=data,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            r = json.load(urllib.request.urlopen(req, timeout=TIMEOUT))
            tok = r.get("token")
            if tok:
                self._token = tok
                return True
        except Exception:
            pass
        return False

    def _request(self, method, path, payload=None):
        try:
            data = json.dumps(payload).encode() if payload is not None else None
            headers = {"Accept": "application/vnd.api+json"}
            if self._token:
                headers["Authorization"] = "Token {}".format(self._token)
            if data:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(
                self.base + path, data=data, headers=headers, method=method
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            # N11：401/403 抛 AuthError，由调度器统一调 refresh_auth() 重登
            if e.code in (401, 403):
                raise AuthError("Flarum token 失效（401/403）")
            return e.code, None
        except Exception:
            return 0, None

    def fetch_updates(self):
        """轻量拉取讨论列表（不进详情，不刷浏览量）"""
        if not self.base:
            return []
        path = "/api/discussions?sort=-lastPostedAt&page[limit]=15"
        if self._tag:
            path += "&filter[tag]=" + urllib.parse.quote(self._tag)
        code, data = self._request("GET", path)
        if code != 200 or not data:
            return []
        out = []
        for d in data.get("data", []):
            a = d.get("attributes", {}) or {}
            out.append(
                Message(
                    id=str(d.get("id")),
                    platform="flarum",
                    conversation_id=str(d.get("id")),
                    conversation_title=a.get("title", ""),
                    created_at=a.get("lastPostedAt", "") or a.get("createdAt", ""),
                    raw={
                        "lastPostNumber": a.get("lastPostNumber", 0),
                        "commentCount": a.get("commentCount", 0),
                    },
                )
            )
        return out

    def fetch_detail(self, conversation_id):
        """拉取讨论的全部帖子"""
        code, data = self._request(
            "GET", "/api/discussions/{}?include=posts,posts.user".format(conversation_id)
        )
        if code != 200 or not data:
            return []
        attrs = (data.get("data") or {}).get("attributes", {}) or {}
        title = attrs.get("title", "")
        users = {}
        for inc in data.get("included", []):
            if inc.get("type") == "users":
                users[str(inc.get("id"))] = (inc.get("attributes") or {}).get(
                    "displayName", ""
                )
        out = []
        for inc in data.get("included", []):
            if inc.get("type") != "posts":
                continue
            pa = inc.get("attributes", {}) or {}
            rel = ((inc.get("relationships") or {}).get("user") or {}).get("data") or {}
            uid = str(rel.get("id", ""))
            content = pa.get("content") or _strip_html(pa.get("contentHtml") or "")
            out.append(
                Message(
                    id=str(inc.get("id")),
                    platform="flarum",
                    conversation_id=str(conversation_id),
                    conversation_title=title,
                    author_id=uid,
                    author_name=users.get(uid, ""),
                    content=(content or "").strip(),
                    created_at=pa.get("createdAt", ""),
                    raw={"number": pa.get("number", 0)},
                )
            )
        out.sort(key=lambda m: m.raw.get("number", 0))
        return out

    def reply(self, conversation_id, content):
        """发布回复"""
        payload = {
            "data": {
                "type": "posts",
                "attributes": {"content": content},
                "relationships": {
                    "discussion": {
                        "data": {"type": "discussions", "id": str(conversation_id)}
                    }
                },
            }
        }
        code, data = self._request("POST", "/api/posts", payload)
        return code in (200, 201) and bool(data)
