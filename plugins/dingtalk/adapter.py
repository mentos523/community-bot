"""钉钉自定义群机器人适配器

配置：
  webhook_url: 群机器人 Webhook 地址（含 access_token）（或环境变量 DINGTALK_WEBHOOK）
  secret:      加签密钥，SEC 开头（或环境变量 DINGTALK_SECRET）

说明：
  钉钉自定义机器人是单向推送机制，无法主动拉取群消息，
  因此 fetch_updates() 返回空；如需接收消息，需另起 webhook 接收服务
 （钉钉后台配置 outgoing 回调）。reply() 通过加签 Webhook 发送。
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

TEXT_LIMIT = 20000


def _chunks(text, limit):
    """超长文本按 limit 切片，分多条发送而非截断丢字（M8）。"""
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _http_post(url, body, timeout=30):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception:
        return 0, {}


class Adapter(PlatformAdapter):
    def __init__(self, config):
        super().__init__(config)
        self.webhook = self._resolve("webhook_url", "DINGTALK_WEBHOOK")
        self.secret = self._resolve("secret", "DINGTALK_SECRET")

    def _resolve(self, key, env_var):
        return (self.config.get(key) or os.environ.get(env_var, "") or "").strip()

    def validate_config(self):
        if not self._resolve("webhook_url", "DINGTALK_WEBHOOK"):
            raise ValueError("缺少 webhook_url（配置或环境变量 DINGTALK_WEBHOOK）")

    def _signed_url(self):
        url = self.webhook
        if self.secret:
            ts = str(int(time.time() * 1000))
            string_to_sign = f"{ts}\n{self.secret}"
            digest = hmac.new(
                self.secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha256,
            ).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}timestamp={ts}&sign={sign}"
        return url

    def fetch_updates(self) -> list[Message]:
        # 钉钉自定义机器人无法主动拉取消息，返回空。
        # 如需接收：在钉钉后台为机器人配置 outgoing 回调地址，
        # 由外部 webhook 服务接收后转给引擎。
        return []

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        # 一个 webhook 对应一个群，conversation_id 仅作标识，不参与寻址
        if not content or not content.strip():
            return False
        # 超长内容分多条发送（M8），原先 content[:20000] 直接截断会丢字
        for chunk in _chunks(content, TEXT_LIMIT):
            body = {"msgtype": "text", "text": {"content": chunk}}
            _, data = _http_post(self._signed_url(), body)
            if data.get("errcode") != 0:
                return False
        return True

    def get_info(self) -> dict:
        return {"platform": "dingtalk", "mode": "webhook-send-only"}
