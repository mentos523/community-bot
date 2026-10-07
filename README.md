# 社区机器人 (Community Bot)

一个可配置的多平台社区助教机器人。支持多种社区平台、多种模型后端，带 Web 可视化管理。

## 功能

- **多平台适配**：论坛类（Flarum、Discourse、phpBB、NodeBB、Discuz!）+ 即时通讯类（Telegram、Discord、飞书、钉钉、企业微信、Slack），详见 [docs/ADAPTERS.md](docs/ADAPTERS.md)
- **自由模型层**：本地 Ollama、远端自建、商业 API（OpenAI、Google Gemini、Anthropic）
- **Web 管理**：仪表盘、规则配置、模型管理、日志查看
- **梯度轮询**：按帖子活跃度分级检查（Hot/Warm/Cold/Archived），不刷浏览量
- **完整性校验**：回复生成后自检，思维链泄露、截断自动丢弃

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 复制配置
cp config/config.example.yaml config/config.yaml

# 3. 编辑配置（社区、模型、规则）
vim config/config.yaml

# 4. 启动守护进程
python -m src.main

# 5. 启动 Web 管理（另开一个终端）
export WEB_PASSWORD="你的管理密码"   # 必设，否则 Web 锁定无法登录
python -m src.web.app
```

Web 管理界面：`http://localhost:52323`（默认只监听本机）

详细配置说明见 [docs/CONFIG.md](docs/CONFIG.md)，
本地模型配置见 [docs/LOCAL_MODELS.md](docs/LOCAL_MODELS.md)。

## 配置说明

`config/config.yaml` 是唯一配置源，Web 界面的修改会同步写回此文件。

```yaml
communities:
  - name: "技术交流"
    platform: flarum
    url: "https://example.com"
    # ... 平台相关配置

models:
  - name: "qwen2.5:7b"
    type: local          # local | remote | openai | gemini | anthropic
    endpoint: "http://localhost:11434"
  - name: "gpt-4o"
    type: openai
    # api_key 通过环境变量 OPENAI_API_KEY 提供

rules:
  reply_threshold: ...   # 回复规则
  tier_intervals: ...    # 梯度间隔
```

密钥类配置通过环境变量提供，不写入配置文件明文。

## 模型管理

Web 界面支持：
- **扫描本地**：自动检测 Ollama 已下载模型，一键选用
- **手动添加**：远端/商业模型填地址和密钥
- **测试**：每个模型可发测试 prompt，看延迟和质量

## 本地模型

推荐优先用本地模型（免费、无限量），详见 [docs/LOCAL_MODELS.md](docs/LOCAL_MODELS.md)。

## 插件

平台适配器以插件形式载入，详见 [docs/PLUGINS.md](docs/PLUGINS.md)。

## 协议

本项目采用 [MIT](LICENSE) 协议开源。

第三方依赖的协议清单见 [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md)。

## 打赏

如果这个项目对你有帮助，欢迎请我喝杯咖啡：

| 支付宝 | 微信支付 | 以太坊 |
|--------|----------|--------|
| ![支付宝](docs/donate-alipay.jpg) | ![微信](docs/donate-wechat.png) | ![以太坊](docs/donate-eth.png) |

以太坊地址：`0x79b046b212c3151e08994e833a51289ca82b13df`
