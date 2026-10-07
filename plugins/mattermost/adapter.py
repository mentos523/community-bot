"""Mattermost 适配器（API 兼容 Slack 风格）"""
import sys, os, json, time, urllib.request, urllib.parse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

TIMEOUT = 20


class Adapter(PlatformAdapter):
    def __init__(self, config: dict):
        self._last_seen: dict[str, int] = {}  # channel_id -> 最新已读 post 的 create_at
        self._me_id: str = ""
        super().__init__(config)

    def _cfg(self, key, env_var, default=""):
        return self.config.get(key) or os.environ.get(env_var, default)

    def _api(self, method: str, path: str, payload: dict | None = None, params: dict | None = None):
        base = self._cfg("server_url", "MATTERMOST_SERVER_URL").rstrip("/")
        token = self._cfg("token", "MATTERMOST_TOKEN")
        url = base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)

    def _channels(self) -> list[dict]:
        only = self._cfg("channel_id", "MATTERMOST_CHANNEL_ID")
        if only:
            try:
                return [self._api("GET", f"/api/v4/channels/{only}")]
            except Exception:
                return []
        try:
            return self._api("GET", "/api/v4/users/me/channels") or []
        except Exception:
            return []

    def fetch_updates(self) -> list[Message]:
        """轮询各频道的新帖子。"""
        try:
            me = self._api("GET", "/api/v4/users/me")
            self._me_id = me.get("id", "")
        except Exception:
            return []
        msgs = []
        for ch in self._channels():
            ch_id = ch.get("id", "")
            if not ch_id:
                continue
            try:
                data = self._api("GET", f"/api/v4/channels/{ch_id}/posts",
                                 params={"per_page": 20, "page": 0})
            except Exception:
                continue
            posts = (data.get("posts") or {})
            order = data.get("order") or []
            last_seen = self._last_seen.get(ch_id, 0)
            newest = last_seen
            for pid in order:
                p = posts.get(pid) or {}
                if p.get("user_id") == self._me_id:
                    continue  # 跳过自己发的
                if p.get("type", "") != "":
                    continue  # 跳过系统消息
                created = int(p.get("create_at", 0))
                if created > last_seen:
                    newest = max(newest, created)
                    msgs.append(Message(
                        id=str(p.get("id", "")),
                        platform="mattermost",
                        conversation_id=ch_id,
                        conversation_title=str(ch.get("display_name") or ch.get("name", "")),
                        author_id=str(p.get("user_id", "")),
                        content=str(p.get("message", "")),
                        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created / 1000)) if created else "",
                        raw=p,
                    ))
            self._last_seen[ch_id] = newest
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # 聊天类无需详情
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        """conversation_id 为频道 ID。POST /api/v4/posts。"""
        try:
            data = self._api("POST", "/api/v4/posts", {
                "channel_id": conversation_id,
                "message": content,
            })
            return bool(data.get("id"))
        except Exception:
            return False
