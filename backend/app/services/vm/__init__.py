from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.vm import placement_service as vm_request_placement_service

__all__ = [
    "batch_provision_service",
    "spec_change_service",
    "vm_request_availability_service",
    "vm_request_placement_service",
    "vm_request_service",
    "workload_advisor",
]

_MODULES = {
    "batch_provision_service": "app.services.vm.batch_provision_service",
    "spec_change_service": "app.services.vm.spec_change_service",
    "vm_request_availability_service": "app.services.vm.vm_request_availability_service",
    "vm_request_placement_service": "app.services.vm.placement_service",
    "vm_request_service": "app.services.vm.vm_request_service",
    "workload_advisor": "app.services.vm.workload_advisor",
}


def __getattr__(name: str):
    if name in _MODULES:
        return import_module(_MODULES[name])
    raise AttributeError(name)
