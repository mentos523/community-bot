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
from src.core.base import Message, PlatformAdapter, AuthError
from src.core.resilience import ResilienceMixin

TIMEOUT = 20
DETAIL_MAX_PAGES = 10  # R-S2：fetch_detail 分页上限，防超长讨论无限拉取


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


class Adapter(PlatformAdapter, ResilienceMixin):
    """NodeBB 平台适配器"""

    def __init__(self, config):
        self._init_resilience()
        super().__init__(config)
        self.base = _conf(config, "url").rstrip("/")
        self._token = _conf(config, "token", "NODEBB_TOKEN")
        self._uid = _conf(config, "uid")

    def refresh_auth(self) -> bool:
        """R-M4：用户 Token 类型无法自动刷新，返回 False 并记日志。"""
        import logging
        logging.getLogger(__name__).warning(
            "NodeBB 使用用户 Token 认证，无法自动刷新；请在后台重新生成后更新配置")
        return False

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
            if e.code in (401, 403):
                raise AuthError("NodeBB 认证失效（401/403）")
            return e.code, None
        except Exception:
            return 0, None

    def fetch_updates(self):
        """轻量拉取最近主题列表"""
        if self._in_backoff() or self._rate_limited():
            return []
        try:
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
            self._note_success()
            return out
        except Exception:
            self._note_failure()
            return []

    def fetch_detail(self, conversation_id):
        """拉取主题的全部帖子。

        R-S2：分页拉取全量（NodeBB 支持 ?page= 参数），最多 DETAIL_MAX_PAGES 页，
        保证长讨论的新回复可见；按 pid 去重后按时间正序返回。
        """
        out = []
        seen = set()
        title = ""
        try:
            for page in range(1, DETAIL_MAX_PAGES + 1):
                code, data = self._request(
                    "GET", "/api/topic/{}{}".format(
                        conversation_id, f"?page={page}" if page > 1 else ""))
                if code != 200 or not data:
                    break
                if not title:
                    title = data.get("title", "")
                posts = data.get("posts", []) or []
                if not posts:
                    break
                for p in posts:
                    pid = str(p.get("pid", ""))
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)
                    user = p.get("user") or {}
                    out.append(
                        Message(
                            id=pid,
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
                # 返回帖子数少于页大小说明已到末页
                if len(posts) < 20:
                    break
            out.sort(key=lambda m: int(m.id) if m.id.isdigit() else 0)
            return out
        except AuthError:
            raise
        except Exception:
            return out

    def reply(self, conversation_id, content):
        """在主题下发布回复"""
        payload = {"content": content}
        if self._uid:
            payload["_uid"] = self._uid
        code, data = self._request(
            "POST", "/api/v3/topics/{}".format(conversation_id), payload
        )
        # M11 修复：只看状态码，空 body 也算成功，避免重复发帖
        return code in (200, 201)
