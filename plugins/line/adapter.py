"""LINE Messaging API 适配器

收消息走 Webhook（由 Web 层调用 ingest_webhook 写入缓冲），
发消息走 Messaging API。

reply 策略：ingest_webhook 时记录 conversation_id -> replyToken 映射；
reply(conversation_id, content) 按基类签名调用，内部查最新有效
（10 分钟内）的 replyToken，有则走 /message/reply，过期/无则降级走 /message/push。
"""
import sys, os, json, time, hmac, hashlib, base64, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

TIMEOUT = 20
API_BASE = "https://api.line.me/v2/bot"
TEXT_LIMIT = 5000  # LINE 单条文本消息上限 5000 字符
REPLY_TOKEN_TTL = 600  # replyToken 有效期约 10 分钟，过期自动清理


def _chunks(text, limit):
    """超长文本按 limit 切片。"""
    return [text[i:i + limit] for i in range(0, len(text), limit)]


class Adapter(PlatformAdapter):
    def __init__(self, config: dict):
        # message_id -> (replyToken, 到达时间戳)，已用 token 成功回复后删除
        self._reply_tokens: dict[str, tuple[str, float]] = {}
        # conversation_id -> (message_id, replyToken, 到达时间戳)，reply() 时查用
        self._conv_tokens: dict[str, tuple[str, str, float]] = {}
        self._buffer: list[Message] = []
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

    def _purge_tokens(self):
        """清理超过 TTL 的 replyToken，防止长期运行内存泄漏（M13）。"""
        now = time.time()
        for store in (self._reply_tokens, self._conv_tokens):
            stale = [k for k, v in store.items() if now - v[-1] > REPLY_TOKEN_TTL]
            for k in stale:
                store.pop(k, None)

    def ingest_webhook(self, payload: dict):
        """Web 层收到 LINE Webhook 后调用，把文本消息写入缓冲。"""
        self._purge_tokens()
        now = time.time()
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
            if reply_token and mid:
                self._reply_tokens[mid] = (reply_token, now)
                if conv_id:
                    self._conv_tokens[conv_id] = (mid, reply_token, now)
            ts = ev.get("timestamp", 0)
            self._buffer.append(Message(
                id=mid,
                platform="line",
                conversation_id=conv_id,
                author_id=str(src.get("userId", "")),
                content=str(msg.get("text", "")),
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts / 1000)) if ts else "",
                raw=ev,
            ))

    def fetch_updates(self) -> list[Message]:
        """取出 Webhook 缓冲中的新消息。"""
        msgs, self._buffer = self._buffer, []
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # 聊天类无需详情
        return []

    def _post(self, url: str, payload: dict) -> bool:
        """单次 POST，messages 数组按 LINE 限制每批最多 5 条。"""
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=self._headers())
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status == 200
        except Exception:
            return False

    def reply(self, conversation_id: str, content: str) -> bool:
        """按基类签名 reply(conversation_id, content)。

        查 conversation_id 对应的最新有效 replyToken：
        有则走 /message/reply（免费、即时）；无或已过期则降级走 /message/push。
        超长内容自动分片（每批最多 5 条消息）。
        """
        if not content or not content.strip() or not conversation_id:
            return False
        self._purge_tokens()
        token = None
        entry = self._conv_tokens.get(conversation_id)
        if entry and time.time() - entry[2] <= REPLY_TOKEN_TTL:
            token = entry[1]
        chunks = _chunks(content, TEXT_LIMIT)
        ok = True
        for i in range(0, len(chunks), 5):
            batch = [{"type": "text", "text": c} for c in chunks[i:i + 5]]
            if token:
                url = f"{API_BASE}/message/reply"
                payload = {"replyToken": token, "messages": batch}
            else:
                url = f"{API_BASE}/message/push"
                payload = {"to": conversation_id, "messages": batch}
            if not self._post(url, payload):
                ok = False
                break
        if ok and token:
            # replyToken 一次性有效，用过即删，避免复用 400
            self._conv_tokens.pop(conversation_id, None)
        return ok
