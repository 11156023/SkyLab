from __future__ import annotations

from importlib import import_module
from types import ModuleType

__all__ = ["monitoring_service", "alert_service"]

_MODULES = {
    "monitoring_service": "app.services.monitoring.monitoring_service",
    "alert_service": "app.services.monitoring.alert_service",
}


def __getattr__(name: str) -> ModuleType:
    if name in _MODULES:
        return import_module(_MODULES[name])
    raise AttributeError(name)
