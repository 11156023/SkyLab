"""Coverage tests for app.services.resource.live_ip (cache pruning, entry filtering)."""

from __future__ import annotations

import time
from collections.abc import Generator

import pytest

from app.services.resource import live_ip


@pytest.fixture(autouse=True)
def _clean_cache() -> Generator[None, None, None]:
    live_ip.clear_live_ip_cache()
    yield
    live_ip.clear_live_ip_cache()


def test_oversized_cache_drops_stale_entries_and_skips_entries_without_vmid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live_ip, "_CACHE_PRUNE_SIZE", 1)
    now = time.monotonic()
    live_ip._cache[("pve1", 900)] = (now - live_ip.STALE_TTL - 5, "10.0.0.9")
    live_ip._cache[("pve1", 901)] = (now - live_ip.STALE_TTL - 1, None)
    live_ip._cache[("pve1", 902)] = (now, "10.0.0.2")

    asked: list[int] = []

    def lookup(_node: str, vmid: int, _rtype: str) -> str | None:
        asked.append(vmid)
        return None

    batch = live_ip.start_live_ip_probes(
        [
            {"node": "pve1", "type": "qemu", "status": "running"},  # no vmid
            {"node": "pve1", "type": "qemu", "vmid": 903, "status": "stopped"},
        ],
        lookup,
        wait_seconds=0.0,
    )

    assert ("pve1", 900) not in live_ip._cache
    assert ("pve1", 901) not in live_ip._cache
    assert ("pve1", 902) in live_ip._cache
    assert batch.collect() == {903: None}
    assert asked == []
