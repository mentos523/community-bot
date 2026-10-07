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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message

REQUEST_TIMEOUT = 20


class Adapter(PlatformAdapter):
    def __init__(self, config: dict):
        super().__init__(config)
        self.base_url = (config.get("url") or "").rstrip("/")
        # 密码优先从环境变量读取
        self.password = config.get("password") or os.environ.get("DISCUZ_PASSWORD", "")
        self.username = config.get("username", "")
        self.fid = config.get("fid", "")

    def _api_get(self, params: dict):
        """调用 mobile API，返回解析后的 dict，失败返回 None"""
        try:
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
        except Exception:
            return None

    def _api_post(self, params: dict, form: dict):
        """POST 调用 mobile API，返回 True/False"""
        try:
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
                created_at=str(t.get("dbdateline", "")),
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
                created_at=str(p.get("dbdateline", "")),
                raw=p,
            ))
        return msgs

    def reply(self, conversation_id: str, content: str) -> bool:
        """发表回复"""
        form = {"message": content}
        if self.username:
            form["username"] = self.username
        if self.password:
            form["password"] = self.password
        return self._api_post({"module": "sendreply", "tid": conversation_id}, form)
