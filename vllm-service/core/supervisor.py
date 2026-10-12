"""vLLM 子程序崩潰後的自動重啟策略（單模型與多模型 cluster 共用）。

launcher 以 nohup 在背景執行，vLLM 啟動後若崩潰，沒有人會發現；這裡決定
每次崩潰後要等多久再重啟、何時放棄。等待時間從 ``backoff_seconds`` 起每次
加倍（上限 ``MAX_BACKOFF_SECONDS``）；模型連續運行超過 ``reset_after_seconds``
才崩潰時視為偶發，重啟次數重新計算，偶發的崩潰不會累積到上限而被永久放棄。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 10.0
DEFAULT_RESET_AFTER_SECONDS = 600.0
MAX_BACKOFF_SECONDS = 300.0


@dataclass(frozen=True)
class RestartPolicy:
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS
    reset_after_seconds: float = DEFAULT_RESET_AFTER_SECONDS

    @classmethod
    def from_settings(cls, settings: Any) -> RestartPolicy:
        """由 Settings 的 ENGINE_RESTART_* 欄位建立；測試替身沒有這些欄位時用預設值。"""
        return cls(
            max_attempts=int(getattr(settings, "engine_restart_max_attempts", DEFAULT_MAX_ATTEMPTS)),
            backoff_seconds=float(
                getattr(settings, "engine_restart_backoff_seconds", DEFAULT_BACKOFF_SECONDS)
            ),
            reset_after_seconds=float(
                getattr(settings, "engine_restart_reset_seconds", DEFAULT_RESET_AFTER_SECONDS)
            ),
        )


class RestartTracker:
    """記錄單一模型目前這一輪已重啟幾次。"""

    def __init__(self, policy: RestartPolicy) -> None:
        self.policy = policy
        self.attempts = 0

    def next_delay(self, uptime_seconds: float) -> float | None:
        """程序結束時呼叫：回傳重啟前要等的秒數；已達上限則回傳 None（放棄）。"""
        if uptime_seconds >= self.policy.reset_after_seconds:
            self.attempts = 0
        if self.attempts >= self.policy.max_attempts:
            return None
        delay = min(self.policy.backoff_seconds * (2 ** self.attempts), MAX_BACKOFF_SECONDS)
        self.attempts += 1
        return delay
