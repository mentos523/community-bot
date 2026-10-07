"""Slack Bot 适配器（Web API 轮询实现）

配置：
  bot_token:   Bot User OAuth Token，xoxb- 开头（或环境变量 SLACK_BOT_TOKEN）
  channel_ids: 监听的频道 ID，英文逗号分隔（或环境变量 SLACK_CHANNEL_IDS）

说明：首轮只记录水位不回历史；跳过 bot_message 防回环。
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

API = "https://slack.com/api"
TEXT_LIMIT = 3500


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
        self.token = self._resolve("bot_token", "SLACK_BOT_TOKEN")
        chs = self.config.get("channel_ids") or os.environ.get("SLACK_CHANNEL_IDS", "")
        self.channels = [c.strip() for c in str(chs).split(",") if c.strip()]
        self._last_ts = {}   # channel_id -> str ts 水位
        self._me = None
        self._user_cache = {}

    def _resolve(self, key, env_var):
        return (self.config.get(key) or os.environ.get(env_var, "") or "").strip()

    def validate_config(self):
        if not self._resolve("bot_token", "SLACK_BOT_TOKEN"):
            raise ValueError("缺少 bot_token（配置或环境变量 SLACK_BOT_TOKEN）")
        chs = self.config.get("channel_ids") or os.environ.get("SLACK_CHANNEL_IDS", "")
        if not [c.strip() for c in str(chs).split(",") if c.strip()]:
            raise ValueError("缺少 channel_ids（配置或环境变量 SLACK_CHANNEL_IDS）")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _me_id(self):
        if self._me is None:
            _, data = _http("GET", f"{API}/auth.test", self._headers())
            self._me = data.get("user_id", "") if data.get("ok") else ""
        return self._me

    def _user_name(self, uid):
        if not uid:
            return ""
        if uid not in self._user_cache:
            _, data = _http("GET", f"{API}/users.info?user={uid}", self._headers())
            name = ""
            if data.get("ok"):
                u = data.get("user", {})
                name = u.get("real_name") or u.get("name", "")
            self._user_cache[uid] = name or uid
        return self._user_cache[uid]

    def _history(self, cid, oldest=None):
        """拉取频道历史，处理分页。返回消息列表（新→旧）。"""
        items = []
        cursor = None
        while True:
            params = {"channel": cid, "limit": 50}
            if oldest:
                params["oldest"] = oldest
            if cursor:
                params["cursor"] = cursor
            url = f"{API}/conversations.history?" + urllib.parse.urlencode(params)
            _, data = _http("GET", url, self._headers())
            if not data.get("ok"):
                break
            items.extend(data.get("messages", []))
            if data.get("has_more"):
                cursor = (data.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
            else:
                break
        return items

    def fetch_updates(self) -> list[Message]:
        me = self._me_id()
        msgs = []
        for cid in self.channels:
            items = self._history(cid, self._last_ts.get(cid))
            if not items:
                continue
            max_ts = max(float(m.get("ts", 0)) for m in items)
            if cid not in self._last_ts:
                self._last_ts[cid] = str(max_ts)
                continue  # 首轮只记水位，不回历史
            for m in sorted(items, key=lambda x: float(x.get("ts", 0))):
                ts = m.get("ts", "")
                if float(ts) <= float(self._last_ts[cid]):
                    continue
                if m.get("subtype") == "bot_message":
                    continue  # 跳过机器人消息
                uid = m.get("user", "")
                if me and uid == me:
                    continue
                text = m.get("text", "") or ""
                if not text.strip():
                    continue
                msgs.append(Message(
                    id=f"slack-{cid}-{ts}",
                    platform="slack",
                    conversation_id=cid,
                    conversation_title="",
                    author_id=uid,
                    author_name=self._user_name(uid),
                    content=text,
                    created_at=datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(),
                    raw=m,
                ))
            self._last_ts[cid] = str(max_ts)
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # IM 类：消息即详情，无需二次拉取
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        if not content or not content.strip() or not conversation_id:
            return False
        for chunk in _chunks(content, TEXT_LIMIT):
            _, data = _http(
                "POST", f"{API}/chat.postMessage", self._headers(),
                {"channel": conversation_id, "text": chunk},
            )
            if not data.get("ok"):
                return False
        return True
