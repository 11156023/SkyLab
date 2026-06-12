from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.proxmox import operations as proxmox_service

__all__ = ["gpu_service", "provisioning_service", "proxmox_service"]

_MODULES = {
    "gpu_service": "app.services.proxmox.gpu_service",
    "provisioning_service": "app.services.proxmox.provisioning_service",
    "proxmox_service": "app.infrastructure.proxmox.operations",
}


def __getattr__(name: str):
    if name in _MODULES:
        return import_module(_MODULES[name])
    raise AttributeError(name)
