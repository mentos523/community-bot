"""回复完整性校验：空回复、思维链泄露、截断。"""
import re

# 思维链泄露特征（N2：全文检查，不只看前 300 字）
LEAK_PATTERNS = [
    r"<think>", r"</think>",
    r"we need to", r"as an ai", r"as a language model",
    r"let me think", r"thinking process",
    r"我需要先思考", r"我的思考过程", r"思考过程如下",
    r"首先让我分析一下",
]

# 正常结尾标点（结尾是这些才算完整断句；N1：只保留右引号，去掉左引号）
END_PUNCT = ("。", "！", "？", "…", ".", "!", "?", "」", "』", ")", "）", '"')


def validate(text: str, max_length: int = 0) -> tuple[bool, str]:
    """校验模型输出。返回 (通过, 原因)。"""
    if not text or not text.strip():
        return False, "空回复"
    t = text.strip()
    if len(t) < 10:
        return False, f"过短({len(t)}字)"
    head = t
    for pat in LEAK_PATTERNS:
        if re.search(pat, head, re.IGNORECASE):
            return False, f"思维链泄露({pat})"
    if max_length and len(t) > max_length:
        return False, f"超长({len(t)}>{max_length})"
    if not t.endswith(END_PUNCT):
        return False, "结尾截断"
    return True, "ok"
