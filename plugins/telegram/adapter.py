"""Telegram 适配器（插件骨架）"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from src.core.base import PlatformAdapter, Message


class Adapter(PlatformAdapter):
    def fetch_updates(self) -> list[Message]:
        # TODO: getUpdates 长轮询
        return []

    def fetch_detail(self, conversation_id: str) -> list[Message]:
        # TODO: 聊天类无需详情，直接返回空
        return []

    def reply(self, conversation_id: str, content: str) -> bool:
        # TODO: sendMessage
        return False
