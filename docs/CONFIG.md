# 配置说明

社区机器人的所有配置集中在 `config/config.yaml`（Web 界面修改会同步写回此文件）。

## 快速开始

```bash
cp config/config.example.yaml config/config.yaml
# 编辑 config.yaml，填入你的社区和模型信息
python -m src.main
```

## 完整配置参考

```yaml
# ===== 全局 =====
default_model: "qwen2.5:7b"   # 默认模型（须在 models 中定义）

# ===== 社区（可多个）=====
communities:
  - name: "学习交流"           # 自定义名称
    plugin: flarum            # 插件名（plugins/ 下的目录名）
    enabled: true
    # --- 插件相关配置（见各插件 manifest.yaml）---
    url: "https://example.com"
    tag_filter: "communicate"
    # token/password 走环境变量，此处不写

  - name: "技术群"
    plugin: telegram
    enabled: true
    # bot_token 从环境变量 TELEGRAM_BOT_TOKEN 读取

# ===== 模型（可多个）=====
models:
  - name: "qwen2.5:7b"
    type: local               # local | remote | openai | gemini | anthropic
    endpoint: "http://localhost:11434"
    options:                  # 透传给模型的选项
      temperature: 0.7
      num_predict: 1500

  - name: "gpt-4o-mini"
    type: openai
    # api_key 从环境变量 OPENAI_API_KEY 读取

  - name: "gemini-2.0-flash"
    type: gemini
    # api_key 从环境变量 GEMINI_API_KEY 读取

# ===== 回复规则 =====
rules:
  # 哪些帖子值得回复（四条同时满足才回）
  min_question_length: 5       # 问题最短字符数
  skip_keywords: ["测试", "test"]  # 含这些词的不回

  # 频率控制
  max_replies_per_discussion_per_day: 2
  max_replies_per_day: 20

  # 特定用户（每次都回）
  always_reply_users: ["robbin"]

  # 回复风格
  reply_style:
    language: zh              # 回复语言
    max_length: 500           # 最长字符数
    no_trailing: true         # 不加"随时问我"类结尾

# ===== 梯度轮询 =====
tiers:
  hot_interval: 60            # 1小时内有活动：每60秒
  warm_interval: 1800         # 1-24小时：每30分钟
  cold_interval: 604800       # 24小时-3天：每周
  # archived（超3天）：不再进详情，但列表仍监控新活动

# ===== Web 管理 =====
web:
  host: "0.0.0.0"
  port: 52323
  # 管理密码（建议用环境变量 WEB_PASSWORD）
```

## 环境变量

密钥类配置**必须**走环境变量，不要写进配置文件：

| 变量 | 用途 |
|------|------|
| `FLARUM_TOKEN` | Flarum API Token |
| `FLARUM_PASSWORD` | Flarum 账号密码（自动重登） |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `DISCORD_BOT_TOKEN` | Discord Bot Token |
| `OPENAI_API_KEY` | OpenAI API Key |
| `GEMINI_API_KEY` | Google Gemini API Key |
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `WEB_PASSWORD` | Web 管理密码 |

各插件的 `env_var` 定义见 `plugins/<插件名>/manifest.yaml`。

## 配置优先级

同一配置项：**Web 界面修改 > config.yaml > 环境变量 > 插件默认值**

## 热重载

Web 界面修改配置后即时生效，无需重启。如直接编辑 `config.yaml`，
守护进程会在下次轮询时自动重载（最多 60 秒延迟）。
