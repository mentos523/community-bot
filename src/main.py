"""入口：加载配置，初始化插件/模型，启动调度器。"""
import argparse
import logging
import os
import signal
import sys
import time

import yaml
from logging.handlers import RotatingFileHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.core.plugin_loader import (  # noqa: E402
    discover_plugins, load_adapter, validate_plugin_config)
from src.core.scheduler import Scheduler, POLL_INTERVAL  # noqa: E402
from src.models.manager import ModelManager  # noqa: E402

LOG_FILE = os.path.join(ROOT, "logs", "bot.log")
CONFIG_PATH = os.path.join(ROOT, "config", "config.yaml")
STATE_PATH = os.path.join(ROOT, "data", "state.json")


class BuildError(Exception):
    """S5：调度器构建失败（配置错误/无可用适配器），热重载时捕获。"""


_stop_requested = False


def _on_signal(signum, frame):
    """N5：SIGTERM/SIGINT 时只标记，主循环保存状态后退出。"""
    global _stop_requested
    _stop_requested = True


def setup_logging() -> logging.Logger:
    """日志：轮转（单个 10MB，保留 5 个）+ 控制台。"""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024,
                                backupCount=5, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("community-bot")


def load_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        # 小白友好：YAML 语法错误转中文提示
        mark = getattr(e, "problem_mark", None)
        line_info = f"第 {mark.line + 1} 行附近" if mark else "文件某处"
        raise ValueError(
            f"配置文件写错了（{line_info}有 YAML 语法问题）\n"
            f"  常见原因（三大忌）：\n"
            f"  ① 冒号后面要加空格：写 port: 52323，不要写 port:52323\n"
            f"  ② 不要用 Tab 缩进，只用空格\n"
            f"  ③ 不要用中文冒号：用 : 不要用 ：\n"
            f"  也可以把文件内容粘贴到 https://www.yamllint.com 在线检查\n"
            f"  原始错误：{e}"
        )
    if not isinstance(cfg, dict):
        raise ValueError(f"配置文件顶层必须是字典，实际是 {type(cfg).__name__}: {path}")
    return cfg


def resolve_community_config(comm: dict, manifests: dict) -> dict:
    """合并社区配置：manifest 默认值 < 环境变量 < config.yaml。

    密钥类字段通过 manifest 的 env_var 从环境变量读取。
    """
    merged = dict(comm)
    manifest = manifests.get(comm.get("plugin", ""), {})
    for key, rule in manifest.get("config_schema", {}).items():
        if key in merged and merged[key] not in (None, ""):
            continue
        env_val = os.environ.get(rule.get("env_var", ""), "")
        if env_val:
            merged[key] = env_val
        elif "default" in rule:
            merged[key] = rule["default"]
    return merged


def build_scheduler(config: dict, log: logging.Logger) -> Scheduler:
    """按配置构建模型管理器、适配器、调度器。"""
    mm = ModelManager(config.get("models"))
    log.info(f"已加载模型: {mm.list_models()}")
    manifests = {m["_name"]: m for m in discover_plugins()}
    log.info(f"发现插件: {sorted(manifests)}")
    adapters = {}
    seen_names = set()  # R2-N9：检测重名社区
    for comm in config.get("communities") or []:  # R2-M2：显式 null 兜底
        if not isinstance(comm, dict):
            log.error(f"社区配置条目不是 dict，已跳过: {comm!r:.80}")
            continue
        cname = comm.get("name") or "<unnamed>"
        if cname in seen_names:
            log.error(f"[{cname}] 社区名称重复，已跳过该条目")
            continue
        seen_names.add(cname)
        if not comm.get("name"):
            log.error("社区配置缺少 name，已跳过")
            continue
        if not comm.get("enabled", True):
            log.info(f"[{cname}] 已禁用，跳过")
            continue
        plugin = comm.get("plugin", "")
        if plugin not in manifests:
            log.error(f"[{cname}] 插件不存在: {plugin}")
            continue
        cfg = resolve_community_config(comm, manifests)
        errors = validate_plugin_config(manifests[plugin], cfg)
        if errors:
            log.error(f"[{cname}] 配置错误: {'; '.join(errors)}")
            continue
        try:
            adapters[comm["name"]] = load_adapter(plugin, cfg)
            log.info(f"[{cname}] 适配器加载成功 ({plugin})")
        except Exception:
            log.exception(f"[{cname}] 适配器加载失败")
    if not adapters:
        # S5：抛异常而不是 sys.exit，由调用方决定是退出（初始启动）还是保留旧调度器（热重载）
        raise BuildError("没有可用的社区适配器")
    return Scheduler(config, adapters, mm, STATE_PATH, log)


def parse_args():
    """U3：支持 --config 指定配置文件路径。"""
    p = argparse.ArgumentParser(description="社区机器人守护进程")
    p.add_argument("--config", default=CONFIG_PATH,
                   help=f"配置文件路径（默认 {CONFIG_PATH}）")
    return p.parse_args()


def main():
    args = parse_args()
    config_path = os.path.abspath(args.config)
    log = setup_logging()
    log.info("=" * 50)
    log.info("社区机器人启动")
    if not os.path.isfile(config_path):
        log.error(f"配置文件不存在: {config_path}")
        log.error("请先执行: cp config/config.example.yaml config/config.yaml")
        sys.exit(1)
    try:
        config = load_config(config_path)
    except ValueError as e:
        # 小白友好：配置文件写错时给中文提示而不是 traceback
        print(f"\n{e}\n", file=sys.stderr)
        sys.exit(1)
    try:
        sched = build_scheduler(config, log)
    except BuildError as e:
        # S5：sys.exit 只留给初始启动路径
        log.error(f"{e}，退出")
        sys.exit(1)
    last_mtime = os.path.getmtime(config_path)
    config_missing_warned = False  # R2-N1：配置文件被删后只告警一次
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    # 主循环：轮询 + 配置热重载
    while not _stop_requested:
        try:
            try:
                mtime = os.path.getmtime(config_path)
            except FileNotFoundError:
                # R2-N1：配置文件被删除时不再每轮刷屏，只告警一次并停止热重载检查
                if not config_missing_warned:
                    log.error(f"配置文件不存在: {config_path}，停止热重载检查，继续用旧配置运行")
                    config_missing_warned = True
                mtime = last_mtime
            if mtime != last_mtime:
                log.info("检测到配置变更，热重载")
                try:
                    new_config = load_config(config_path)
                    new_sched = build_scheduler(new_config, log)
                except Exception:
                    # S5：热重载失败保留旧调度器继续跑，不杀进程
                    log.exception("配置热重载失败，保留旧配置继续运行")
                else:
                    # R2-M5：迁移去重集合，避免热重载后重复回复
                    new_sched._seen_ids = sched._seen_ids
                    new_sched._seen_order = sched._seen_order
                    new_sched._sent_fingerprints = sched._sent_fingerprints
                    new_sched._sent_fp_order = sched._sent_fp_order
                    new_sched._bot_id_warned = sched._bot_id_warned
                    config, sched = new_config, new_sched
                    last_mtime = mtime
                    log.info("配置热重载成功")
            sched.poll_once()
        except Exception:
            log.exception("主循环异常")
        # N5：可中断的 sleep，收到信号时尽快退出
        for _ in range(POLL_INTERVAL):
            if _stop_requested:
                break
            time.sleep(1)
    log.info("收到退出信号，保存状态后退出")
    sched._save_state()


if __name__ == "__main__":
    main()
