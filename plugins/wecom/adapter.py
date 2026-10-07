"""企业微信适配器（仅发送模式）

企业微信官方没有"拉取消息"的 API（/cgi-bin/message/list 不存在）。
接收消息的正规途径是：在企业微信后台配置消息接收 URL（回调推送），
或开通会话内容存档（付费）。本插件仅支持发送消息。
"""
import sys, os, json, time, logging, urllib.request, urllib.parse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

TIMEOUT = 20
API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

logger = logging.getLogger(__name__)


class Adapter(PlatformAdapter):
    def __init__(self, config: dict):
        self._access_token = None
        self._token_expires_at = 0
        super().__init__(config)

    def _cfg(self, key, env_var, default=""):
        v = self.config.get(key) or os.environ.get(env_var, default)
        return str(v).strip() if v is not None else ""

    def validate_config(self):
        # 空值提前报错，避免拼出 corpid= 的无效请求（N9）
        if not self._cfg("corp_id", "WECOM_CORP_ID"):
            raise ValueError("缺少 corp_id（配置或环境变量 WECOM_CORP_ID）")
        if not self._cfg("corp_secret", "WECOM_CORP_SECRET"):
            raise ValueError("缺少 corp_secret（配置或环境变量 WECOM_CORP_SECRET）")
        if not self._cfg("agent_id", "WECOM_AGENT_ID"):
            raise ValueError("缺少 agent_id（配置或环境变量 WECOM_AGENT_ID）")

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
        # 企业微信官方 API 不提供消息拉取端点（/cgi-bin/message/list 不存在）。
        # 接收消息需在企业微信后台配置消息接收 URL（回调推送）或开通会话内容存档；
        # 本插件为仅发送模式，此处返回空列表（S2）。
        logger.info("企业微信插件为仅发送模式，fetch_updates 返回空（官方无拉取 API）")
        return []

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
