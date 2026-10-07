"""回复生成：prompt 模板 + 调用模型 + 校验，不合格重试一次。"""
from .validator import validate

# 完整版 prompt：12 岁能懂的中文
PROMPT_TEMPLATE = """你是{bot_name}，社区里的助教。

要求：
1. 用12岁孩子能听懂的中文回答，专业术语先解释，多用生活中的类比
2. 一次性完整回答，不要分段设悬念
3. 不要加"还有什么问题可以问我"之类的结尾
4. 直接输出回复正文，不要输出思考过程

问题：{content}
"""

# 简化版 prompt：首次生成校验失败时重试用
SHORT_PROMPT_TEMPLATE = """你是{bot_name}，社区助教。用12岁孩子能懂的中文简短完整地回答，不要输出思考过程，直接给正文：{content}"""


def build_prompt(content: str, bot_name: str = "助教", template: str | None = None) -> str:
    """构造完整版 prompt。template 为空时用内置默认模板。"""
    tpl = template or PROMPT_TEMPLATE
    return tpl.format(bot_name=bot_name, content=content.strip())


def build_short_prompt(content: str, bot_name: str = "助教", template: str | None = None) -> str:
    """构造简化版 prompt（重试用）。"""
    tpl = template or SHORT_PROMPT_TEMPLATE
    return tpl.format(bot_name=bot_name, content=content.strip())


def generate_reply(model_manager, model_name: str, content: str,
                   bot_name: str = "助教", max_length: int = 0,
                   options: dict | None = None, logger=None,
                   prompts_cfg: dict | None = None) -> tuple[str | None, str]:
    """生成并校验回复。失败时用简化 prompt 重试一次。

    prompts_cfg: 配置文件 prompts 段，可含 bot_name/template/short_template。
    为空时用内置默认模板（向后兼容）。

    返回 (正文|None, 原因)。
    """
    prompts_cfg = prompts_cfg or {}
    bot_name = prompts_cfg.get("bot_name", bot_name)
    prompts = [build_prompt(content, bot_name, prompts_cfg.get("template")),
               build_short_prompt(content, bot_name, prompts_cfg.get("short_template"))]
    reason = ""
    for i, prompt in enumerate(prompts):
        try:
            raw = model_manager.generate(model_name, prompt, options)
        except Exception as e:
            reason = f"模型调用失败: {e}"
            if logger:
                logger.warning(f"生成失败(第{i + 1}次): {e}")
            continue
        text = (raw or "").strip()
        ok, reason = validate(text, max_length)
        if ok:
            return text, "ok"
        if logger:
            logger.warning(f"校验失败(第{i + 1}次): {reason}")
    return None, reason
