"""企业微信适配器"""
import sys, os, json, time, urllib.request, urllib.parse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

TIMEOUT = 20
API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"


class Adapter(PlatformAdapter):
    def __init__(self, config: dict):
        self._access_token = None
        self._token_expires_at = 0
        super().__init__(config)

    def _cfg(self, key, env_var, default=""):
        return self.config.get(key) or os.environ.get(env_var, default)

    def _get_access_token(self):
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token
        corp_id = self._cfg("corp_id", "WECOM_CORP_ID")
        corp_secret = self._cfg("corp_secret", "WECOM_CORP_SECRET")
        url = f"{API_BASE}/gettoken?corpid={urllib.parse.quote(corp_id)}&corpsecret={urllib.parse.quote(corp_secret)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.load(r)
        if data.get("errcode") != 0:
            raise RuntimeError(f"企业微信获取 token 失败: {data}")
        self._access_token = data["access_token"]
        self._token_expires_at = now + int(data.get("expires_in", 7200))
        return self._access_token

    def fetch_updates(self) -> list[Message]:
        """拉取消息列表（轻量轮询）"""
        try:
            token = self._get_access_token()
            url = f"{API_BASE}/message/list?access_token={token}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.load(r)
        except Exception:
            return []
        if data.get("errcode") != 0:
            self._access_token = None  # token 可能失效，下次重取
            return []
        msgs = []
        for item in data.get("msglist", []):
            msgs.append(Message(
                id=str(item.get("msgid", "")),
                platform="wecom",
                conversation_id=str(item.get("chatid") or item.get("touser") or ""),
                author_id=str(item.get("from", "")),
                content=item.get("text", {}).get("content", "") if isinstance(item.get("text"), dict) else str(item.get("content", "")),
                created_at=item.get("msgtime", ""),
                raw=item,
            ))
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # 聊天类无需详情，fetch_updates 已含完整内容
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        """发送文本消息。conversation_id 为 touser 或 chatid（chat: 前缀）。"""
        try:
            token = self._get_access_token()
            agent_id = self._cfg("agent_id", "WECOM_AGENT_ID")
            payload = {
                "msgtype": "text",
                "agentid": int(agent_id) if str(agent_id).isdigit() else agent_id,
                "text": {"content": content},
                "safe": 0,
            }
            if conversation_id.startswith("chat:"):
                payload["chatid"] = conversation_id[5:]
            else:
                payload["touser"] = conversation_id
            req = urllib.request.Request(
                f"{API_BASE}/message/send?access_token={token}",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.load(r)
            return data.get("errcode") == 0
        except Exception:
            return False
