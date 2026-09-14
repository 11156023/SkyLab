from __future__ import annotations

from importlib import import_module
from types import ModuleType

__all__ = ["config_service", "lifecycle_policy", "lifecycle_service"]

_MODULES = {
    "config_service": "app.services.governance.config_service",
    "lifecycle_policy": "app.services.governance.lifecycle_policy",
    "lifecycle_service": "app.services.governance.lifecycle_service",
}


def __getattr__(name: str) -> ModuleType:
    if name in _MODULES:
        return import_module(_MODULES[name])
    raise AttributeError(name)
