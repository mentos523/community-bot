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
    type: string             # string | integer | boolean（密码框靠 secret 标记，不是 type）
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

打开 `http://localhost:52323` → 社区管理：
- 页面列出 `config.yaml` 里已配置的社区，可启用/禁用、点"配置"修改
- 点"新增社区"可添加新社区：选插件（下拉，来自自动扫描 `plugins/` 的结果）→ 填社区名称 → 按该插件 `manifest.yaml` 的 `config_schema` 生成表单填写
- 表单由 `config_schema` 自动生成，`secret: true` 的字段用密码框（留空表示保留原值）
- **注意**：表单填写的值会**明文写入** `config.yaml`；密钥类建议走环境变量（表单下方会提示对应的环境变量名），不要填进表单
- 保存后即时生效（热重载，无需重启）

### 2. 配置文件

`config/config.yaml` 的 `communities` 列表中，每个社区条目直接写插件配置项：

```yaml
communities:
  - name: "技术交流"      # 社区名称（Web 界面按此管理）
    plugin: flarum       # 插件名
    enabled: true
    model: "qwen2.5:7b"  # 本社区用的模型（留空用 default_model）
    url: "https://example.com"
    tag_filter: "tech"
    # token 从环境变量 FLARUM_TOKEN 读取，此处不写
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
- 配置文件：`communities` 条目中 `enabled: true/false`
- 禁用的插件不被加载，不参与轮询

## 开发新插件

1. 在 `plugins/` 下新建目录，名即插件名
2. 写 `manifest.yaml`（参考 `plugins/flarum/manifest.yaml`）
3. 写 `adapter.py`，继承 `src.core.base.PlatformAdapter`，类名 `Adapter`，实现三个方法：
   - `fetch_updates()`：轻量拉取（不产生浏览量/已读）
   - `fetch_detail(conversation_id)`：拉取详情
   - `reply(conversation_id, content)`：发布回复
4. 重启或 Web 点"重新扫描"，插件自动被发现
