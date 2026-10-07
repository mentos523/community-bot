"""Slack Bot 适配器（Web API 轮询实现）

配置：
  bot_token:   Bot User OAuth Token，xoxb- 开头（或环境变量 SLACK_BOT_TOKEN）
  channel_ids: 监听的频道 ID，英文逗号分隔（或环境变量 SLACK_CHANNEL_IDS）

说明：首轮只记录水位不回历史；跳过 bot_message 防回环。
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
from src.core.base import PlatformAdapter, Message, AuthError
from src.core.resilience import ResilienceMixin

API = "https://slack.com/api"
TEXT_LIMIT = 3500
HISTORY_MAX_PAGES = 5  # H-M4：_history 翻页上限，防无限循环
USER_CACHE_MAX = 500  # H-M1：用户缓存上限，超了清空重建


def _retry_after(headers) -> int:
    """从响应头读 Retry-After（秒），读不到返回 0。"""
    try:
        ra = headers.get("Retry-After") if headers else None
        return max(0, int(float(ra))) if ra else 0
    except Exception:
        return 0


def _http(method, url, headers=None, body=None, timeout=30):
    """返回 (status_code, dict, retry_after_seconds)。

    R-M3：401/403 直接抛 AuthError，由调度器统一处理。
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8")), 0
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError("Slack 认证失效（401/403）")
        ra = _retry_after(e.headers)
        try:
            return e.code, json.loads(e.read().decode("utf-8")), ra
        except Exception:
            return e.code, {}, ra
    except Exception:
        return 0, {}, 0


def _chunks(text, limit):
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _safe_float(v, default=0.0):
    """畸形 ts 防崩溃（M5）：失败返回 default，调用方跳过该条。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


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
# 改用 ResilienceMixin 的非阻塞退避标记。


class Adapter(PlatformAdapter, ResilienceMixin):
    def __init__(self, config):
        self._init_resilience()
        super().__init__(config)
        self.token = self._resolve("bot_token", "SLACK_BOT_TOKEN")
        chs = self.config.get("channel_ids") or os.environ.get("SLACK_CHANNEL_IDS", "")
        self.channels = [c.strip() for c in str(chs).split(",") if c.strip()]
        # channel_id -> ts 水位。重启后从文件恢复（M2）。
        self._last_ts = _load_json(_data_file("slack_last_ts.json"), {})
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
            _, data, _ = _http("GET", f"{API}/auth.test", self._headers())
            self._me = data.get("user_id", "") if data.get("ok") else ""
        return self._me

    def _user_name(self, uid):
        if not uid:
            return ""
        # H-M1：缓存上限 500，超了清空重建，防长期运行内存膨胀
        if len(self._user_cache) >= USER_CACHE_MAX:
            self._user_cache.clear()
        if uid not in self._user_cache:
            try:
                _, data, _ = _http("GET", f"{API}/users.info?user={uid}", self._headers())
            except AuthError:
                raise
            except Exception:
                data = {}
            name = ""
            if data.get("ok"):
                u = data.get("user", {})
                name = u.get("real_name") or u.get("name", "")
            self._user_cache[uid] = name or uid
        return self._user_cache[uid]

    def _history(self, cid, oldest=None):
        """拉取频道历史，处理分页。返回消息列表（新→旧）。

        H-M4：翻页上限 HISTORY_MAX_PAGES，防 has_more 异常导致的无限循环。
        H-S2：429 时标记非阻塞退避并中断本轮，不再 sleep 等待。
        """
        items = []
        cursor = None
        for _ in range(HISTORY_MAX_PAGES):
            params = {"channel": cid, "limit": 50}
            if oldest:
                params["oldest"] = oldest
            if cursor:
                params["cursor"] = cursor
            url = f"{API}/conversations.history?" + urllib.parse.urlencode(params)
            try:
                code, data, retry_after = _http("GET", url, self._headers())
            except AuthError:
                raise
            except Exception:
                break
            if code == 429:
                self._mark_rate_limited(retry_after)
                break
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
        # H-S2/F-M1：限流退避或失败退避期内直接跳过
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
                items = self._history(cid, self._last_ts.get(cid))
                if not items:
                    continue
                # 时间解析全部防崩溃（M5）：畸形 ts 跳过该条，不崩整个拉取
                max_ts = max((_safe_float(m.get("ts")) for m in items), default=0.0)
                if cid not in self._last_ts:
                    self._last_ts[cid] = str(max_ts)
                    _save_json(_data_file("slack_last_ts.json"), self._last_ts)
                    continue  # 首轮只记水位，不回历史
                water = _safe_float(self._last_ts[cid])
                for m in sorted(items, key=lambda x: _safe_float(x.get("ts"))):
                    ts = m.get("ts", "")
                    ts_f = _safe_float(ts)
                    if ts_f <= 0 or ts_f <= water:
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
                        created_at=datetime.fromtimestamp(ts_f, tz=timezone.utc).isoformat(),
                        raw=m,
                    ))
                self._last_ts[cid] = str(max_ts)
            _save_json(_data_file("slack_last_ts.json"), self._last_ts)
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
                _, data, retry_after = _http(
                    "POST", f"{API}/chat.postMessage", self._headers(),
                    {"channel": conversation_id, "text": chunk},
                )
            except AuthError:
                raise
            except Exception:
                return False
            if not data.get("ok") and str(data.get("error", "")) == "ratelimited":
                # H-S2：非阻塞退避——标记到期时间，本轮不再重试
                self._mark_rate_limited(retry_after)
                return False
            if not data.get("ok"):
                return False
        return True
