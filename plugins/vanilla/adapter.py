"""Vanilla Forums 适配器（API v2）

官方文档：https://docs.vanillaforums.com/apiv2/
- 讨论列表：GET /api/v2/discussions?sort=-dateLastComment&categoryID={id}
- 评论列表：GET /api/v2/comments?discussionID={id}&sort=dateInserted
- 发表评论：POST /api/v2/comments  {"discussionID": ..., "body": "..."}

认证：请求头 Authorization: Bearer <token>。
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
        self.api_token = config.get("api_token") or os.environ.get("VANILLA_API_TOKEN", "")
        self.category_id = config.get("category_id", "")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_token}",
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
        """轻量拉取讨论列表（按最后评论排序）"""
        params = {"sort": "-dateLastComment", "limit": 20}
        if self.category_id:
            params["categoryID"] = self.category_id
        data = self._get("/api/v2/discussions", params)
        if not isinstance(data, list):
            return []
        msgs = []
        for d in data:
            msgs.append(Message(
                id=str(d.get("discussionID", "")),
                platform="vanilla",
                conversation_id=str(d.get("discussionID", "")),
                conversation_title=d.get("name", ""),
                author_id=str(d.get("insertUserID", "")),
                author_name=d.get("insertUser", {}).get("name", "") if isinstance(d.get("insertUser"), dict) else "",
                content=d.get("name", ""),
                created_at=str(d.get("dateInserted", "")),
                raw=d,
            ))
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        """拉取讨论下的评论"""
        data = self._get("/api/v2/comments", {
            "discussionID": conversation_id,
            "sort": "dateInserted",
            "limit": 100,
        })
        if not isinstance(data, list):
            return []
        msgs = []
        for c in data:
            user = c.get("insertUser") or {}
            msgs.append(Message(
                id=str(c.get("commentID", "")),
                platform="vanilla",
                conversation_id=str(conversation_id),
                author_id=str(c.get("insertUserID", "")),
                author_name=user.get("name", "") if isinstance(user, dict) else "",
                content=c.get("body", ""),
                created_at=str(c.get("dateInserted", "")),
                raw=c,
            ))
        return msgs

    def reply(self, conversation_id: str, content: str) -> bool:
        """发表评论"""
        return self._post("/api/v2/comments", {
            "discussionID": int(conversation_id) if str(conversation_id).isdigit() else conversation_id,
            "body": content,
            "format": "Text",
        })
