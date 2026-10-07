# 第三方来源与协议说明

本项目自身采用 [MIT License](../LICENSE)（Copyright 2026 robbin）。

## 直接依赖

| 包 | 版本要求 | 协议 | 用途 |
|---|---------|------|------|
| PyYAML | >=6.0 | MIT | 解析 `config.yaml` 和插件 `manifest.yaml` |
| Flask | >=3.0 | BSD-3-Clause | Web 管理界面 |

完整依赖见 `requirements.txt`。

## 说明

- 本项目**不捆绑**任何第三方代码，依赖通过 pip 安装。
- 各平台插件通过其**公开 API** 与社区平台通信，不包含任何平台的私有 SDK 或逆向代码。
- 插件目录下的 `adapter.py` 均为本项目原创实现。
