from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.network import (
        firewall_service,
        gateway_service,
        nat_service,
        reverse_proxy_service,
        script_deploy_service,
        snapshot_service,
    )

__all__ = [
    "firewall_service",
    "gateway_service",
    "nat_service",
    "reverse_proxy_service",
    "script_deploy_service",
    "snapshot_service",
]

_MODULES = {
    "firewall_service": "app.services.network.firewall_service",
    "gateway_service": "app.services.network.gateway_service",
    "nat_service": "app.services.network.nat_service",
    "reverse_proxy_service": "app.services.network.reverse_proxy_service",
    "script_deploy_service": "app.services.network.script_deploy_service",
    "snapshot_service": "app.services.network.snapshot_service",
}


def __getattr__(name: str):
    if name in _MODULES:
        return import_module(_MODULES[name])
    raise AttributeError(name)
