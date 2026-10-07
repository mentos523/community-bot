"""Flarum 适配器（插件示例骨架，具体实现后续填充）"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message


class Adapter(PlatformAdapter):
    """Flarum 平台适配器"""

    def fetch_updates(self) -> list[Message]:
        # TODO: 调 /api/discussions 轻量列表，不进详情
        return []

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # TODO: 调 /api/discussions/{id}?include=posts
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        # TODO: POST /api/posts
        return False
