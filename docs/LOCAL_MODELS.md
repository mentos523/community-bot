# 本地模型配置说明

社区机器人优先推荐本地模型：免费、无限量、数据不出内网。

## 1. 安装 Ollama

```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh

# macOS / Windows：去 https://ollama.com/download 下载安装包
```

启动服务（Linux 安装后自动启动）：

```bash
ollama serve          # 默认监听 http://localhost:11434
```

## 2. 下载模型

```bash
# 推荐（中文好、速度快、7B 参数，普通 CPU 可跑）
ollama pull qwen2.5:7b

# 其他可选
ollama pull qwen2.5:14b     # 效果更好，需要更大内存
ollama pull llama3.1:8b     # 英文强
ollama pull gemma2:9b       # Google 系
```

查看已下载的模型：

```bash
ollama list
```

## 3. 在社区机器人中配置

`config/config.yaml`：

```yaml
models:
  - name: "qwen2.5:7b"
    type: local
    endpoint: "http://localhost:11434"   # Ollama 地址
    options:
      temperature: 0.7      # 0-1：越低越严谨，越高越发散
      num_predict: 1500    # 单次最多生成的 token 数
      num_ctx: 4096       # 上下文窗口；长帖问答可调大到 8192（显存占用增加）
      keep_alive: "24h"   # 模型在内存中常驻多久；设大可避免冷启动（见下）

default_model: "qwen2.5:7b"
```

也可以在 Web 界面 → 模型管理 → 点"扫描本地模型"，自动发现 Ollama 里的模型并一键添加。

## 4. 硬件要求参考

| 模型规模 | 内存需求 | 说明 |
|---------|---------|------|
| 7B（如 qwen2.5:7b） | 8GB+ | 普通 CPU 可跑，生成约 30 秒/次 |
| 14B | 16GB+ | 建议有 GPU |
| 70B | 64GB+ / 高端 GPU | 服务器级别 |

**技巧**：
- 模型常驻内存可保持热启动，第一次生成慢、之后快
- CPU 机器选 7B 模型即可满足日常问答
- 如需更快响应，可加 GPU（Ollama 自动识别）

## 5. 冷启动说明

Ollama 为省内存，模型闲置一段时间后会自动卸载。下次调用时需重新加载（冷启动），7B 模型在 CPU 机器上可能慢几十秒。

- 社区机器人是 7×24 运行的，建议 `keep_alive` 设为 `"24h"` 让模型常驻内存，避免每次都要冷启动
- 即使冷启动，机器人也有熔断保护：模型连续失败会自动熔断几分钟，不会卡死轮询
- 首次部署后，可以在 Web → 模型管理 → 点"测试"预热一次

## 6. 远端 Ollama

Ollama 也可以装在另一台机器上（如家里的服务器）：

```yaml
models:
  - name: "qwen2.5:7b"
    type: local
    endpoint: "http://192.168.1.100:11434"   # 远端 Ollama 地址
```

注意：Ollama 默认只监听 localhost，远端访问需设置：

```bash
OLLAMA_HOST=0.0.0.0 ollama serve
```

内网使用 http 即可，公网访问务必加反向代理和认证。

## 7. 常见问题

**Q: 提示连接被拒绝？**
A: 检查 `ollama serve` 是否在运行，`curl http://localhost:11434/api/tags` 应返回模型列表。

**Q: 生成速度慢？**
A: 7B 模型纯 CPU 约 20-40 秒/次是正常的。如需更快：换更小的模型（如 qwen2.5:3b）、加 GPU、或调小 `num_predict`。

**Q: 显存/内存不足？**
A: Ollama 会自动量化加载，内存不足时换更小的模型。可用 `ollama ps` 查看当前加载的模型和显存占用。

**Q: 想同时用本地和商业模型？**
A: 可以混用，给不同社区配不同模型：

```yaml
communities:
  - name: "技术交流"
    plugin: flarum
    model: "qwen2.5:7b"       # 本地，免费

  - name: "VIP 群"
    plugin: telegram
    model: "gpt-4o-mini"      # 商业，效果好
```
