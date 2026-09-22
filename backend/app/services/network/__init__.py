from __future__ import annotations

from importlib import import_module

__all__ = [
    "cloudflare_service",
    "firewall_service",
    "gateway_service",
    "nat_service",
    "reverse_proxy_service",
    "snapshot_service",
    "traefik_runtime_service",
]

_MODULES = {
    "cloudflare_service": "app.services.network.cloudflare_service",
    "firewall_service": "app.services.network.firewall_service",
    "gateway_service": "app.services.network.gateway_service",
    "nat_service": "app.services.network.nat_service",
    "reverse_proxy_service": "app.services.network.reverse_proxy_service",
    "snapshot_service": "app.services.network.snapshot_service",
    "traefik_runtime_service": "app.services.network.traefik_runtime_service",
}


def __getattr__(name: str):
    if name in _MODULES:
        return import_module(_MODULES[name])
    raise AttributeError(name)
