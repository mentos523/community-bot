"""插件加载器：扫描 plugins/ 目录，按 manifest.yaml 加载适配器。"""
import logging
import os
import re
import sys
import yaml
import importlib.util

log = logging.getLogger(__name__)

PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "plugins")

# M8：插件名白名单，只允许字母数字下划线连字符，防路径穿越
_PLUGIN_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")


def discover_plugins():
    """扫描插件目录，返回 manifest 列表"""
    plugins = []
    if not os.path.isdir(PLUGINS_DIR):
        return plugins
    for name in sorted(os.listdir(PLUGINS_DIR)):
        manifest_path = os.path.join(PLUGINS_DIR, name, "manifest.yaml")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    m = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                # R2-M10：单个 manifest 损坏只跳过该插件，不拖垮整页
                log.warning(f"插件 {name} 的 manifest.yaml 解析失败，已跳过: {e}")
                continue
            m["_dir"] = os.path.join(PLUGINS_DIR, name)
            m["_name"] = name
            plugins.append(m)
    return plugins


def load_adapter(plugin_name: str, config: dict):
    """按插件名加载适配器类并实例化"""
    # M8：插件名白名单 + 路径 containment 双重校验，防 ../../ 跳出插件目录
    if not plugin_name or not _PLUGIN_NAME_RE.fullmatch(plugin_name):
        raise ValueError(f"非法插件名: {plugin_name!r}")
    base = os.path.abspath(PLUGINS_DIR)
    adapter_path = os.path.join(base, plugin_name, "adapter.py")
    if os.path.dirname(os.path.abspath(adapter_path)) != os.path.join(base, plugin_name):
        raise ValueError(f"插件路径越界: {plugin_name!r}")
    if not os.path.isfile(adapter_path):
        raise FileNotFoundError(f"插件 {plugin_name} 缺少 adapter.py")
    spec = importlib.util.spec_from_file_location(f"plugins.{plugin_name}.adapter", adapter_path)
    mod = importlib.util.module_from_spec(spec)
    # 让插件能 import src.core.base（N3：避免重复插入 sys.path）
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)
    spec.loader.exec_module(mod)
    # 约定：adapter.py 中类名为 Adapter
    cls = getattr(mod, "Adapter", None)
    if cls is None:
        raise AttributeError(f"插件 {plugin_name}/adapter.py 缺少 Adapter 类")
    return cls(config)


def validate_plugin_config(manifest: dict, config: dict) -> list[str]:
    """按 manifest 的 config_schema 校验配置，返回错误列表"""
    errors = []
    schema = manifest.get("config_schema", {})
    for key, rule in schema.items():
        # M9：显式判断 None/""，避免 boolean False 被 or 短路掉
        v = config.get(key)
        if v in (None, ""):
            v = os.environ.get(rule.get("env_var", ""), "")
        if rule.get("required") and v in (None, ""):
            errors.append(f"缺少必填配置: {key}（{rule.get('description', '')}）")
    return errors
