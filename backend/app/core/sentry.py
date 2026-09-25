"""Sentry 錯誤追蹤初始化（backend 與 arq worker 共用）。

``SENTRY_DSN`` 留空時完全不啟用。FastAPI／Starlette 整合由 sentry-sdk 依已安裝
套件自動掛上；worker 端沒有 HTTP，但未處理例外與 ERROR 日誌一樣會送出。
"""

from __future__ import annotations

import sentry_sdk

from app.core.config import settings

_state = {"initialized": False}


def init_sentry(component: str) -> bool:
    """啟用 Sentry；回傳是否真的有初始化（DSN 未設定時回 False）。"""
    if _state["initialized"] or not settings.SENTRY_DSN:
        return _state["initialized"]
    sentry_sdk.init(
        dsn=str(settings.SENTRY_DSN),
        environment=settings.ENVIRONMENT,
        release=settings.SENTRY_RELEASE or None,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
    )
    sentry_sdk.set_tag("component", component)
    _state["initialized"] = True
    return True


__all__ = ["init_sentry"]
