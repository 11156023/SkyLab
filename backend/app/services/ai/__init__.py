from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.llm_gateway import ai_gateway_service as ai_api_service

__all__ = ["ai_api_service"]

_MODULES = {
    "ai_api_service": "app.services.llm_gateway.ai_gateway_service",
}


def __getattr__(name: str):
    if name in _MODULES:
        return import_module(_MODULES[name])
    raise AttributeError(name)
