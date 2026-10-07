"""企业微信适配器（仅发送模式）

企业微信官方没有"拉取消息"的 API（/cgi-bin/message/list 不存在）。
接收消息的正规途径是：在企业微信后台配置消息接收 URL（回调推送），
或开通会话内容存档（付费）。本插件仅支持发送消息。
"""
import sys, os, json, time, logging, urllib.request, urllib.parse, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message, AuthError
from src.core.resilience import ResilienceMixin

TIMEOUT = 20
API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

logger = logging.getLogger(__name__)


class Adapter(PlatformAdapter, ResilienceMixin):
    def __init__(self, config: dict):
        self._init_resilience()
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

    def _api_post(self, path: str, payload: dict):
        """POST 调用企业微信 API。

        R-M3：401/403 抛 AuthError；H-M3：429 标记非阻塞退避后抛给调用方处理。
        企业微信用 errcode 表示业务错误：40014/42001/42007/42009 为 token 类问题。
        """
        token = self._get_access_token()
        url = f"{API_BASE}{path}?access_token={token}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError("企业微信认证失效（401/403）")
            if e.code == 429:
                self._mark_rate_limited()
            raise

    def fetch_updates(self) -> list[Message]:
        # 企业微信官方 API 不提供消息拉取端点（/cgi-bin/message/list 不存在）。
        # 接收消息需在企业微信后台配置消息接收 URL（回调推送）或开通会话内容存档；
        # 本插件为仅发送模式，此处返回空列表（S2）。
        # F-N1：info 降为 debug，避免每轮刷屏
        logger.debug("企业微信插件为仅发送模式，fetch_updates 返回空（官方无拉取 API）")
        return []

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # 聊天类无需详情，fetch_updates 已含完整内容
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        """发送文本消息。conversation_id 为 touser 或 chatid（chat: 前缀）。"""
        # H-M3/F-M1：退避期内直接返回 False
        if self._in_backoff() or self._rate_limited():
            return False
        try:
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
            data = self._api_post("/message/send", payload)
            errcode = data.get("errcode", -1)
            if errcode == 0:
                self._note_success()
                return True
            # token 相关 errcode：清掉缓存的 token，下次重取（类 refresh）
            if errcode in (40014, 42001, 42007, 42009):
                self._access_token = None
                self._note_failure()
                return False
            # 45009 达到发送频率上限：标记限流退避
            if errcode == 45009:
                self._mark_rate_limited()
            self._note_failure()
            return False
        except AuthError:
            raise
        except Exception:
            self._note_failure()
            return False
