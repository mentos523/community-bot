"""LINE Messaging API 适配器"""
import sys, os, json, hmac, hashlib, base64, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

TIMEOUT = 20
API_BASE = "https://api.line.me/v2/bot"


class Adapter(PlatformAdapter):
    """收消息走 Webhook（由 Web 层调用 ingest_webhook 写入缓冲），发消息走 Messaging API。"""

    def __init__(self, config: dict):
        self._buffer: list[Message] = []
        self._reply_tokens: dict[str, str] = {}  # message_id -> replyToken
        super().__init__(config)

    def _cfg(self, key, env_var, default=""):
        return self.config.get(key) or os.environ.get(env_var, default)

    def _headers(self):
        token = self._cfg("channel_access_token", "LINE_CHANNEL_ACCESS_TOKEN")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def verify_signature(self, body: bytes, signature: str) -> bool:
        """校验 Webhook 签名（X-Line-Signature）。"""
        secret = self._cfg("channel_secret", "LINE_CHANNEL_SECRET").encode("utf-8")
        digest = hmac.new(secret, body, hashlib.sha256).digest()
        return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")

    def ingest_webhook(self, payload: dict):
        """Web 层收到 LINE Webhook 后调用，把文本消息写入缓冲。"""
        for ev in payload.get("events", []):
            if ev.get("type") != "message":
                continue
            msg = ev.get("message", {})
            if msg.get("type") != "text":
                continue
            src = ev.get("source", {})
            conv_id = str(src.get("groupId") or src.get("roomId") or src.get("userId") or "")
            mid = str(msg.get("id", ""))
            reply_token = ev.get("replyToken", "")
            if reply_token:
                self._reply_tokens[mid] = reply_token
            ts = ev.get("timestamp", 0)
            import time as _t
            self._buffer.append(Message(
                id=mid,
                platform="line",
                conversation_id=conv_id,
                author_id=str(src.get("userId", "")),
                content=str(msg.get("text", "")),
                created_at=_t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(ts / 1000)) if ts else "",
                raw=ev,
            ))

    def fetch_updates(self) -> list[Message]:
        """取出 Webhook 缓冲中的新消息。"""
        msgs, self._buffer = self._buffer, []
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # 聊天类无需详情
        return []

    def reply(self, conversation_id: str, content: str, message_id: str = "") -> bool:
        """优先用 replyToken 回复（免费且即时）；否则用 push API。

        conversation_id 为 replyToken（reply 模式）或 userId/groupId/roomId（push 模式）。
        """
        try:
            if conversation_id in self._reply_tokens or not conversation_id.startswith("U"):
                token = self._reply_tokens.pop(message_id, conversation_id)
                payload = {"replyToken": token, "messages": [{"type": "text", "text": content}]}
                url = f"{API_BASE}/message/reply"
            else:
                payload = {"to": conversation_id, "messages": [{"type": "text", "text": content}]}
                url = f"{API_BASE}/message/push"
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=self._headers())
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status == 200
        except Exception:
            return False
