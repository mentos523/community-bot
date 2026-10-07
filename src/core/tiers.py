"""梯度轮询：按会话最后活动时间分级，只在到期时进详情拉取。"""
from datetime import datetime, timezone

# 梯度边界（秒）
HOT_BOUND = 3600     # 1 小时内有活动
WARM_BOUND = 86400   # 24 小时内有活动
COLD_BOUND = 259200  # 3 天内有活动

TIERS = ("hot", "warm", "cold", "archived")


def get_tier(last_activity_iso: str | None) -> str:
    """按最后活动时间返回梯度。"""
    if not last_activity_iso:
        return "hot"
    try:
        la = datetime.fromisoformat(last_activity_iso)
        if la.tzinfo is None:
            la = la.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - la).total_seconds()
    except Exception:
        return "hot"
    if delta < 0:
        return "hot"
    if delta < HOT_BOUND:
        return "hot"
    if delta < WARM_BOUND:
        return "warm"
    if delta < COLD_BOUND:
        return "cold"
    return "archived"


def tier_due(tier: str, last_check_iso: str | None, intervals: dict) -> bool:
    """该梯度是否到了进详情检查的时间。

    intervals 如 {"hot": 60, "warm": 1800, "cold": 604800}。
    archived 无间隔，永远返回 False（列表仍会监控新活动）。
    """
    interval = intervals.get(tier)
    if interval is None:
        return False
    if not last_check_iso:
        return True
    try:
        lc = datetime.fromisoformat(last_check_iso)
        if lc.tzinfo is None:
            lc = lc.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - lc).total_seconds() >= interval
    except Exception:
        return True
