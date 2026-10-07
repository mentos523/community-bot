"""XenForo 论坛适配器（官方 REST API）

官方文档：https://xenforo.com/docs/dev/rest-api/
- 主题列表：GET /api/threads/?node_id={id}&order=last_post_date&direction=desc
- 主题帖子：GET /api/threads/{thread_id}/posts/
- 发表回复：POST /api/posts/  {"thread_id": ..., "message": "..."}

认证：请求头 XF-Api-Key。
"""
import sys
import os
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message, AuthError

REQUEST_TIMEOUT = 20


def _to_iso(ts):
    """unix 时间戳（秒）-> ISO 8601"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return str(ts)


class Adapter(PlatformAdapter):
    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = (config.get("url") or "").rstrip("/")
        self.api_key = config.get("api_key") or os.environ.get("XENFORO_API_KEY", "")
        self.node_id = config.get("node_id", "")

    def _headers(self):
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "CommunityBot/1.0",
        }
        if self.api_key:
            headers["XF-Api-Key"] = self.api_key
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
                raise AuthError("XenForo 认证失效（401/403）")
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
        except Exception:
            return False

    def fetch_updates(self) -> list[Message]:
        """轻量拉取主题列表（按最后回帖排序）"""
        params = {"order": "last_post_date", "direction": "desc"}
        if self.node_id:
            params["node_id"] = self.node_id
        data = self._get("/api/threads/", params)
        if not data:
            return []
        msgs = []
        for t in data.get("threads", []) or []:
            msgs.append(Message(
                id=str(t.get("thread_id", "")),
                platform="xenforo",
                conversation_id=str(t.get("thread_id", "")),
                conversation_title=t.get("title", ""),
                author_id=str((t.get("User") or {}).get("user_id", "")),
                author_name=(t.get("User") or {}).get("username", ""),
                content=t.get("title", ""),
                # S3 修复：用最后活动时间（last_post_date）做水位，转 ISO
                created_at=_to_iso(t.get("last_post_date") or t.get("post_date", "")),
                raw=t,
            ))
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        """拉取主题下的帖子"""
        data = self._get(f"/api/threads/{conversation_id}/posts/")
        if not data:
            return []
        # N1 修复：补 conversation_title
        title = ""
        thread = data.get("thread") or {}
        if isinstance(thread, dict):
            title = thread.get("title", "")
        if not title:
            tdata = self._get(f"/api/threads/{conversation_id}/")
            if isinstance(tdata, dict):
                t = tdata.get("thread", tdata)
                if isinstance(t, dict):
                    title = t.get("title", "")
        msgs = []
        for p in data.get("posts", []) or []:
            user = p.get("User") or {}
            msgs.append(Message(
                id=str(p.get("post_id", "")),
                platform="xenforo",
                conversation_id=str(conversation_id),
                conversation_title=title,
                author_id=str(user.get("user_id", "")),
                author_name=user.get("username", ""),
                content=p.get("message", ""),
                created_at=_to_iso(p.get("post_date", "")),
                raw=p,
            ))
        return msgs

    def reply(self, conversation_id: str, content: str) -> bool:
        """发表回复"""
        return self._post("/api/posts/", {
            "thread_id": conversation_id,
            "message": content,
        })
