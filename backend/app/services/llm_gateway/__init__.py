from __future__ import annotations

from importlib import import_module

__all__ = ["ai_gateway_service", "relay_service"]

_MODULES = {
    "ai_gateway_service": "app.services.llm_gateway.ai_gateway_service",
    "relay_service": "app.services.llm_gateway.relay_service",
}


def __getattr__(name: str):
    if name in _MODULES:
        return import_module(_MODULES[name])
    raise AttributeError(name)
