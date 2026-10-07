"""NodeBB 论坛适配器

API:
  GET  /api/recent              轻量主题列表
  GET  /api/topic/{tid}         主题详情（含 posts）
  POST /api/v3/topics/{tid}     发布回复 {"content": ...}
认证：请求头 Authorization: Bearer {token}（后台生成的用户 Token）
"""
import html as _html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import Message, PlatformAdapter

TIMEOUT = 20


def _conf(config, key, env_var=""):
    v = config.get(key) or (os.environ.get(env_var) if env_var else "")
    return (v or "").strip()


def _strip_html(s):
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return _html.unescape(s).strip()


def _to_iso(ms):
    """毫秒时间戳 -> ISO 字符串"""
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return str(ms)


class Adapter(PlatformAdapter):
    """NodeBB 平台适配器"""

    def __init__(self, config):
        super().__init__(config)
        self.base = _conf(config, "url").rstrip("/")
        self._token = _conf(config, "token", "NODEBB_TOKEN")
        self._uid = _conf(config, "uid")

    def _request(self, method, path, payload=None):
        try:
            data = json.dumps(payload).encode() if payload is not None else None
            headers = {"Accept": "application/json"}
            if self._token:
                headers["Authorization"] = "Bearer {}".format(self._token)
            if data:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(
                self.base + path, data=data, headers=headers, method=method
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            return e.code, None
        except Exception:
            return 0, None

    def fetch_updates(self):
        """轻量拉取最近主题列表"""
        if not self.base:
            return []
        code, data = self._request("GET", "/api/recent")
        if code != 200 or not data:
            return []
        out = []
        for t in data.get("topics", []):
            user = t.get("user") or {}
            out.append(
                Message(
                    id=str(t.get("tid")),
                    platform="nodebb",
                    conversation_id=str(t.get("tid")),
                    conversation_title=t.get("title", ""),
                    author_name=user.get("username", ""),
                    created_at=_to_iso(t.get("lastposttime")),
                    raw={"postcount": t.get("postcount", 0)},
                )
            )
        return out

    def fetch_detail(self, conversation_id):
        """拉取主题的全部帖子"""
        code, data = self._request("GET", "/api/topic/{}".format(conversation_id))
        if code != 200 or not data:
            return []
        title = data.get("title", "")
        out = []
        for p in data.get("posts", []):
            user = p.get("user") or {}
            out.append(
                Message(
                    id=str(p.get("pid")),
                    platform="nodebb",
                    conversation_id=str(conversation_id),
                    conversation_title=title,
                    author_id=str(p.get("uid", "")),
                    author_name=user.get("username", ""),
                    content=_strip_html(p.get("content", "")),
                    created_at=_to_iso(p.get("timestamp")),
                    raw={},
                )
            )
        return out

    def reply(self, conversation_id, content):
        """在主题下发布回复"""
        payload = {"content": content}
        if self._uid:
            payload["_uid"] = self._uid
        code, data = self._request(
            "POST", "/api/v3/topics/{}".format(conversation_id), payload
        )
        return code in (200, 201) and data is not None
