from __future__ import annotations

from importlib import import_module
from types import ModuleType

__all__ = [
    "alert_service",
    "health_policy",
    "heartbeat_service",
    "monitoring_service",
    "system_health_service",
]

_MODULES = {
    "monitoring_service": "app.services.monitoring.monitoring_service",
    "alert_service": "app.services.monitoring.alert_service",
    "health_policy": "app.services.monitoring.health_policy",
    "heartbeat_service": "app.services.monitoring.heartbeat_service",
    "system_health_service": "app.services.monitoring.system_health_service",
}


def __getattr__(name: str) -> ModuleType:
    if name in _MODULES:
        return import_module(_MODULES[name])
    raise AttributeError(name)
