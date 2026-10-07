# 配置说明

社区机器人的所有配置集中在 `config/config.yaml`（Web 界面修改会同步写回此文件）。
完整注释版见 `config/config.example.yaml`，本文件是精简参考。

## 快速开始

```bash
cp config/config.example.yaml config/config.yaml
# 编辑 config.yaml，填入你的社区和模型信息
python -m src.main
```

## 完整配置参考

```yaml
# ===== 社区（可多个，每个独立选插件和模型）=====
communities:
  - name: "学习交流"
    plugin: flarum            # 插件名 = plugins/ 下的目录名
    enabled: true
    model: "qwen2.5:7b"       # 本社区用的模型（不填则用 default_model）
    url: "https://example.com"
    # 认证走环境变量：FLARUM_TOKEN（推荐）或 FLARUM_USERNAME / FLARUM_PASSWORD
    #   401/403 时用账号密码自动重新登录换 token
    tag_filter: "communicate" # 只处理该标签，留空则全板块
    # bot_name: "助教"        # 本社区的机器人自称（可选）

# ===== 模型（本地/远端/商业可混用）=====
models:
  # 本地 Ollama：POST {endpoint}/api/generate
  #   协议头：Content-Type: application/json（无需认证）
  - name: "qwen2.5:7b"
    type: local               # local | remote | openai | gemini | anthropic
    endpoint: "http://localhost:11434"
    options:
      temperature: 0.7
      num_predict: 1500

  # 远端自建（OpenAI 兼容）：POST {endpoint}/v1/chat/completions
  #   协议头：Content-Type: application/json
  #          Authorization: Bearer {api_key}
  # - name: "my-remote-qwen"
  #   type: remote
  #   endpoint: "https://llm.example.com"
  #   api_key_env: "MY_LLM_API_KEY"
  #   options:
  #     temperature: 0.7
  #     max_tokens: 1500

  # OpenAI 官方：POST https://api.openai.com/v1/chat/completions
  #   协议头：Authorization: Bearer {OPENAI_API_KEY}
  # - name: "gpt-4o-mini"
  #   type: openai
  #   # api_key 走环境变量 OPENAI_API_KEY

  # Google Gemini：POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
  #   协议头：x-goog-api-key: {GEMINI_API_KEY}（专用头，不是 Bearer）
  # - name: "gemini-2.0-flash"
  #   type: gemini
  #   # api_key 走环境变量 GEMINI_API_KEY

  # Anthropic：POST https://api.anthropic.com/v1/messages
  #   协议头：x-api-key: {ANTHROPIC_API_KEY}
  #          anthropic-version: 2023-06-01（必填）
  # - name: "claude-4-sonnet"
  #   type: anthropic
  #   # api_key 走环境变量 ANTHROPIC_API_KEY

default_model: "qwen2.5:7b"   # 社区没单独指定 model 时用这个

# ===== 提示词（发给模型的指令模板）=====
prompts:
  bot_name: "助教"
  # {bot_name} 和 {content} 会被自动替换
  template: |
    你是{bot_name}，社区里的助教。
    ……（见 config.example.yaml 完整版）
  short_template: "你是{bot_name}，社区助教。用12岁孩子能懂的中文简短完整地回答……"

# ===== 回复规则 =====
rules:
  min_question_length: 5       # 问题最短字符数
  skip_keywords: ["测试", "test", "签到"]
  always_reply_users: ["robbin"]  # 名单用户每次都回，不受门槛限制
  max_replies_per_discussion_per_day: 2
  max_replies_per_day: 20
  reply_style:
    max_length: 800
    no_trailing: true         # 不加"随时问我"类结尾

# ===== 梯度轮询 =====
tiers:
  hot_interval: 60            # 1小时内有活动：每60秒
  warm_interval: 1800         # 1-24小时：每30分钟
  cold_interval: 604800       # 24小时-3天：每周
  # archived（超3天）：不再进详情，但轻量列表仍监控新活动

# ===== Web 管理 =====
web:
  host: "127.0.0.1"           # 只监听本机；局域网访问改为 "0.0.0.0"
  port: 52323
  # 管理密码走环境变量 WEB_PASSWORD（Web 界面登录用，必须设置，否则 Web 锁定）
  # 会话密钥走环境变量 WEB_SECRET_KEY（不设则每次启动随机生成，重启后需重新登录）
```

## 环境变量

密钥类配置**必须**走环境变量，不要写进配置文件：

| 变量 | 用途 |
|------|------|
| `FLARUM_TOKEN` / `FLARUM_USERNAME` / `FLARUM_PASSWORD` | Flarum 认证 |
| `DISCOURSE_API_KEY` / `DISCOURSE_API_USERNAME` | Discourse 认证 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `DISCORD_BOT_TOKEN` | Discord Bot Token |
| `SLACK_BOT_TOKEN` | Slack Bot Token |
| `OPENAI_API_KEY` | OpenAI API Key |
| `GEMINI_API_KEY` | Google Gemini API Key |
| `ANTHROPIC_API_KEY` | Anthropic API Key |
| `WEB_PASSWORD` | Web 管理密码 |

各插件的完整 `env_var` 定义见 `plugins/<插件名>/manifest.yaml`。

## 配置优先级

同一配置项：**Web 界面修改 > config.yaml > 环境变量 > 插件默认值**

## 热重载

Web 界面修改配置后即时生效，无需重启。如直接编辑 `config.yaml`，
守护进程会在下次轮询时自动重载（最多 60 秒延迟）。
