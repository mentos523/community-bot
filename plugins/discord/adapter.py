"""Discord Bot 适配器（REST 轮询实现）

配置：
  bot_token:   开发者门户申请的 Bot Token（或环境变量 DISCORD_BOT_TOKEN）
  channel_ids: 监听的频道 ID，英文逗号分隔（或环境变量 DISCORD_CHANNEL_IDS）

说明：首轮只记录水位不回历史；跳过机器人消息防回环。
"""
import json
import os
import sys
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

API = "https://discord.com/api/v10"
TEXT_LIMIT = 2000  # Discord 单条消息上限


def _http(method, url, headers=None, body=None, timeout=30):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
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
        self.token = self._resolve("bot_token", "DISCORD_BOT_TOKEN")
        chs = self.config.get("channel_ids") or os.environ.get("DISCORD_CHANNEL_IDS", "")
        self.channels = [c.strip() for c in str(chs).split(",") if c.strip()]
        self._last_ids = {}  # channel_id -> int（雪花 ID 单调递增）
        self._me = None

    def _resolve(self, key, env_var):
        return (self.config.get(key) or os.environ.get(env_var, "") or "").strip()

    def validate_config(self):
        if not self._resolve("bot_token", "DISCORD_BOT_TOKEN"):
            raise ValueError("缺少 bot_token（配置或环境变量 DISCORD_BOT_TOKEN）")
        chs = self.config.get("channel_ids") or os.environ.get("DISCORD_CHANNEL_IDS", "")
        if not [c.strip() for c in str(chs).split(",") if c.strip()]:
            raise ValueError("缺少 channel_ids（配置或环境变量 DISCORD_CHANNEL_IDS）")

    def _headers(self):
        return {"Authorization": f"Bot {self.token}", "Content-Type": "application/json"}

    def _me_id(self):
        if self._me is None:
            code, data = _http("GET", f"{API}/users/@me", self._headers())
            self._me = data.get("id", "") if code == 200 else ""
        return self._me

    def fetch_updates(self) -> list[Message]:
        me = self._me_id()
        msgs = []
        for cid in self.channels:
            url = f"{API}/channels/{cid}/messages?limit=50"
            if cid in self._last_ids:
                url += f"&after={self._last_ids[cid]}"
            code, data = _http("GET", url, self._headers())
            if code != 200 or not isinstance(data, list) or not data:
                continue
            max_id = max(int(m.get("id", 0)) for m in data)
            if cid not in self._last_ids:
                self._last_ids[cid] = max_id
                continue  # 首轮只记水位，不回历史
            for m in reversed(data):  # API 返回新→旧，反转为旧→新
                mid = int(m.get("id", 0))
                if mid <= self._last_ids[cid]:
                    continue
                author = m.get("author", {})
                if author.get("bot") or (me and author.get("id") == me):
                    continue  # 跳过机器人消息
                content = m.get("content", "") or ""
                if not content.strip():
                    continue
                msgs.append(Message(
                    id=m.get("id", ""),
                    platform="discord",
                    conversation_id=cid,
                    conversation_title="",
                    author_id=str(author.get("id", "")),
                    author_name=author.get("global_name") or author.get("username", ""),
                    content=content,
                    created_at=m.get("timestamp", ""),
                    raw=m,
                ))
            self._last_ids[cid] = max_id
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # IM 类：消息即详情，无需二次拉取
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        if not content or not content.strip() or not conversation_id:
            return False
        for chunk in _chunks(content, TEXT_LIMIT):
            code, _ = _http(
                "POST",
                f"{API}/channels/{conversation_id}/messages",
                self._headers(),
                {"content": chunk},
            )
            if code not in (200, 201):
                return False
        return True
