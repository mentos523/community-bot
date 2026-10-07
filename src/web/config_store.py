"""配置读写：与 config/config.yaml 同源，Web 修改直接写回此文件。"""
import os
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(ROOT, "config", "config.yaml")
EXAMPLE_PATH = os.path.join(ROOT, "config", "config.example.yaml")
LOGS_DIR = os.path.join(ROOT, "logs")


def load_config() -> dict:
    """读取配置。config.yaml 不存在时回退到 config.example.yaml。"""
    path = CONFIG_PATH if os.path.isfile(CONFIG_PATH) else EXAMPLE_PATH
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_config(cfg: dict) -> None:
    """写回 config/config.yaml。"""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def get_plugin_config(cfg: dict, name: str) -> dict:
    """取某插件的配置段（含 enabled）。"""
    return (cfg.get("plugins") or {}).get(name) or {}


def effective_value(cfg_value, env_var: str):
    """配置值优先，其次环境变量。返回 (值, 来源)。"""
    if cfg_value not in (None, ""):
        return cfg_value, "config"
    if env_var:
        v = os.environ.get(env_var, "")
        if v:
            return v, "env"
    return "", "none"
