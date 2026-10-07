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
        self.api_key = config.get("api_key") or os.environ.get("MYBB_API_KEY", "")
        self.fid = config.get("fid", "")

    def _headers(self):
        return {
            "X-API-Key": self.api_key,
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
                created_at=str(t.get("dateline", t.get("created_at", ""))),
                raw=t,
            ))
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        """拉取主题下的帖子"""
        data = self._get(f"/api/threads/{conversation_id}/posts")
        msgs = []
        for p in self._as_list(data):
            msgs.append(Message(
                id=str(p.get("pid", p.get("id", ""))),
                platform="mybb",
                conversation_id=str(conversation_id),
                author_id=str(p.get("uid", p.get("author_id", ""))),
                author_name=p.get("username", p.get("author", "")),
                content=p.get("message", p.get("content", "")),
                created_at=str(p.get("dateline", p.get("created_at", ""))),
                raw=p,
            ))
        return msgs

    def reply(self, conversation_id: str, content: str) -> bool:
        """发表回复"""
        return self._post(
            f"/api/threads/{conversation_id}/posts",
            {"message": content},
        )
