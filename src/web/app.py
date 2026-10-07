"""社区机器人 Web 管理端 MVP。

运行：python -m src.web.app   （项目根目录下）
或：   python src/web/app.py

功能：仪表盘 / 插件管理 / 插件配置 / 模型管理 / 日志查看
配置与 config/config.yaml 同源，修改直接写回。
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
import urllib.error
from functools import wraps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session  # noqa: E402

from src.web import config_store as cs  # noqa: E402
from src.core import plugin_loader  # noqa: E402

app = Flask(__name__)

# ---- 会话密钥（S1/N8）：优先环境变量，否则每次启动随机生成 ----
_sk = os.environ.get("WEB_SECRET_KEY", "")
if _sk:
    app.secret_key = _sk
else:
    app.secret_key = os.urandom(32)
    print("警告: 未设置 WEB_SECRET_KEY，已生成随机密钥（重启后登录态失效）")


# ---------- 登录鉴权（S1） ----------

def _password_ok(pw: str) -> bool:
    """密码与环境变量 WEB_PASSWORD 做 SHA256 哈希比对。"""
    expected = os.environ.get("WEB_PASSWORD", "")
    if not expected:
        return False  # 未设置密码时一律拒绝登录
    return hmac.compare_digest(
        hashlib.sha256(pw.encode("utf-8")).hexdigest(),
        hashlib.sha256(expected.encode("utf-8")).hexdigest(),
    )


@app.before_request
def _require_login():
    if request.endpoint in ("login", "static"):
        return
    if not session.get("authed"):
        return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    pw_set = bool(os.environ.get("WEB_PASSWORD", ""))
    if request.method == "POST" and pw_set:
        if _password_ok(request.form.get("password", "")):
            session["authed"] = True
            nxt = request.args.get("next") or url_for("dashboard")
            return redirect(nxt)
        flash("密码错误")
    return render_template("login.html", pw_set=pw_set)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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
    """密钥一律全掩码，不泄露任何原文字符（N9）。"""
    return "********" if v else ""


# ---------- 仪表盘 ----------

@app.route("/")
def dashboard():
    cfg = cs.load_config()
    communities = cfg.get("communities") or []
    models = cfg.get("models") or []
    default_model = cfg.get("default_model", "")

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
                           models=models,
                           default_model=default_model,
                           ollama_ok=ollama_ok,
                           ollama_models=ollama_models)


# ---------- 社区管理（S2：读写 communities 列表段，与守护进程同源） ----------

def _merged_community_config(comm: dict, manifest: dict) -> dict:
    """合并社区配置：manifest 默认 < 环境变量 < 配置文件（与 main.py 一致）。"""
    merged = dict(comm)
    for key, rule in (manifest.get("config_schema") or {}).items():
        if merged.get(key) not in (None, ""):
            continue
        ev = os.environ.get(rule.get("env_var", ""), "")
        if ev:
            merged[key] = ev
        elif "default" in rule:
            merged[key] = rule["default"]
    return merged


@app.route("/plugins")
def plugins():
    cfg = cs.load_config()
    manifests = {m["_name"]: m for m in plugin_loader.discover_plugins()}
    items = []
    for comm in cfg.get("communities") or []:
        name = comm.get("name", "")
        plugin = comm.get("plugin", "")
        m = manifests.get(plugin, {})
        merged = _merged_community_config(comm, m)
        items.append({
            "name": name,
            "plugin": plugin,
            "description": m.get("description", ""),
            "version": m.get("version", ""),
            "platform_type": m.get("platform_type", ""),
            "model": comm.get("model") or cfg.get("default_model", ""),
            "enabled": bool(comm.get("enabled", True)),
            "missing": plugin_loader.validate_plugin_config(m, merged),
            "plugin_missing": plugin not in manifests,
        })
    used = {c.get("plugin") for c in cfg.get("communities") or []}
    unused = [m for m in plugin_loader.discover_plugins() if m["_name"] not in used]
    return render_template("plugins.html", items=items, unused=unused)


@app.route("/plugins/<name>/toggle", methods=["POST"])
def plugin_toggle(name):
    cfg = cs.load_config()
    enabled = (request.form.get("enabled") == "1")
    if cs.toggle_community(cfg, name, enabled):
        cs.save_config(cfg)
        flash(f"社区 {name} 已{'启用' if enabled else '禁用'}")
    else:
        flash(f"未找到社区 {name}")
    return redirect(url_for("plugins"))


@app.route("/plugins/<name>", methods=["GET", "POST"])
def plugin_config(name):
    cfg = cs.load_config()
    comm = cs.find_community(cfg, name)
    if not comm:
        flash(f"未找到社区 {name}")
        return redirect(url_for("plugins"))
    manifests = {m["_name"]: m for m in plugin_loader.discover_plugins()}
    m = manifests.get(comm.get("plugin", ""), {})
    schema = m.get("config_schema") or {}

    if request.method == "POST":
        values = {}
        for key, rule in schema.items():
            raw = request.form.get(f"cfg_{key}", "")
            if rule.get("type") == "boolean":
                values[key] = (raw == "1")
            elif rule.get("secret") and not raw.strip():
                continue  # 密码框留空 = 保留原值
            else:
                values[key] = raw.strip()
        # 社区级字段
        model = request.form.get("comm_model", "").strip()
        values["model"] = model  # 空表示用 default_model
        cs.set_community_values(cfg, name, values)
        cs.save_config(cfg)
        flash(f"社区 {name} 配置已保存")
        return redirect(url_for("plugin_config", name=name))

    cfg = cs.load_config()
    comm = cs.find_community(cfg, name)
    fields = []
    for key, rule in schema.items():
        cur, source = cs.effective_value(comm.get(key), rule.get("env_var", ""))
        ftype = rule.get("type", "string")
        if rule.get("secret"):
            shown = ""  # 密码框不回显原值
        elif ftype == "boolean":
            shown = bool(comm.get(key, rule.get("default", False)))
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
    errors = plugin_loader.validate_plugin_config(m, _merged_community_config(comm, m))
    return render_template("plugin_config.html", name=name, manifest=m,
                           fields=fields, errors=errors,
                           enabled=bool(comm.get("enabled", True)),
                           plugin=comm.get("plugin", ""),
                           comm_model=comm.get("model", ""),
                           default_model=cfg.get("default_model", ""))


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
            # M7：endpoint 只允许 http/https 协议，防 SSRF 到 file/gopher 等
            if endpoint and not endpoint.startswith(("http://", "https://")):
                flash("endpoint 必须以 http:// 或 https:// 开头")
                return redirect(url_for("models"))
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


# ---------- 全局设置（回复规则 / 梯度轮询 / 提示词） ----------

# 每个选项的详细说明（Web 表单用）
SETTING_DEFS = [
    ("rules", "回复规则", [
        ("min_question_length", "问题最短长度", "integer",
         "问题正文少于这么多字符就不回复（如“顶”、“？”）。默认 5。设为 0 则不限制。"),
        ("skip_keywords", "跳过关键词（逗号分隔）", "string",
         "内容包含这些词的消息不回复，如“测试,签到,广告”。多个词用英文逗号分隔。"),
        ("always_reply_users", "必回用户（逗号分隔）", "string",
         "名单里的用户每次都回复，不受“最短长度”和“跳过关键词”限制。填你的用户名，多个用英文逗号分隔。"),
        ("max_replies_per_discussion_per_day", "单讨论每日最多回复", "integer",
         "同一个讨论（帖子/话题）每天最多回复几次，防止在同一个帖子里刷屏。默认 2。"),
        ("max_replies_per_day", "全站每日最多回复", "integer",
         "所有社区加起来每天最多回复几次，控制模型调用量和费用。默认 20，可按你的需求调大或调小。"),
    ]),
    ("rules.reply_style", "回复风格", [
        ("max_length", "回复最长字符数", "integer",
         "超过此长度的回复会被按句子截断，保证不断句。默认 800。"),
        ("no_trailing", "不加客套结尾", "boolean",
         "勾选后回复末尾不加“还有什么问题可以问我”之类的客套话。"),
    ]),
    ("tiers", "梯度轮询（省浏览量）", [
        ("hot_interval", "Hot 检查间隔（秒）", "integer",
         "1 小时内有新回帖的讨论属于 Hot，每隔这么多秒检查一次。默认 60 秒。"),
        ("warm_interval", "Warm 检查间隔（秒）", "integer",
         "1–24 小时无新回帖的讨论属于 Warm，每隔这么多秒检查一次。默认 1800 秒（30 分钟）。"),
        ("cold_interval", "Cold 检查间隔（秒）", "integer",
         "24 小时–3 天无新回帖的讨论属于 Cold，每隔这么多秒检查一次。默认 604800 秒（7 天）。超过 3 天无活动的归档后不再进详情。"),
    ]),
    ("prompts", "提示词", [
        ("bot_name", "机器人自称", "string",
         "机器人在提示词里的自称，如“助教”、“小帮手”。默认“助教”。"),
        ("template", "完整版提示词模板", "text",
         "{bot_name} 会被替换为上面的自称，{content} 会被替换为用户的问题。每次生成回复都用这个模板。"),
        ("short_template", "简化版提示词模板（重试用）", "text",
         "完整版生成后校验失败时，用这个简化模板重试一次。"),
    ]),
]


def _get_nested(cfg: dict, path: str):
    cur = cfg
    for p in path.split("."):
        cur = cur.get(p, {}) if isinstance(cur, dict) else {}
    return cur if isinstance(cur, dict) else {}


@app.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = cs.load_config()
    if request.method == "POST":
        for section, _, fields in SETTING_DEFS:
            for key, _, ftype, _ in fields:
                form_key = section.replace(".", "_") + "__" + key
                raw = request.form.get(form_key, "").strip()
                # 定位到嵌套 dict
                target = cfg
                parts = section.split(".")
                for p in parts:
                    target = target.setdefault(p, {})
                if ftype == "boolean":
                    target[key] = form_key in request.form
                elif ftype == "integer":
                    try:
                        target[key] = int(raw)
                    except ValueError:
                        pass  # 非法输入保留原值
                elif key in ("skip_keywords", "always_reply_users"):
                    target[key] = [w.strip() for w in raw.split(",") if w.strip()]
                else:
                    target[key] = raw
        cs.save_config(cfg)
        return redirect("/settings?saved=1")
    # 准备显示值
    sections = []
    for section, title, fields in SETTING_DEFS:
        data = _get_nested(cfg, section)
        items = []
        for key, label, ftype, desc in fields:
            v = data.get(key, "")
            if key in ("skip_keywords", "always_reply_users") and isinstance(v, list):
                v = ",".join(v)
            items.append({"key": key, "label": label, "type": ftype, "desc": desc,
                          "value": v, "form_key": section.replace(".", "_") + "__" + key})
        sections.append({"title": title, "items": items})
    return render_template("settings.html", sections=sections,
                           saved=request.args.get("saved"))


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
    host = web.get("host", "127.0.0.1")  # S1：默认只监听本机
    port = int(web.get("port", 52323))
    if not os.environ.get("WEB_PASSWORD"):
        print("警告: 未设置 WEB_PASSWORD 环境变量，Web 登录已锁定（所有页面需登录）")
    print(f"Web 管理端启动: http://{host}:{port}")
    app.run(host=host, port=port)


if __name__ == "__main__":
    main()
