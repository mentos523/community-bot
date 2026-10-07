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
from src.core.base import PlatformAdapter, Message, AuthError
from src.core.resilience import ResilienceMixin

AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
API = "https://open.feishu.cn/open-apis"
TEXT_LIMIT = 10000
MESSAGES_MAX_PAGES = 5  # R-S1：单群消息翻页上限，防无限循环


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
        # R-M3：401/403 直接抛 AuthError，由调度器统一处理
        if e.code in (401, 403):
            raise AuthError("飞书认证失效（401/403）")
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception:
        return 0, {}


def _chunks(text, limit):
    return [text[i:i + limit] for i in range(0, len(text), limit)]


def _safe_int(v, default=0):
    """畸形 create_time 防崩溃（M5）：失败返回 default，调用方跳过该条。"""
    try:
        return int(v)
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


class Adapter(PlatformAdapter, ResilienceMixin):
    def __init__(self, config):
        self._init_resilience()
        super().__init__(config)
        self.app_id = self._resolve("app_id", "FEISHU_APP_ID")
        self.app_secret = self._resolve("app_secret", "FEISHU_APP_SECRET")
        chs = self.config.get("chat_ids") or os.environ.get("FEISHU_CHAT_IDS", "")
        self.chats = [c.strip() for c in str(chs).split(",") if c.strip()]
        self._token = ""
        self._token_exp = 0
        # chat_id -> 毫秒时间戳水位。重启后从文件恢复（M2）。
        self._last_time = {k: _safe_int(v) for k, v in
                           _load_json(_data_file("feishu_last_time.json"), {}).items()}
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
        # F-M1/H-M3：退避期内直接返回
        if self._in_backoff() or self._rate_limited():
            return []
        try:
            if not self._tenant_token():
                self._note_failure()
                return []
            bot_id = self._bot_open_id()
        except AuthError:
            raise
        except Exception:
            self._note_failure()
            return []
        msgs = []
        try:
            for cid in self.chats:
                # R-S1：分页拉取全量消息（page_token 翻页，上限 MESSAGES_MAX_PAGES 页），
                # 避免只取第一页导致水位推进后中间页消息永久丢失
                items = []
                page_token = ""
                for _ in range(MESSAGES_MAX_PAGES):
                    params = {
                        "container_id_type": "chat",
                        "container_id": cid,
                        "sort_type": "ByCreateTimeDesc",
                        "page_size": 20,
                    }
                    if page_token:
                        params["page_token"] = page_token
                    url = f"{API}/im/v1/messages?" + urllib.parse.urlencode(params)
                    code, data = _http("GET", url, self._headers())
                    if code == 429:
                        # H-M3：飞书 429 限流，标记非阻塞退避，本轮跳过该群
                        self._mark_rate_limited()
                        break
                    if data.get("code") != 0:
                        break
                    d = data.get("data") or {}
                    items.extend(d.get("items", []))
                    if not d.get("has_more"):
                        break
                    page_token = d.get("page_token", "")
                    if not page_token:
                        break
                if not items:
                    continue
                # 时间解析防崩溃（M5）：畸形 create_time 视为 0，后续被水位过滤跳过
                max_t = max((_safe_int(i.get("create_time")) for i in items), default=0)
                if cid not in self._last_time:
                    self._last_time[cid] = max_t
                    _save_json(_data_file("feishu_last_time.json"), self._last_time)
                    continue  # 首轮只记水位，不回历史
                water = self._last_time.get(cid, 0)
                for i in sorted(items, key=lambda x: _safe_int(x.get("create_time"))):
                    ct = _safe_int(i.get("create_time"))
                    if ct <= 0 or ct <= water:
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
                        created_at=datetime.fromtimestamp(ct / 1000, tz=timezone.utc).isoformat() if ct else "",
                        raw=i,
                    ))
                self._last_time[cid] = max_t
            _save_json(_data_file("feishu_last_time.json"), self._last_time)
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
