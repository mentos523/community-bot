"""弹性辅助混入类：失败计数指数退避 + 限流退避标记（非阻塞）。

供各平台插件的 Adapter 继承使用：

    from src.core.resilience import ResilienceMixin

    class Adapter(PlatformAdapter, ResilienceMixin):
        def __init__(self, config):
            self._init_resilience()
            super().__init__(config)
            ...

        def fetch_updates(self):
            if self._in_backoff() or self._rate_limited():
                return []
            try:
                ...
            except Exception:
                self._note_failure()
                return []
            self._note_success()
            return msgs
"""
import time


class ResilienceMixin:
    """失败退避（F-M1）+ 限流退避（H-S2/H-M3）。"""

    def _init_resilience(self):
        self._fail_count = 0
        self._backoff_until = 0.0
        self._rate_limited_until = 0.0

    # ---- 失败计数与指数退避（F-M1） ----
    def _in_backoff(self) -> bool:
        """是否处于失败退避期。"""
        return time.time() < self._backoff_until

    def _note_success(self):
        """一次完整成功后清零失败计数。"""
        self._fail_count = 0
        self._backoff_until = 0.0

    def _note_failure(self):
        """记录一次失败。连续 5 次后指数退避：1/2/4/8/16 分钟，上限 30 分钟。"""
        self._fail_count += 1
        if self._fail_count >= 5:
            delay = min(60 * (2 ** (self._fail_count - 5)), 1800)
            self._backoff_until = time.time() + delay

    # ---- 限流退避（H-S2/H-M3）：非阻塞，只打时间标记 ----
    def _rate_limited(self) -> bool:
        """是否处于限流退避期。"""
        return time.time() < self._rate_limited_until

    def _mark_rate_limited(self, retry_after: int = 0):
        """标记限流退避。retry_after 为平台返回的秒数，无则默认 5 秒，上限 5 分钟。"""
        try:
            delay = int(float(retry_after or 5))
        except (TypeError, ValueError):
            delay = 5
        delay = min(max(delay, 5), 300)
        self._rate_limited_until = time.time() + delay
