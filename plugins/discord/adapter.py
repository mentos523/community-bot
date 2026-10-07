"""Discord Bot 适配器（REST 轮询实现）

配置：
  bot_token:   开发者门户申请的 Bot Token（或环境变量 DISCORD_BOT_TOKEN）
  channel_ids: 监听的频道 ID，英文逗号分隔（或环境变量 DISCORD_CHANNEL_IDS）

说明：首轮只记录水位不回历史；跳过机器人消息防回环。
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message, AuthError
from src.core.resilience import ResilienceMixin

API = "https://discord.com/api/v10"
TEXT_LIMIT = 2000  # Discord 单条消息上限


def _retry_after(headers) -> int:
    """从响应头读 Retry-After（秒），读不到返回 0。"""
    try:
        ra = headers.get("Retry-After") if headers else None
        return max(0, int(float(ra))) if ra else 0
    except Exception:
        return 0


def _http(method, url, headers=None, body=None, timeout=30):
    """返回 (status_code, dict, retry_after_seconds)。

    R-M3：401/403 直接抛 AuthError，由调度器统一处理（不再静默返回）。
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8")), 0
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError("Discord 认证失效（401/403）")
        ra = _retry_after(e.headers)
        try:
            return e.code, json.loads(e.read().decode("utf-8")), ra
        except Exception:
            return e.code, {}, ra
    except Exception:
        return 0, {}, 0


def _chunks(text, limit):
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _data_file(name: str) -> str:
    """data 目录下持久化文件路径（相对项目根目录 data/）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", name)


def _load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, obj) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        os.replace(tmp, path)
    except Exception:
        pass


# H-S2：删除阻塞式 _backoff（time.sleep 会卡住调度线程）。
# 改用 ResilienceMixin 的非阻塞退避标记：_mark_rate_limited() 只记录到期时间，
# fetch_updates/reply 开头检查 _rate_limited()，退避期内直接跳过。


class Adapter(PlatformAdapter, ResilienceMixin):
    def __init__(self, config):
        self._init_resilience()
        super().__init__(config)
        self.token = self._resolve("bot_token", "DISCORD_BOT_TOKEN")
        chs = self.config.get("channel_ids") or os.environ.get("DISCORD_CHANNEL_IDS", "")
        self.channels = [c.strip() for c in str(chs).split(",") if c.strip()]
        # channel_id -> 雪花 ID 水位。重启后从文件恢复（M2）。
        # 注意 JSON 的 key 只能是 str，读出后转回 int。
        self._last_ids = {k: int(v) for k, v in
                          _load_json(_data_file("discord_last_ids.json"), {}).items()}
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
            code, data, _ = _http("GET", f"{API}/users/@me", self._headers())
            self._me = data.get("id", "") if code == 200 else ""
        return self._me

    def fetch_updates(self) -> list[Message]:
        # H-S2/F-M1：限流退避或失败退避期内直接跳过，不阻塞调度线程
        if self._in_backoff() or self._rate_limited():
            return []
        try:
            me = self._me_id()
        except AuthError:
            raise
        except Exception:
            self._note_failure()
            return []
        msgs = []
        try:
            for cid in self.channels:
                url = f"{API}/channels/{cid}/messages?limit=50"
                if cid in self._last_ids:
                    url += f"&after={self._last_ids[cid]}"
                code, data, retry_after = _http("GET", url, self._headers())
                if code == 429:
                    # H-S2：非阻塞退避——标记到期时间，本轮跳过该频道
                    self._mark_rate_limited(retry_after)
                    continue
                if code != 200 or not isinstance(data, list) or not data:
                    continue
                max_id = max(int(m.get("id", 0)) for m in data)
                if cid not in self._last_ids:
                    self._last_ids[cid] = max_id
                    _save_json(_data_file("discord_last_ids.json"),
                               {k: str(v) for k, v in self._last_ids.items()})
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
            _save_json(_data_file("discord_last_ids.json"),
                       {k: str(v) for k, v in self._last_ids.items()})
            self._note_success()
            return msgs
        except AuthError:
            raise
        except Exception:
            self._note_failure()
            return []

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # IM 类：消息即详情，无需二次拉取
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        # H-S2：限流退避期内直接返回 False，不阻塞等待
        if self._rate_limited():
            return False
        if not content or not content.strip() or not conversation_id:
            return False
        for chunk in _chunks(content, TEXT_LIMIT):
            try:
                code, _, retry_after = _http(
                    "POST",
                    f"{API}/channels/{conversation_id}/messages",
                    self._headers(),
                    {"content": chunk},
                )
            except AuthError:
                raise
            except Exception:
                return False
            if code == 429:
                # H-S2：非阻塞退避——标记到期时间，本轮不再重试，调度器下轮再试
                self._mark_rate_limited(retry_after)
                return False
            if code not in (200, 201):
                return False
        return True
