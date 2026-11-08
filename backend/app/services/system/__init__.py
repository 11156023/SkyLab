from __future__ import annotations

from importlib import import_module

__all__ = ["feedback_service", "setup_service"]

_MODULES = {
    "feedback_service": "app.services.system.feedback_service",
    "setup_service": "app.services.system.setup_service",
}


def __getattr__(name: str):
    if name in _MODULES:
        return import_module(_MODULES[name])
    raise AttributeError(name)
