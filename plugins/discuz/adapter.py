"""Discuz! 论坛适配器

通过 Discuz! 移动端 API（需开启手机客户端插件）：
- 主题列表：GET /api/mobile/?module=topiclist&fid={fid}
- 帖子详情：GET /api/mobile/?module=viewthread&tid={tid}
- 发表回复：POST /api/mobile/?module=sendreply&tid={tid}

注意：Discuz! 较老，没有官方 REST API，本适配器依赖
论坛后台已开启的"手机客户端"插件（api/mobile 接口）。
"""
import sys
import os
import json
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message, AuthError

REQUEST_TIMEOUT = 20


def _to_iso(ts):
    """unix 时间戳（秒）-> ISO 8601"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return str(ts)


class Adapter(PlatformAdapter):
    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = (config.get("url") or "").rstrip("/")
        # 密码优先从环境变量读取；登录成功后缓存 auth，后续请求不再明文传密码
        self.password = config.get("password") or os.environ.get("DISCUZ_PASSWORD", "")
        self.username = config.get("username") or os.environ.get("DISCUZ_USERNAME", "")
        self.fid = config.get("fid", "")
        self._auth = ""  # 登录后缓存的 auth 凭证

    def _login(self) -> bool:
        """M6 修复：真正的登录流程，调 mobile API 拿 auth 并缓存复用"""
        if self._auth:
            return True
        if not (self.base_url and self.username and self.password):
            return False
        data = self._api_get({
            "module": "login",
            "username": self.username,
            "password": self.password,
        })
        if not data:
            return False
        auth = data.get("auth", "")
        if auth:
            self._auth = auth
            return True
        return False

    def _api_get(self, params: dict):
        """调用 mobile API，返回解析后的 dict，失败返回 None"""
        try:
            # 登录成功后带上 auth 凭证
            if self._auth and params.get("module") != "login":
                params = {**params, "auth": self._auth}
            query = urllib.parse.urlencode({"version": 4, **params})
            req = urllib.request.Request(
                f"{self.base_url}/api/mobile/?{query}",
                headers={"User-Agent": "CommunityBot/1.0"},
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                data = json.load(r)
            # Discuz mobile API: Variables 为主体，Message 含错误信息
            if isinstance(data, dict) and data.get("Message", {}).get("messageval"):
                return None
            return data.get("Variables", data)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError("Discuz 认证失效（401/403）")
            return None
        except Exception:
            return None

    def _api_post(self, params: dict, form: dict):
        """POST 调用 mobile API，返回 True/False"""
        try:
            if self._auth and params.get("module") != "login":
                params = {**params, "auth": self._auth}
            query = urllib.parse.urlencode({"version": 4, **params})
            body = urllib.parse.urlencode(form).encode()
            req = urllib.request.Request(
                f"{self.base_url}/api/mobile/?{query}",
                data=body,
                headers={"User-Agent": "CommunityBot/1.0"},
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                data = json.load(r)
            msg = data.get("Message", {}) if isinstance(data, dict) else {}
            return not msg.get("messageval")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError("Discuz 认证失效（401/403）")
            return False
        except Exception:
            return False

    def fetch_updates(self) -> list[Message]:
        """轻量拉取主题列表（不进帖子详情）"""
        params = {"module": "topiclist"}
        if self.fid:
            params["fid"] = self.fid
        data = self._api_get(params)
        if not data:
            return []
        msgs = []
        for t in data.get("forum_threadlist", []) or []:
            msgs.append(Message(
                id=str(t.get("tid", "")),
                platform="discuz",
                conversation_id=str(t.get("tid", "")),
                conversation_title=t.get("subject", ""),
                author_id=str(t.get("authorid", "")),
                author_name=t.get("author", ""),
                content=t.get("subject", ""),
                # M7 修复：用最后回复时间（dblastpost）做水位，转 ISO
                created_at=_to_iso(t.get("dblastpost") or t.get("dbdateline", "")),
                raw=t,
            ))
        return msgs

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        """拉取主题下的帖子列表"""
        data = self._api_get({"module": "viewthread", "tid": conversation_id})
        if not data:
            return []
        thread = data.get("thread", {}) or {}
        msgs = []
        for p in data.get("postlist", []) or []:
            msgs.append(Message(
                id=str(p.get("pid", "")),
                platform="discuz",
                conversation_id=str(conversation_id),
                conversation_title=thread.get("subject", ""),
                author_id=str(p.get("authorid", "")),
                author_name=p.get("author", ""),
                content=p.get("message", ""),
                created_at=_to_iso(p.get("dbdateline", "")),
                raw=p,
            ))
        return msgs

    def refresh_auth(self) -> bool:
        """N11：调度器 401 时调用，重新登录拿 auth"""
        self._auth = ""
        return self._login()

    def reply(self, conversation_id: str, content: str) -> bool:
        """发表回复（先登录拿 auth，不再每次明文传密码）"""
        if not self._auth and not self._login():
            return False
        return self._api_post(
            {"module": "sendreply", "tid": conversation_id},
            {"message": content},
        )
