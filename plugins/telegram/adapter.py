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
from src.core.base import PlatformAdapter, Message, AuthError
from src.core.resilience import ResilienceMixin

API = "https://api.telegram.org"
TEXT_LIMIT = 4000  # Telegram 单条消息上限 4096，留余量
POLL_TIMEOUT = 10  # getUpdates 长轮询秒数（M4：从 25s 降到 10s，减少调度阻塞）


def _http(method, url, body=None, timeout=35):
    """最小 HTTP 客户端，只用标准库。返回 (status_code, dict)。

    R-M3：401/403 直接抛 AuthError，由调度器统一处理。
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError("Telegram 认证失效（401/403，token 错误或被吊销）")
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception:
        return 0, {}


def _chunks(text, limit):
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _data_file(name: str) -> str:
    """data 目录下持久化文件路径（相对项目根目录 data/）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", name)


def _load_offset() -> int:
    try:
        with open(_data_file("telegram_offset.json"), encoding="utf-8") as f:
            return int(json.load(f).get("offset", 0))
    except Exception:
        return 0


def _save_offset(offset: int) -> None:
    try:
        path = _data_file("telegram_offset.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"offset": offset}, f)
        os.replace(tmp, path)
    except Exception:
        pass


class Adapter(PlatformAdapter, ResilienceMixin):
    def __init__(self, config):
        self._init_resilience()
        super().__init__(config)
        self.token = self._resolve("bot_token", "TELEGRAM_BOT_TOKEN")
        self.chat_filter = str(self.config.get("chat_id") or "").strip()
        self._offset = _load_offset()  # getUpdates 水位，持久化防重启重复（M1）

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
        # F-M1/H-M3：退避期内直接返回，不发起请求
        if self._in_backoff() or self._rate_limited():
            return []
        params = {
            "offset": self._offset,
            "timeout": POLL_TIMEOUT,  # 长轮询秒数
            "allowed_updates": json.dumps(["message", "channel_post"]),
        }
        try:
            code, data = self._api("getUpdates", params=params, timeout=POLL_TIMEOUT + 10)
        except AuthError:
            raise
        except Exception:
            self._note_failure()
            return []
        msgs = []
        # H-M3：Telegram 429 限流时标记非阻塞退避，本轮直接返回
        if code == 429:
            retry_after = 0
            try:
                retry_after = int((data.get("parameters") or {}).get("retry_after", 0))
            except Exception:
                pass
            self._mark_rate_limited(retry_after)
            return msgs
        if code != 200 or not data.get("ok"):
            self._note_failure()
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
        _save_offset(self._offset)  # 水位落盘，重启不丢（M1）
        self._note_success()
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
        return {"platform": "telegram", "mode": "polling"}
