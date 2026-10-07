"""模型管理器：统一 generate() 接口，支持本地/远端/商业模型。

密钥一律从环境变量读取，不硬编码，不写进配置文件。
"""
import json
import os
import urllib.request
import urllib.error

# 各类型默认的密钥环境变量
DEFAULT_ENV_KEY = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

REQUEST_TIMEOUT = 120  # M2：模型生成超时（秒）。单线程调度，超时太长会卡住所有社区


class ModelError(Exception):
    pass


def _post_json(url: str, payload: dict, headers: dict | None = None,
               timeout: int = REQUEST_TIMEOUT) -> dict:
    """POST JSON 并解析返回。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:300]
        raise ModelError(f"HTTP {e.code}: {body}")
    except Exception as e:
        raise ModelError(f"请求失败: {e}")


class ModelManager:
    def __init__(self, models_cfg: list[dict] | None):
        self.models = {m["name"]: m for m in (models_cfg or []) if m.get("name")}

    def list_models(self) -> list[str]:
        return list(self.models)

    def get_options(self, model_name: str) -> dict:
        """N10：取某模型的 options（供调度器调用，不直取内部属性）。"""
        return (self.models.get(model_name) or {}).get("options") or {}

    def detect_local_models(self, endpoint: str = "http://localhost:11434") -> list[str]:
        """扫描本地 Ollama 已下载模型（供 Web 界面调用）。"""
        try:
            with urllib.request.urlopen(
                    endpoint.rstrip("/") + "/api/tags", timeout=10) as r:
                data = json.load(r)
            return [m.get("name") for m in data.get("models", []) if m.get("name")]
        except Exception:
            return []

    def _api_key(self, cfg: dict) -> str:
        """取密钥：配置 api_key > 配置 api_key_env > 类型默认环境变量。"""
        if cfg.get("api_key"):
            return cfg["api_key"]
        env_name = cfg.get("api_key_env") or DEFAULT_ENV_KEY.get(cfg.get("type", ""))
        if env_name:
            return os.environ.get(env_name, "")
        return ""

    def generate(self, model_name: str, prompt: str,
                 options: dict | None = None) -> str:
        """统一生成接口。options 如 {"temperature": 0.7, "num_predict": 1500}。"""
        cfg = self.models.get(model_name)
        if not cfg:
            raise ModelError(f"未配置模型: {model_name}")
        t = cfg.get("type", "local")
        if t == "local":
            return self._ollama(cfg, prompt, options)
        if t in ("remote", "openai"):
            return self._openai_compat(cfg, prompt, options)
        if t == "gemini":
            return self._gemini(cfg, prompt, options)
        if t == "anthropic":
            return self._anthropic(cfg, prompt, options)
        raise ModelError(f"未知模型类型: {t}")

    # ---- 后端实现 ----

    def _ollama(self, cfg: dict, prompt: str, options: dict | None) -> str:
        endpoint = (cfg.get("endpoint") or "http://localhost:11434").rstrip("/")
        payload = {"model": cfg["name"], "prompt": prompt, "stream": False}
        if options:
            payload["options"] = options
        data = _post_json(endpoint + "/api/generate", payload)
        text = data.get("response", "")
        if not text:
            raise ModelError("Ollama 返回空")
        return text

    def _openai_compat(self, cfg: dict, prompt: str, options: dict | None) -> str:
        """OpenAI 兼容接口：remote 自定义地址 / openai 官方。"""
        t = cfg.get("type")
        if t == "openai":
            endpoint = (cfg.get("endpoint") or "https://api.openai.com/v1").rstrip("/")
        else:
            endpoint = (cfg.get("endpoint") or "").rstrip("/")
            if not endpoint:
                raise ModelError("remote 类型必须配置 endpoint")
        key = self._api_key(cfg)
        if not key:
            raise ModelError("缺少 API Key（环境变量）")
        payload = {
            "model": cfg.get("remote_model") or cfg["name"],
            "messages": [{"role": "user", "content": prompt}],
        }
        if options:
            if "temperature" in options:
                payload["temperature"] = options["temperature"]
            if "num_predict" in options:
                payload["max_tokens"] = options["num_predict"]
        data = _post_json(endpoint + "/chat/completions", payload,
                          {"Authorization": f"Bearer {key}"})
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise ModelError(f"返回格式异常: {str(data)[:200]}")

    def _gemini(self, cfg: dict, prompt: str, options: dict | None) -> str:
        key = self._api_key(cfg)
        if not key:
            raise ModelError("缺少 GEMINI_API_KEY")
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{cfg.get('remote_model') or cfg['name']}:generateContent")
        payload: dict = {"contents": [{"parts": [{"text": prompt}]}]}
        if options:
            gen = {}
            if "temperature" in options:
                gen["temperature"] = options["temperature"]
            if "num_predict" in options:
                gen["maxOutputTokens"] = options["num_predict"]
            if gen:
                payload["generationConfig"] = gen
        data = _post_json(url, payload, {"x-goog-api-key": key})
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise ModelError(f"返回格式异常: {str(data)[:200]}")

    def _anthropic(self, cfg: dict, prompt: str, options: dict | None) -> str:
        key = self._api_key(cfg)
        if not key:
            raise ModelError("缺少 ANTHROPIC_API_KEY")
        payload = {
            "model": cfg.get("remote_model") or cfg["name"],
            "max_tokens": (options or {}).get("num_predict", 1500),
            "messages": [{"role": "user", "content": prompt}],
        }
        if options and "temperature" in options:
            payload["temperature"] = options["temperature"]
        data = _post_json("https://api.anthropic.com/v1/messages", payload, {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        })
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError):
            raise ModelError(f"返回格式异常: {str(data)[:200]}")
