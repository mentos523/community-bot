# 平台适配规划

社区机器人通过平台适配器接入各种社区。每个适配器实现统一接口：拉取新内容、发布回复、获取元信息。

## 适配器接口

```python
class PlatformAdapter:
    def fetch_updates(self) -> list[Message]: ...  # 拉取新消息/帖子
    def reply(self, target_id, content) -> bool: ...  # 发布回复
    def get_info(self) -> dict: ...  # 平台元信息
```

## 论坛类（帖子流）

| 平台 | 优先级 | API 状况 | 备注 |
|------|--------|----------|------|
| Flarum | P0 | REST API 完善 | 已有实现经验，首个适配器 |
| Discourse | P0 | 官方 API 很完善 | 最主流的开源论坛 |
| phpBB | P1 | 有 API 扩展 | 全球最广泛使用的开源论坛 |
| NodeBB | P1 | REST + WebSocket | 实时性强，Node.js 生态 |
| Discuz! | P1 | 有 API | 中文社区主流 |
| MyBB | P2 | 插件 API | 轻量小社区 |
| XenForo | P2 | 官方 API（商业版） | 商业论坛 |
| Vanilla | P2 | API 完善 | 企业级 |

## 即时通讯类（消息流）

| 平台 | 优先级 | API 状况 | 备注 |
|------|--------|----------|------|
| Telegram | P0 | Bot API 极友好 | 60 秒建机器人，免费 |
| Discord | P0 | Bot + Webhook API | 社区/游戏主流，Slash 命令 |
| 飞书/Lark | P1 | 开放平台 API | 国内企业，需应用审核 |
| 钉钉 | P1 | Webhook 机器人 | 国内企业，群机器人简单 |
| 企业微信 | P1 | 应用 API | 国内企业 |
| Slack | P1 | Bolt 框架 | 海外企业 |
| WhatsApp | P2 | Cloud API | 需 Meta 商业账号 |
| Matrix | P2 | 开放联邦协议 | 开源自托管 |
| Mattermost | P2 | API 兼容 Slack | 开源企业聊天 |
| LINE | P3 | Bot API | 日韩台市场 |

## 优先级说明

- **P0**：首批实现，覆盖最主流场景
- **P1**：第二批，覆盖国内外企业/中文社区
- **P2**：按需实现
- **P3**：特定市场，有需求再做

## 实现顺序

1. Flarum（逻辑现成，直接移植）
2. Telegram（API 最简单，验证消息流架构）
3. Discourse（论坛类第二个，验证抽象）
4. Discord（消息流第二个）
5. 飞书 / 钉钉 / 企业微信（国内企业场景）
