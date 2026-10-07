"""WhatsApp Cloud API 适配器（Meta）"""
import sys, os, json, hmac, hashlib, time, urllib.request, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message, AuthError
from src.core.resilience import ResilienceMixin

TIMEOUT = 20
GRAPH_BASE = "https://graph.facebook.com"
TEXT_LIMIT = 4096  # WhatsApp 文本消息上限
BUFFER_MAX = 1000  # H-M2：Webhook 缓冲上限，超了丢弃最旧


def _chunks(text, limit):
    """超长文本按 limit 切片。"""
    return [text[i:i + limit] for i in range(0, len(text), limit)]


class Adapter(PlatformAdapter, ResilienceMixin):
    """收消息走 Webhook（由 Web 层调用 ingest_webhook 写入缓冲），发消息走 Cloud API。"""

    def __init__(self, config: dict):
        self._init_resilience()
        self._buffer: list[Message] = []
        self._buffer_ids: set[str] = set()  # H-M2：按消息 ID 去重
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
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # R-M3：401/403 抛 AuthError，由调度器统一处理
            if e.code in (401, 403):
                raise AuthError("WhatsApp 认证失效（401/403）")
            # H-M3：429 限流标记非阻塞退避
            if e.code == 429:
                self._mark_rate_limited()
            raise

    def verify_signature(self, body: bytes, signature: str) -> bool:
        """校验 Meta Webhook 签名（X-Hub-Signature-256: sha256=<hex>）。

        用 Meta 应用密钥（WHATSAPP_APP_SECRET）做 HMAC-SHA256。
        未配置密钥时返回 False（拒绝无鉴权写入）。
        """
        secret = self._cfg("app_secret", "WHATSAPP_APP_SECRET").encode("utf-8")
        if not secret:
            return False
        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    def ingest_webhook(self, payload: dict, body: bytes | None = None,
                       signature: str | None = None) -> bool:
        """Web 层收到 Meta Webhook 后调用，把消息写入缓冲。

        body 为原始请求体、signature 为 X-Hub-Signature-256 请求头。
        只要传了 signature 就必须验签通过，否则拒绝写入并返回 False（S5）。
        """
        if signature is not None:
            if body is None or not self.verify_signature(body, signature):
                return False  # 验签失败：拒绝写入
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    if msg.get("type") != "text":
                        continue
                    mid = str(msg.get("id", ""))
                    if not mid or mid in self._buffer_ids:
                        continue  # H-M2：按消息 ID 去重
                    self._buffer.append(Message(
                        id=mid,
                        platform="whatsapp",
                        conversation_id=str(msg.get("from", "")),
                        author_id=str(msg.get("from", "")),
                        content=(msg.get("text") or {}).get("body", ""),
                        created_at=str(msg.get("timestamp", "")),
                        raw=msg,
                    ))
                    self._buffer_ids.add(mid)
                    # H-M2：缓冲上限，超了丢弃最旧
                    while len(self._buffer) > BUFFER_MAX:
                        old = self._buffer.pop(0)
                        self._buffer_ids.discard(old.id)
        return True

    def fetch_updates(self) -> list[Message]:
        """取出 Webhook 缓冲中的新消息。"""
        # F-M1：退避期内直接返回（缓冲保留，下轮再取）
        if self._in_backoff() or self._rate_limited():
            return []
        try:
            msgs, self._buffer = self._buffer, []
            self._buffer_ids.clear()
            self._note_success()
            return msgs
        except Exception:
            self._note_failure()
            return []

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # 聊天类无需详情
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        """conversation_id 为收件人电话号码（含国家码）。超长自动分片发送。"""
        if not content or not content.strip() or not conversation_id:
            return False
        try:
            phone_id = self._cfg("phone_number_id", "WHATSAPP_PHONE_NUMBER_ID")
            for chunk in _chunks(content, TEXT_LIMIT):
                data = self._api("POST", f"{phone_id}/messages", {
                    "messaging_product": "whatsapp",
                    "to": conversation_id,
                    "type": "text",
                    "text": {"body": chunk},
                })
                if not (data.get("messages") or [{}])[0].get("id"):
                    return False
            return True
        except Exception:
            return False
