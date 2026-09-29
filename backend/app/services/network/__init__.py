"""網路相關服務：防火牆、NAT、反向代理、Gateway（nginx／WireGuard）、Cloudflare、IP 管理（另有 VM 快照 snapshot_service）。

各模組直接以 ``from app.services.network import firewall_service`` 這類子模組匯入使用。
"""
