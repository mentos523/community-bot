"""配置读写：与 config/config.yaml 同源，Web 修改直接写回此文件。

S2：Web 只读写 communities 列表段，与守护进程（main.py）同源。
"""
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
    """写回 config/config.yaml。N4：先写临时文件再原子替换，避免读到一半的 YAML。"""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, CONFIG_PATH)


def get_communities(cfg: dict) -> list:
    """取 communities 列表。"""
    return cfg.get("communities") or []


def find_community(cfg: dict, name: str) -> dict | None:
    """按名称找社区条目（返回引用，可直接修改）。"""
    for c in get_communities(cfg):
        if c.get("name") == name:
            return c
    return None


def toggle_community(cfg: dict, name: str, enabled: bool) -> bool:
    """启用/禁用某社区。返回是否找到。"""
    comm = find_community(cfg, name)
    if comm is None:
        return False
    comm["enabled"] = enabled
    return True


def set_community_values(cfg: dict, name: str, values: dict) -> bool:
    """更新某社区的配置键。返回是否找到。"""
    comm = find_community(cfg, name)
    if comm is None:
        return False
    comm.update(values)
    return True


def add_community(cfg: dict, name: str, plugin: str) -> bool:
    """新增社区条目。名称已存在时返回 False。"""
    if find_community(cfg, name) is not None:
        return False
    comms = cfg.setdefault("communities", [])
    if not isinstance(comms, list):
        cfg["communities"] = comms = []
    comms.append({"name": name, "plugin": plugin, "enabled": True})
    return True


def effective_value(cfg_value, env_var: str):
    """配置值优先，其次环境变量。返回 (值, 来源)。"""
    if cfg_value not in (None, ""):
        return cfg_value, "config"
    if env_var:
        v = os.environ.get(env_var, "")
        if v:
            return v, "env"
    return "", "none"
