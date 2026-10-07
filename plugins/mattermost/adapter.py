"""Mattermost 适配器（API 兼容 Slack 风格）"""
import sys, os, json, time, urllib.request, urllib.parse, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message, AuthError
from src.core.resilience import ResilienceMixin

TIMEOUT = 20
POSTS_MAX_PAGES = 5  # R-S1：单频道帖子翻页上限，防无限循环
USER_CACHE_MAX = 500  # H-M1：用户缓存上限，超了清空重建
CHANNELS_CACHE_TTL = 600  # H-M5：频道列表缓存 10 分钟，过期才重拉


class _RateLimited(Exception):
    """H-M3：内部限流信号，_api 遇到 429 时抛出，调用方转为非阻塞退避标记。"""
    pass


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


class Adapter(PlatformAdapter, ResilienceMixin):
    def __init__(self, config: dict):
        self._init_resilience()
        # channel_id -> 最新已读 post 的 create_at（毫秒）。重启后从文件恢复（S6/M2）。
        self._last_seen: dict[str, int] = _load_json(_data_file("mattermost_last_seen.json"), {})
        self._me_id: str = ""
        self._user_cache: dict[str, str] = {}
        self._channels_cache: list[dict] = []
        self._channels_cache_at: float = 0.0
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
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # R-M3：401/403 抛 AuthError，由调度器统一处理
            if e.code in (401, 403):
                raise AuthError("Mattermost 认证失效（401/403）")
            # H-M3：429 限流抛给调用方标记非阻塞退避
            if e.code == 429:
                raise _RateLimited()
            raise

    def _channels(self) -> list[dict]:
        """H-M5：频道列表缓存到实例变量，10 分钟刷新一次而非每轮重拉。"""
        now = time.time()
        if self._channels_cache and now - self._channels_cache_at < CHANNELS_CACHE_TTL:
            return self._channels_cache
        only = self._cfg("channel_id", "MATTERMOST_CHANNEL_ID")
        try:
            if only:
                channels = [self._api("GET", f"/api/v4/channels/{only}")]
            else:
                channels = self._api("GET", "/api/v4/users/me/channels") or []
        except _RateLimited:
            self._mark_rate_limited()
            return self._channels_cache  # 限流时用旧缓存
        except Exception:
            return self._channels_cache
        self._channels_cache = channels
        self._channels_cache_at = now
        return channels

    def _my_id(self) -> str:
        """自身用户 ID，缓存到实例变量避免每轮都调 /users/me（N5）。"""
        if not self._me_id:
            me = self._api("GET", "/api/v4/users/me")
            self._me_id = me.get("id", "")
        return self._me_id

    def _user_name(self, uid: str) -> str:
        """用户 ID -> 用户名（带缓存，N6）。

        H-M1：缓存上限 500，超了清空重建，防长期运行内存膨胀。
        """
        if not uid:
            return ""
        if len(self._user_cache) >= USER_CACHE_MAX:
            self._user_cache.clear()
        if uid not in self._user_cache:
            try:
                u = self._api("GET", f"/api/v4/users/{uid}")
                self._user_cache[uid] = u.get("username") or u.get("nickname") or uid
            except _RateLimited:
                self._mark_rate_limited()
                return uid
            except Exception:
                self._user_cache[uid] = uid
        return self._user_cache[uid]

    def _channel_posts(self, ch_id: str) -> tuple[dict, list]:
        """R-S1：分页拉取频道帖子（page 翻页，上限 POSTS_MAX_PAGES 页）。

        返回 (posts_dict, order_list)。429 时标记非阻塞退避并返回已取到的数据。
        """
        posts: dict = {}
        order: list = []
        seen_ids: set = set()
        for page in range(POSTS_MAX_PAGES):
            try:
                data = self._api("GET", f"/api/v4/channels/{ch_id}/posts",
                                 params={"per_page": 60, "page": page})
            except _RateLimited:
                self._mark_rate_limited()
                break
            except Exception:
                break
            p = data.get("posts") or {}
            o = data.get("order") or []
            if not o:
                break
            posts.update(p)
            # order 去重保序合并
            for x in o:
                if x not in seen_ids:
                    seen_ids.add(x)
                    order.append(x)
            if len(o) < 60:
                break  # 未取满一页，说明已到末页
        return posts, order

    def fetch_updates(self) -> list[Message]:
        """轮询各频道的新帖子。

        首轮只记水位不回历史（S6，防重启后重复回复 20 条历史）；
        水位同时持久化到 data/mattermost_last_seen.json。
        """
        # F-M1/H-M3：退避期内直接返回
        if self._in_backoff() or self._rate_limited():
            return []
        try:
            me_id = self._my_id()
        except AuthError:
            raise
        except Exception:
            self._note_failure()
            return []  # 拿不到自身 ID 时不处理，避免回环误判
        msgs = []
        try:
            for ch in self._channels():
                ch_id = ch.get("id", "")
                if not ch_id:
                    continue
                posts, order = self._channel_posts(ch_id)
                if not posts:
                    continue
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
            self._note_success()
            return msgs
        except AuthError:
            raise
        except Exception:
            self._note_failure()
            return []

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
