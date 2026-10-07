"""WhatsApp Cloud API 适配器（Meta）"""
import sys, os, json, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

TIMEOUT = 20
GRAPH_BASE = "https://graph.facebook.com"


class Adapter(PlatformAdapter):
    """收消息走 Webhook（由 Web 层调用 ingest_webhook 写入缓冲），发消息走 Cloud API。"""

    def __init__(self, config: dict):
        self._buffer: list[Message] = []
        super().__init__(config)

    def _cfg(self, key, env_var, default=""):
        return self.config.get(key) or os.environ.get(env_var, default)

    def _api(self, method: str, path: str, payload: dict | None = None):
        version = self._cfg("api_version", "WHATSAPP_API_VERSION", "v21.0")
        token = self._cfg("access_token", "WHATSAPP_ACCESS_TOKEN")
        url = f"{GRAPH_BASE}/{version}/{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)

    def ingest_webhook(self, payload: dict):
        """Web 层收到 Meta Webhook 后调用，把消息写入缓冲。"""
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    if msg.get("type") != "text":
                        continue
                    self._buffer.append(Message(
                        id=str(msg.get("id", "")),
                        platform="whatsapp",
                        conversation_id=str(msg.get("from", "")),
                        author_id=str(msg.get("from", "")),
                        content=(msg.get("text") or {}).get("body", ""),
                        created_at=str(msg.get("timestamp", "")),
                        raw=msg,
                    ))

    def fetch_updates(self) -> list[Message]:
        """取出 Webhook 缓冲中的新消息。"""
        msgs, self._buffer = self._buffer, []
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # 聊天类无需详情
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        """conversation_id 为收件人电话号码（含国家码）。"""
        try:
            phone_id = self._cfg("phone_number_id", "WHATSAPP_PHONE_NUMBER_ID")
            data = self._api("POST", f"{phone_id}/messages", {
                "messaging_product": "whatsapp",
                "to": conversation_id,
                "type": "text",
                "text": {"body": content},
            })
            return bool((data.get("messages") or [{}])[0].get("id"))
        except Exception:
            return False
