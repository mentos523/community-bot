# 插件配置方法

## 插件结构

每个平台适配器是一个独立插件，放在 `plugins/<插件名>/`：

```
plugins/flarum/
├── manifest.yaml   # 插件元信息 + 配置项定义（必须）
├── adapter.py      # 适配器实现，类名必须为 Adapter（必须）
└── README.md       # 插件说明（可选）
```

## manifest.yaml 说明

```yaml
name: flarum                # 插件名（与目录名一致）
version: 1.0.0
description: Flarum 论坛适配器
author: robbin
platform_type: forum        # forum | chat

# 配置项定义：Web 界面据此自动生成配置表单
config_schema:
  url:
    type: string             # string | integer | boolean | password
    required: true           # 是否必填
    description: 论坛地址     # 表单上显示的说明
  token:
    type: string
    required: true
    secret: true             # 密钥字段：Web 用密码框，不回显
    env_var: FLARUM_TOKEN    # 可从环境变量读取，优先级：配置文件 > 环境变量
```

## 三种配置方式

### 1. Web 界面（推荐）

打开 `http://localhost:52323` → 插件管理：
- 左侧列出所有已发现插件（自动扫描 `plugins/`）
- 点插件进入配置页，表单由 `manifest.yaml` 的 `config_schema` 自动生成
- `secret: true` 的字段用密码框，保存时加密存储
- 点"测试连接"验证配置是否有效
- 保存后即时生效（热重载，无需重启）

### 2. 配置文件

`config/config.yaml` 中按插件名配置：

```yaml
plugins:
  flarum:
    enabled: true
    url: "https://example.com"
    # token 从环境变量 FLARUM_TOKEN 读取，此处不写
  telegram:
    enabled: false
    # bot_token 从环境变量 TELEGRAM_BOT_TOKEN 读取
```

### 3. 环境变量

密钥类配置建议走环境变量，不进文件：

```bash
export FLARUM_TOKEN="xxx"
export FLARUM_PASSWORD="xxx"
export TELEGRAM_BOT_TOKEN="xxx"
export OPENAI_API_KEY="xxx"
```

`manifest.yaml` 中 `env_var` 指定的变量名会被自动读取。

## 优先级

同一配置项：**Web 界面/配置文件 > 环境变量 > manifest 默认值**

## 启用/禁用

- Web 界面：插件列表页一键启用/禁用
- 配置文件：`plugins.<名>.enabled: true/false`
- 禁用的插件不被加载，不参与轮询

## 开发新插件

1. 在 `plugins/` 下新建目录，名即插件名
2. 写 `manifest.yaml`（参考 `plugins/flarum/manifest.yaml`）
3. 写 `adapter.py`，继承 `src.core.base.PlatformAdapter`，类名 `Adapter`，实现三个方法：
   - `fetch_updates()`：轻量拉取（不产生浏览量/已读）
   - `fetch_detail(conversation_id)`：拉取详情
   - `reply(conversation_id, content)`：发布回复
4. 重启或 Web 点"重新扫描"，插件自动被发现
