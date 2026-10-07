"""Mattermost 适配器（API 兼容 Slack 风格）"""
import sys, os, json, time, urllib.request, urllib.parse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

TIMEOUT = 20


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


def _safe_int(v, default=0):
    """畸形时间字段防崩溃（M5）：失败返回 default，调用方跳过该条。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class Adapter(PlatformAdapter):
    def __init__(self, config: dict):
        # channel_id -> 最新已读 post 的 create_at（毫秒）。重启后从文件恢复（S6/M2）。
        self._last_seen: dict[str, int] = _load_json(_data_file("mattermost_last_seen.json"), {})
        self._me_id: str = ""
        self._user_cache: dict[str, str] = {}
        super().__init__(config)

    def _cfg(self, key, env_var, default=""):
        return self.config.get(key) or os.environ.get(env_var, default)

    def _api(self, method: str, path: str, payload: dict | None = None, params: dict | None = None):
        base = self._cfg("server_url", "MATTERMOST_SERVER_URL").rstrip("/")
        token = self._cfg("token", "MATTERMOST_TOKEN")
        url = base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)

    def _channels(self) -> list[dict]:
        only = self._cfg("channel_id", "MATTERMOST_CHANNEL_ID")
        if only:
            try:
                return [self._api("GET", f"/api/v4/channels/{only}")]
            except Exception:
                return []
        try:
            return self._api("GET", "/api/v4/users/me/channels") or []
        except Exception:
            return []

    def _my_id(self) -> str:
        """自身用户 ID，缓存到实例变量避免每轮都调 /users/me（N5）。"""
        if not self._me_id:
            me = self._api("GET", "/api/v4/users/me")
            self._me_id = me.get("id", "")
        return self._me_id

    def _user_name(self, uid: str) -> str:
        """用户 ID -> 用户名（带缓存，N6）。"""
        if not uid:
            return ""
        if uid not in self._user_cache:
            try:
                u = self._api("GET", f"/api/v4/users/{uid}")
                self._user_cache[uid] = u.get("username") or u.get("nickname") or uid
            except Exception:
                self._user_cache[uid] = uid
        return self._user_cache[uid]

    def fetch_updates(self) -> list[Message]:
        """轮询各频道的新帖子。

        首轮只记水位不回历史（S6，防重启后重复回复 20 条历史）；
        水位同时持久化到 data/mattermost_last_seen.json。
        """
        try:
            me_id = self._my_id()
        except Exception:
            return []  # 拿不到自身 ID 时不处理，避免回环误判
        msgs = []
        for ch in self._channels():
            ch_id = ch.get("id", "")
            if not ch_id:
                continue
            try:
                data = self._api("GET", f"/api/v4/channels/{ch_id}/posts",
                                 params={"per_page": 20, "page": 0})
            except Exception:
                continue
            posts = (data.get("posts") or {})
            order = data.get("order") or []
            first_round = ch_id not in self._last_seen
            last_seen = self._last_seen.get(ch_id, 0)
            newest = last_seen
            for pid in order:
                p = posts.get(pid) or {}
                if p.get("user_id") == me_id:
                    continue  # 跳过自己发的
                if p.get("type", "") != "":
                    continue  # 跳过系统消息
                created = _safe_int(p.get("create_at"), 0)
                if created <= 0:
                    continue  # 畸形时间跳过该条，不崩整个拉取（M5）
                newest = max(newest, created)
                if first_round:
                    continue  # 首轮只记水位，不回历史（S6）
                if created > last_seen:
                    msgs.append(Message(
                        id=str(p.get("id", "")),
                        platform="mattermost",
                        conversation_id=ch_id,
                        conversation_title=str(ch.get("display_name") or ch.get("name", "")),
                        author_id=str(p.get("user_id", "")),
                        author_name=self._user_name(str(p.get("user_id", ""))),
                        content=str(p.get("message", "")),
                        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created / 1000)),
                        raw=p,
                    ))
            self._last_seen[ch_id] = newest
        _save_json(_data_file("mattermost_last_seen.json"), self._last_seen)
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # 聊天类无需详情
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        """conversation_id 为频道 ID。POST /api/v4/posts。"""
        try:
            data = self._api("POST", "/api/v4/posts", {
                "channel_id": conversation_id,
                "message": content,
            })
            return bool(data.get("id"))
        except Exception:
            return False
