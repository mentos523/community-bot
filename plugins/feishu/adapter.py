"""飞书自建应用机器人适配器（REST 轮询实现）

配置：
  app_id:     飞书开放平台 App ID（或环境变量 FEISHU_APP_ID）
  app_secret: 飞书开放平台 App Secret（或环境变量 FEISHU_APP_SECRET）
  chat_ids:   监听的群聊 chat_id，英文逗号分隔（或环境变量 FEISHU_CHAT_IDS）

说明：tenant_access_token 自动获取并缓存；首轮只记录水位不回历史；
跳过机器人自己发的消息防回环。
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
API = "https://open.feishu.cn/open-apis"
TEXT_LIMIT = 10000


def _http(method, url, headers=None, body=None, timeout=30):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = dict(headers or {})
    if data:
        h["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
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


def _chunks(text, limit):
    return [text[i:i + limit] for i in range(0, len(text), limit)]


class Adapter(PlatformAdapter):
    def __init__(self, config):
        super().__init__(config)
        self.app_id = self._resolve("app_id", "FEISHU_APP_ID")
        self.app_secret = self._resolve("app_secret", "FEISHU_APP_SECRET")
        chs = self.config.get("chat_ids") or os.environ.get("FEISHU_CHAT_IDS", "")
        self.chats = [c.strip() for c in str(chs).split(",") if c.strip()]
        self._token = ""
        self._token_exp = 0
        self._last_time = {}  # chat_id -> int（毫秒时间戳水位）
        self._bot_id = None

    def _resolve(self, key, env_var):
        return (self.config.get(key) or os.environ.get(env_var, "") or "").strip()

    def validate_config(self):
        if not self._resolve("app_id", "FEISHU_APP_ID"):
            raise ValueError("缺少 app_id（配置或环境变量 FEISHU_APP_ID）")
        if not self._resolve("app_secret", "FEISHU_APP_SECRET"):
            raise ValueError("缺少 app_secret（配置或环境变量 FEISHU_APP_SECRET）")
        chs = self.config.get("chat_ids") or os.environ.get("FEISHU_CHAT_IDS", "")
        if not [c.strip() for c in str(chs).split(",") if c.strip()]:
            raise ValueError("缺少 chat_ids（配置或环境变量 FEISHU_CHAT_IDS）")

    def _tenant_token(self):
        if self._token and time.time() < self._token_exp:
            return self._token
        _, data = _http("POST", AUTH_URL,
                        body={"app_id": self.app_id, "app_secret": self.app_secret})
        if data.get("code") == 0:
            self._token = data.get("tenant_access_token", "")
            self._token_exp = time.time() + int(data.get("expire", 7200)) - 120
        return self._token

    def _headers(self):
        return {"Authorization": f"Bearer {self._tenant_token()}"}

    def _bot_open_id(self):
        """尽力获取机器人自身的 open_id，用于跳过自己发的消息。"""
        if self._bot_id is None:
            _, data = _http("GET", f"{API}/bot/v3/info", self._headers())
            if data.get("code") == 0:
                self._bot_id = (data.get("bot") or {}).get("open_id", "")
            else:
                self._bot_id = ""
        return self._bot_id

    @staticmethod
    def _extract_text(item):
        body = item.get("body") or {}
        if body.get("msg_type") != "text":
            return ""
        try:
            return json.loads(body.get("content", "{}")).get("text", "")
        except Exception:
            return ""

    def fetch_updates(self) -> list[Message]:
        if not self._tenant_token():
            return []
        bot_id = self._bot_open_id()
        msgs = []
        for cid in self.chats:
            params = {
                "container_id_type": "chat",
                "container_id": cid,
                "sort_type": "ByCreateTimeDesc",
                "page_size": 20,
            }
            url = f"{API}/im/v1/messages?" + urllib.parse.urlencode(params)
            _, data = _http("GET", url, self._headers())
            if data.get("code") != 0:
                continue
            items = (data.get("data") or {}).get("items", [])
            if not items:
                continue
            max_t = max(int(i.get("create_time", 0)) for i in items)
            if cid not in self._last_time:
                self._last_time[cid] = max_t
                continue  # 首轮只记水位，不回历史
            for i in sorted(items, key=lambda x: int(x.get("create_time", 0))):
                ct = int(i.get("create_time", 0))
                if ct <= self._last_time[cid]:
                    continue
                sender = i.get("sender") or {}
                if bot_id and sender.get("id") == bot_id:
                    continue  # 跳过自己发的消息
                text = self._extract_text(i)
                if not text.strip():
                    continue
                msgs.append(Message(
                    id=i.get("message_id", ""),
                    platform="feishu",
                    conversation_id=cid,
                    conversation_title="",
                    author_id=sender.get("id", ""),
                    author_name="",
                    content=text,
                    created_at=datetime.fromtimestamp(ct / 1000, tz=timezone.utc).isoformat(),
                    raw=i,
                ))
            self._last_time[cid] = max_t
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # IM 类：消息即详情，无需二次拉取
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        if not self._tenant_token():
            return False
        if not content or not content.strip() or not conversation_id:
            return False
        for chunk in _chunks(content, TEXT_LIMIT):
            body = {
                "receive_id": conversation_id,
                "msg_type": "text",
                "content": json.dumps({"text": chunk}, ensure_ascii=False),
            }
            url = f"{API}/im/v1/messages?receive_id_type=chat_id"
            _, data = _http("POST", url, self._headers(), body)
            if data.get("code") != 0:
                return False
        return True
