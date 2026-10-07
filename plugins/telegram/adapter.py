"""Telegram Bot 适配器（长轮询实现）

配置：
  bot_token: BotFather 申请的 Token（或环境变量 TELEGRAM_BOT_TOKEN）
  chat_id:   可选，只监听指定 chat（或不过滤）
"""
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

API = "https://api.telegram.org"
TEXT_LIMIT = 4000  # Telegram 单条消息上限 4096，留余量


def _http(method, url, body=None, timeout=35):
    """最小 HTTP 客户端，只用标准库。返回 (status_code, dict)。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
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
        self.token = self._resolve("bot_token", "TELEGRAM_BOT_TOKEN")
        self.chat_filter = str(self.config.get("chat_id") or "").strip()
        self._offset = 0  # getUpdates 水位

    def _resolve(self, key, env_var):
        return (self.config.get(key) or os.environ.get(env_var, "") or "").strip()

    def validate_config(self):
        if not self._resolve("bot_token", "TELEGRAM_BOT_TOKEN"):
            raise ValueError("缺少 bot_token（配置或环境变量 TELEGRAM_BOT_TOKEN）")

    def _api(self, method, params=None, body=None, timeout=35):
        url = f"{API}/bot{self.token}/{method}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return _http("GET" if body is None else "POST", url, body, timeout)

    def fetch_updates(self) -> list[Message]:
        params = {
            "offset": self._offset,
            "timeout": 25,  # 长轮询 25 秒
            "allowed_updates": json.dumps(["message", "channel_post"]),
        }
        code, data = self._api("getUpdates", params=params, timeout=35)
        msgs = []
        if code != 200 or not data.get("ok"):
            return msgs
        for upd in data.get("result", []):
            uid = upd.get("update_id", 0)
            self._offset = max(self._offset, uid + 1)
            m = upd.get("message") or upd.get("channel_post")
            if not m:
                continue
            frm = m.get("from") or {}
            if frm.get("is_bot"):
                continue  # 跳过机器人自己/其他机器人，避免回环
            chat = m.get("chat") or {}
            cid = str(chat.get("id", ""))
            if self.chat_filter and cid != self.chat_filter:
                continue
            text = m.get("text") or m.get("caption") or ""
            if not text.strip():
                continue
            name = frm.get("username") or (
                (frm.get("first_name", "") + " " + frm.get("last_name", "")).strip()
            )
            ts = m.get("date", 0)
            msgs.append(Message(
                id=f"tg-{uid}",
                platform="telegram",
                conversation_id=cid,
                conversation_title=chat.get("title", ""),
                author_id=str(frm.get("id", "")),
                author_name=name,
                content=text,
                created_at=datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                raw=upd,
            ))
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # IM 类：消息即详情，无需二次拉取
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        if not content or not content.strip():
            return False
        cid = conversation_id or self.chat_filter
        if not cid:
            return False
        for chunk in _chunks(content, TEXT_LIMIT):
            code, data = self._api("sendMessage", body={"chat_id": cid, "text": chunk})
            if code != 200 or not data.get("ok"):
                return False
        return True

    def get_info(self) -> dict:
        return {"platform": "telegram", "mode": self.config.get("mode", "polling")}
