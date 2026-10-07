"""社区机器人 Web 管理端 MVP。

运行：python -m src.web.app   （项目根目录下）
或：   python src/web/app.py

功能：仪表盘 / 插件管理 / 插件配置 / 模型管理 / 日志查看
配置与 config/config.yaml 同源，修改直接写回。
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify  # noqa: E402

from src.web import config_store as cs  # noqa: E402
from src.core import plugin_loader  # noqa: E402

app = Flask(__name__)
app.secret_key = os.environ.get("WEB_SECRET_KEY", "dev-secret-change-me")


# ---------- 小工具 ----------

def http_json(url, payload=None, headers=None, timeout=30, method=None):
    """urllib 简易 JSON 请求，避免引入 requests 依赖。"""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    if data and "Content-Type" not in req.headers:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}


def mask_secret(v: str) -> str:
    if not v:
        return ""
    return v[:2] + "*" * 6 if len(v) > 4 else "******"


# ---------- 仪表盘 ----------

@app.route("/")
def dashboard():
    cfg = cs.load_config()
    communities = cfg.get("communities") or []
    plugins_cfg = cfg.get("plugins") or {}
    models = cfg.get("models") or []
    default_model = cfg.get("default_model", "")

    # 插件启用状态速查
    enabled_map = {name: bool((plugins_cfg.get(name) or {}).get("enabled", True))
                   for name in plugins_cfg}

    # Ollama 连通性（取第一个 local 模型 endpoint）
    ollama_ok, ollama_models = False, []
    local_eps = [m.get("endpoint") for m in models if m.get("type") == "local" and m.get("endpoint")]
    ep = (local_eps[0] if local_eps else "http://localhost:11434").rstrip("/")
    code, data = http_json(ep + "/api/tags", timeout=5)
    if code == 200:
        ollama_ok = True
        ollama_models = [m.get("name") for m in data.get("models", [])]

    return render_template("dashboard.html",
                           communities=communities,
                           enabled_map=enabled_map,
                           models=models,
                           default_model=default_model,
                           ollama_ok=ollama_ok,
                           ollama_models=ollama_models)


# ---------- 插件管理 ----------

@app.route("/plugins")
def plugins():
    cfg = cs.load_config()
    plugins_cfg = cfg.get("plugins") or {}
    items = []
    for m in plugin_loader.discover_plugins():
        name = m["_name"]
        pcfg = plugins_cfg.get(name) or {}
        schema = m.get("config_schema") or {}
        # 必填项缺失数
        missing = plugin_loader.validate_plugin_config(m, pcfg)
        items.append({
            "name": name,
            "description": m.get("description", ""),
            "version": m.get("version", ""),
            "platform_type": m.get("platform_type", ""),
            "enabled": bool(pcfg.get("enabled", False)),
            "missing": missing,
        })
    return render_template("plugins.html", items=items)


@app.route("/plugins/<name>/toggle", methods=["POST"])
def plugin_toggle(name):
    cfg = cs.load_config()
    cfg.setdefault("plugins", {}).setdefault(name, {})["enabled"] = (request.form.get("enabled") == "1")
    cs.save_config(cfg)
    flash(f"插件 {name} 已{'启用' if request.form.get('enabled') == '1' else '禁用'}")
    return redirect(url_for("plugins"))


@app.route("/plugins/<name>", methods=["GET", "POST"])
def plugin_config(name):
    manifests = {m["_name"]: m for m in plugin_loader.discover_plugins()}
    m = manifests.get(name)
    if not m:
        flash(f"未找到插件 {name}")
        return redirect(url_for("plugins"))
    schema = m.get("config_schema") or {}

    if request.method == "POST":
        cfg = cs.load_config()
        pcfg = cfg.setdefault("plugins", {}).setdefault(name, {})
        pcfg["enabled"] = pcfg.get("enabled", False)
        for key, rule in schema.items():
            raw = request.form.get(f"cfg_{key}", "")
            if rule.get("type") == "boolean":
                pcfg[key] = (raw == "1")
            elif rule.get("secret") and not raw.strip():
                pass  # 密码框留空 = 保留原值
            else:
                pcfg[key] = raw.strip()
        cs.save_config(cfg)
        flash(f"插件 {name} 配置已保存")
        return redirect(url_for("plugin_config", name=name))

    cfg = cs.load_config()
    pcfg = cs.get_plugin_config(cfg, name)
    fields = []
    for key, rule in schema.items():
        cur, source = cs.effective_value(pcfg.get(key), rule.get("env_var", ""))
        ftype = rule.get("type", "string")
        if rule.get("secret"):
            shown = ""  # 密码框不回显原值
        elif ftype == "boolean":
            shown = bool(pcfg.get(key, rule.get("default", False)))
        else:
            shown = cur if source == "config" else (rule.get("default", "") if cur == "" else cur)
        fields.append({
            "key": key,
            "type": ftype,
            "secret": bool(rule.get("secret")),
            "required": bool(rule.get("required")),
            "description": rule.get("description", ""),
            "env_var": rule.get("env_var", ""),
            "source": source,
            "value": shown,
            "masked": mask_secret(cur) if rule.get("secret") and cur else "",
        })
    errors = plugin_loader.validate_plugin_config(m, pcfg)
    return render_template("plugin_config.html", name=name, manifest=m,
                           fields=fields, errors=errors,
                           enabled=bool(pcfg.get("enabled", False)))


# ---------- 模型管理 ----------

def _local_endpoint(cfg):
    models = cfg.get("models") or []
    for m in models:
        if m.get("type") == "local" and m.get("endpoint"):
            return m["endpoint"].rstrip("/")
    return "http://localhost:11434"


@app.route("/models", methods=["GET", "POST"])
def models():
    cfg = cs.load_config()
    models = cfg.get("models") or []
    default_model = cfg.get("default_model", "")
    scanned = None

    if request.method == "POST":
        action = request.form.get("action")
        if action == "scan":
            ep = _local_endpoint(cfg)
            code, data = http_json(ep + "/api/tags", timeout=10)
            if code == 200:
                have = {m.get("name") for m in models}
                scanned = [{"name": m.get("name"), "added": m.get("name") in have}
                           for m in data.get("models", [])]
                if not scanned:
                    flash("Ollama 可达，但未发现已下载模型")
            else:
                flash(f"扫描失败：{ep} 返回 {code}（Ollama 未运行？）")
        elif action == "add":
            name = request.form.get("name", "").strip()
            if name and not any(m.get("name") == name for m in models):
                models.append({"name": name, "type": "local",
                               "endpoint": _local_endpoint(cfg)})
                cfg["models"] = models
                cs.save_config(cfg)
                flash(f"已添加模型 {name}")
        elif action == "add_custom":
            name = request.form.get("name", "").strip()
            mtype = request.form.get("type", "remote")
            endpoint = request.form.get("endpoint", "").strip()
            if name and not any(m.get("name") == name for m in models):
                entry = {"name": name, "type": mtype}
                if endpoint:
                    entry["endpoint"] = endpoint
                models.append(entry)
                cfg["models"] = models
                cs.save_config(cfg)
                flash(f"已添加模型 {name}")
        elif action == "delete":
            name = request.form.get("name", "")
            models = [m for m in models if m.get("name") != name]
            cfg["models"] = models
            if cfg.get("default_model") == name:
                cfg["default_model"] = models[0]["name"] if models else ""
            cs.save_config(cfg)
            flash(f"已删除模型 {name}")
        elif action == "set_default":
            name = request.form.get("name", "")
            if any(m.get("name") == name for m in models):
                cfg["default_model"] = name
                cs.save_config(cfg)
                flash(f"默认模型已切换为 {name}")
        return redirect(url_for("models")) if action != "scan" else \
            render_template("models.html", models=cfg.get("models") or [],
                            default_model=cfg.get("default_model", ""), scanned=scanned)

    return render_template("models.html", models=models,
                           default_model=default_model, scanned=scanned)


@app.route("/models/test", methods=["POST"])
def model_test():
    """测试单个模型：发一句短 prompt，看延迟与首段输出。返回 JSON。"""
    cfg = cs.load_config()
    name = request.form.get("name", "")
    model = next((m for m in cfg.get("models") or [] if m.get("name") == name), None)
    if not model:
        return jsonify({"ok": False, "error": "模型不存在"})
    mtype = model.get("type", "local")
    endpoint = (model.get("endpoint") or "http://localhost:11434").rstrip("/")
    prompt = "用一句话介绍你自己。"
    t0 = time.time()
    try:
        if mtype == "local":
            code, data = http_json(endpoint + "/api/generate", {
                "model": name, "prompt": prompt, "stream": False,
                "options": {"num_predict": 80}}, timeout=120)
            text = (data.get("response") or "")[:300] if code == 200 else ""
            err = "" if code == 200 else f"HTTP {code}: {data.get('error', '')}"
        elif mtype in ("remote", "openai"):
            key = os.environ.get("OPENAI_API_KEY", "")
            code, data = http_json(endpoint.rstrip("/") + "/v1/chat/completions", {
                "model": name,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 80},
                headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=120)
            ch = (data.get("choices") or [{}])[0]
            text = ((ch.get("message") or {}).get("content") or "")[:300] if code == 200 else ""
            err = "" if code == 200 else f"HTTP {code}: {data.get('error', '')}"
        elif mtype == "gemini":
            key = os.environ.get("GEMINI_API_KEY", "")
            code, data = http_json(
                f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent?key={key}",
                {"contents": [{"parts": [{"text": prompt}]}],
                 "generationConfig": {"maxOutputTokens": 80}}, timeout=120)
            parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
            text = "".join(p.get("text", "") for p in parts)[:300] if code == 200 else ""
            err = "" if code == 200 else f"HTTP {code}: {data.get('error', '')}"
        elif mtype == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            code, data = http_json("https://api.anthropic.com/v1/messages", {
                "model": name, "max_tokens": 80,
                "messages": [{"role": "user", "content": prompt}]},
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=120)
            blocks = data.get("content") or []
            text = "".join(b.get("text", "") for b in blocks)[:300] if code == 200 else ""
            err = "" if code == 200 else f"HTTP {code}: {data.get('error', '')}"
        else:
            return jsonify({"ok": False, "error": f"未知模型类型: {mtype}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})
    dt = round(time.time() - t0, 1)
    if err:
        return jsonify({"ok": False, "error": err, "latency": dt})
    return jsonify({"ok": True, "latency": dt, "text": text})


# ---------- 日志查看 ----------

@app.route("/logs")
def logs():
    files = []
    if os.path.isdir(cs.LOGS_DIR):
        for fn in sorted(os.listdir(cs.LOGS_DIR)):
            fp = os.path.join(cs.LOGS_DIR, fn)
            if os.path.isfile(fp):
                files.append({"name": fn, "size": os.path.getsize(fp)})
    sel = request.args.get("file", files[0]["name"] if files else "")
    lines, total = [], 0
    fp = os.path.join(cs.LOGS_DIR, sel)
    if sel and os.path.isfile(fp) and os.path.dirname(os.path.abspath(fp)) == os.path.abspath(cs.LOGS_DIR):
        n = min(int(request.args.get("n", 200)), 2000)
        with open(fp, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        total = len(all_lines)
        lines = all_lines[-n:]
    return render_template("logs.html", files=files, sel=sel, lines=lines, total=total)


# ---------- 入口 ----------

def main():
    cfg = cs.load_config()
    web = cfg.get("web") or {}
    host = web.get("host", "0.0.0.0")
    port = int(web.get("port", 52323))
    print(f"Web 管理端启动: http://{host}:{port}")
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
