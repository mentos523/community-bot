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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

REQUEST_TIMEOUT = 20


class Adapter(PlatformAdapter):
    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = (config.get("url") or "").rstrip("/")
        self.api_key = config.get("api_key") or os.environ.get("XENFORO_API_KEY", "")
        self.node_id = config.get("node_id", "")

    def _headers(self):
        return {
            "XF-Api-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "CommunityBot/1.0",
        }

    def _get(self, path: str, params: dict = None):
        try:
            url = f"{self.base_url}{path}"
            if params:
                url += "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                return json.load(r)
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
                created_at=str(t.get("post_date", "")),
                raw=t,
            ))
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        """拉取主题下的帖子"""
        data = self._get(f"/api/threads/{conversation_id}/posts/")
        if not data:
            return []
        msgs = []
        for p in data.get("posts", []) or []:
            user = p.get("User") or {}
            msgs.append(Message(
                id=str(p.get("post_id", "")),
                platform="xenforo",
                conversation_id=str(conversation_id),
                author_id=str(user.get("user_id", "")),
                author_name=user.get("username", ""),
                content=p.get("message", ""),
                created_at=str(p.get("post_date", "")),
                raw=p,
            ))
        return msgs

    def reply(self, conversation_id: str, content: str) -> bool:
        """发表回复"""
        return self._post("/api/posts/", {
            "thread_id": conversation_id,
            "message": content,
        })
