"""Guest agent 回報的 IPv4 過濾：只收真的能連的位址。"""

from __future__ import annotations

import pytest

from app.infrastructure.proxmox.operations import _is_usable_ipv4


@pytest.mark.parametrize(
    "ip",
    ["10.0.0.5", "192.168.1.20", "172.16.3.4", "8.8.8.8", " 10.0.0.6 "],
)
def test_usable_addresses(ip: str) -> None:
    assert _is_usable_ipv4(ip) is True


@pytest.mark.parametrize(
    "ip",
    [
        "",
        "127.0.0.1",
        "127.5.5.5",
        "169.254.1.1",
        "0.0.0.0",
        "224.0.0.1",      # multicast
        "240.0.0.1",      # reserved
        "10.0.0.256",     # 不是合法 IPv4
        "not-an-ip",
        "10.0.0.1/24",    # 帶遮罩的字串
        "fe80::1",        # IPv6
    ],
)
def test_unusable_addresses(ip: str) -> None:
    assert _is_usable_ipv4(ip) is False
