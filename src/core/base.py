"""社区机器人插件基类。所有平台适配器继承此类。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Message:
    """统一消息格式"""
    id: str
    platform: str
    conversation_id: str      # 讨论/群组 ID
    conversation_title: str = ""
    author_id: str = ""
    author_name: str = ""
    content: str = ""
    created_at: str = ""      # ISO 时间
    raw: dict = field(default_factory=dict)


class AuthError(Exception):
    """认证失效（token 过期等）。调度器捕获后会调用 refresh_auth() 重试一次。"""
    pass


class PlatformAdapter(ABC):
    """平台适配器基类"""

    def __init__(self, config: dict):
        self.config = config
        self.validate_config()

    def validate_config(self):
        """校验必填配置，子类可重写"""
        pass

    @abstractmethod
    def fetch_updates(self) -> list[Message]:
        """拉取新消息/帖子（轻量，不产生浏览量）"""
        ...

    @abstractmethod
    def fetch_detail(self, conversation_id: str) -> list[Message]:
        """拉取指定会话的详情（含完整内容）"""
        ...

    @abstractmethod
    def reply(self, conversation_id: str, content: str) -> bool:
        """发布回复"""
        ...

    def get_info(self) -> dict:
        """平台元信息"""
        return {"platform": self.__class__.__name__}

    def refresh_auth(self) -> bool:
        """刷新认证（如 token 过期时全新登录）。返回是否成功，默认不支持。"""
        return False
