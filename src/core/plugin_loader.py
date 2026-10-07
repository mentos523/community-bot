"""插件加载器：扫描 plugins/ 目录，按 manifest.yaml 加载适配器。"""
import os
import sys
import yaml
import importlib.util

PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "plugins")


def discover_plugins():
    """扫描插件目录，返回 manifest 列表"""
    plugins = []
    if not os.path.isdir(PLUGINS_DIR):
        return plugins
    for name in sorted(os.listdir(PLUGINS_DIR)):
        manifest_path = os.path.join(PLUGINS_DIR, name, "manifest.yaml")
        if os.path.isfile(manifest_path):
            with open(manifest_path, encoding="utf-8") as f:
                m = yaml.safe_load(f) or {}
            m["_dir"] = os.path.join(PLUGINS_DIR, name)
            m["_name"] = name
            plugins.append(m)
    return plugins


def load_adapter(plugin_name: str, config: dict):
    """按插件名加载适配器类并实例化"""
    adapter_path = os.path.join(PLUGINS_DIR, plugin_name, "adapter.py")
    if not os.path.isfile(adapter_path):
        raise FileNotFoundError(f"插件 {plugin_name} 缺少 adapter.py")
    spec = importlib.util.spec_from_file_location(f"plugins.{plugin_name}.adapter", adapter_path)
    mod = importlib.util.module_from_spec(spec)
    # 让插件能 import src.core.base
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
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
        val = config.get(key) or os.environ.get(rule.get("env_var", ""), "")
        if rule.get("required") and not val:
            errors.append(f"缺少必填配置: {key}（{rule.get('description', '')}）")
    return errors
