from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.scheduling import coordinator as vm_request_schedule_service

__all__ = ["vm_request_schedule_service"]

_MODULES = {
    "vm_request_schedule_service": "app.services.scheduling.coordinator",
}


def __getattr__(name: str):
    if name in _MODULES:
        return import_module(_MODULES[name])
    raise AttributeError(name)
