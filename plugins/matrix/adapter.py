"""Matrix 适配器"""
import sys, os, json, time, urllib.request, urllib.parse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

TIMEOUT = 20


class Adapter(PlatformAdapter):
    def __init__(self, config: dict):
        self._since: str | None = None
        super().__init__(config)

    def _cfg(self, key, env_var, default=""):
        return self.config.get(key) or os.environ.get(env_var, default)

    def _api(self, method: str, path: str, payload: dict | None = None, params: dict | None = None):
        base = self._cfg("homeserver", "MATRIX_HOMESERVER").rstrip("/")
        token = self._cfg("access_token", "MATRIX_ACCESS_TOKEN")
        url = base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)

    def fetch_updates(self) -> list[Message]:
        """sync 长轮询拉取新事件。"""
        try:
            params = {"timeout": "20000"}
            if self._since:
                params["since"] = self._since
            data = self._api("GET", "/_matrix/client/v3/sync", params=params)
        except Exception:
            return []
        self._since = data.get("next_batch", self._since)
        own_id = self._cfg("user_id", "MATRIX_USER_ID")
        msgs = []
        for room_id, room in (data.get("rooms") or {}).get("join", {}).items():
            for ev in (room.get("timeline") or {}).get("events", []):
                if ev.get("type") != "m.room.message":
                    continue
                sender = ev.get("sender", "")
                if own_id and sender == own_id:
                    continue  # 跳过自己发的
                content = ev.get("content", {})
                body = content.get("body", "")
                if content.get("msgtype") != "m.text" or not body:
                    continue
                ts = ev.get("origin_server_ts", 0)
                msgs.append(Message(
                    id=str(ev.get("event_id", "")),
                    platform="matrix",
                    conversation_id=room_id,
                    author_id=sender,
                    content=body,
                    created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts / 1000)) if ts else "",
                    raw=ev,
                ))
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # 聊天类无需详情
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        """conversation_id 为房间 ID。PUT 发送文本消息。"""
        try:
            txn_id = str(int(time.time() * 1000))
            path = f"/_matrix/client/v3/rooms/{urllib.parse.quote(conversation_id, safe='')}/send/m.room.message/{txn_id}"
            data = self._api("PUT", path, {"msgtype": "m.text", "body": content})
            return bool(data.get("event_id"))
        except Exception:
            return False
