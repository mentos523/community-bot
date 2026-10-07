"""回复生成：prompt 模板 + 调用模型 + 校验，不合格重试一次。

R2-M3：用户自定义模板的 format 异常时回退默认模板，不再永久抛错。
R2-M4：用户内容拼 prompt 前截断（默认 3000 字符，可配 prompts.max_content_length）。
R2-N12：模板缺少 {content} 时记警告（用户问题根本没发给模型）。
"""
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

DEFAULT_MAX_CONTENT_LENGTH = 3000  # R2-M4：拼 prompt 前的用户内容截断长度


def _safe_format(tpl: str, bot_name: str, content: str, logger=None) -> str:
    """R2-M3：模板 format 失败时回退默认模板。R2-N12：缺 {content} 记警告。"""
    if "{content}" not in tpl:
        if logger:
            logger.warning("提示词模板缺少 {content} 占位符，用户问题未发给模型")
    try:
        return tpl.format(bot_name=bot_name, content=content)
    except (KeyError, IndexError, ValueError) as e:
        if logger:
            logger.warning(f"提示词模板占位符错误（{e}），已回退默认模板")
        return PROMPT_TEMPLATE.format(bot_name=bot_name, content=content)


def build_prompt(content: str, bot_name: str = "助教", template: str | None = None,
                 logger=None) -> str:
    """构造完整版 prompt。template 为空时用内置默认模板。"""
    return _safe_format(template or PROMPT_TEMPLATE, bot_name, content.strip(), logger)


def build_short_prompt(content: str, bot_name: str = "助教", template: str | None = None,
                       logger=None) -> str:
    """构造简化版 prompt（重试用）。"""
    return _safe_format(template or SHORT_PROMPT_TEMPLATE, bot_name, content.strip(), logger)


def generate_reply(model_manager, model_name: str, content: str,
                   bot_name: str = "助教", max_length: int = 0,
                   options: dict | None = None, logger=None,
                   prompts_cfg: dict | None = None) -> tuple[str | None, str]:
    """生成并校验回复。失败时用简化 prompt 重试一次。

    prompts_cfg: 配置文件 prompts 段，可含 bot_name/template/short_template/
    max_content_length。为空时用内置默认模板（向后兼容）。

    bot_name 优先级：社区级 bot_name > prompts.bot_name > 默认"助教"。

    返回 (正文|None, 原因)。
    """
    prompts_cfg = prompts_cfg or {}
    # U15：社区级 bot_name 优先（scheduler 传 comm.get("bot_name")，可能为 None）
    bot_name = bot_name or prompts_cfg.get("bot_name") or "助教"
    # R2-M4：超长内容截断，避免 prompt 体积爆炸
    content = (content or "").strip()
    max_cl = prompts_cfg.get("max_content_length", DEFAULT_MAX_CONTENT_LENGTH)
    try:
        max_cl = int(max_cl)
    except (TypeError, ValueError):
        max_cl = DEFAULT_MAX_CONTENT_LENGTH
    if max_cl > 0 and len(content) > max_cl:
        content = content[:max_cl] + "……（内容已截断）"
    prompts = [build_prompt(content, bot_name, prompts_cfg.get("template"), logger),
               build_short_prompt(content, bot_name, prompts_cfg.get("short_template"), logger)]
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
