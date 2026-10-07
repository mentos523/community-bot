"""入口：加载配置，初始化插件/模型，启动调度器。"""
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
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


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
    for comm in config.get("communities", []):
        if not comm.get("enabled", True):
            log.info(f"[{comm.get('name')}] 已禁用，跳过")
            continue
        plugin = comm.get("plugin", "")
        if plugin not in manifests:
            log.error(f"[{comm.get('name')}] 插件不存在: {plugin}")
            continue
        cfg = resolve_community_config(comm, manifests)
        errors = validate_plugin_config(manifests[plugin], cfg)
        if errors:
            log.error(f"[{comm.get('name')}] 配置错误: {'; '.join(errors)}")
            continue
        try:
            adapters[comm["name"]] = load_adapter(plugin, cfg)
            log.info(f"[{comm['name']}] 适配器加载成功 ({plugin})")
        except Exception:
            log.exception(f"[{comm['name']}] 适配器加载失败")
    if not adapters:
        # S5：抛异常而不是 sys.exit，由调用方决定是退出（初始启动）还是保留旧调度器（热重载）
        raise BuildError("没有可用的社区适配器")
    return Scheduler(config, adapters, mm, STATE_PATH, log)


def main():
    log = setup_logging()
    log.info("=" * 50)
    log.info("社区机器人启动")
    if not os.path.isfile(CONFIG_PATH):
        log.error(f"配置文件不存在: {CONFIG_PATH}")
        log.error("请先执行: cp config/config.example.yaml config/config.yaml")
        sys.exit(1)
    config = load_config(CONFIG_PATH)
    try:
        sched = build_scheduler(config, log)
    except BuildError as e:
        # S5：sys.exit 只留给初始启动路径
        log.error(f"{e}，退出")
        sys.exit(1)
    last_mtime = os.path.getmtime(CONFIG_PATH)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    # 主循环：轮询 + 配置热重载
    while not _stop_requested:
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
            if mtime != last_mtime:
                log.info("检测到配置变更，热重载")
                try:
                    new_config = load_config(CONFIG_PATH)
                    new_sched = build_scheduler(new_config, log)
                except Exception:
                    # S5：热重载失败保留旧调度器继续跑，不杀进程
                    log.exception("配置热重载失败，保留旧配置继续运行")
                else:
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
