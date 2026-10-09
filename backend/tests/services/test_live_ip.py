"""清單頁與防火牆拓撲查即時 IP 時，不能被 guest agent 沒回應的機器拖住。

回歸背景：QEMU 設定了 agent 但 guest 裡沒在跑（開機中、沒裝、當掉）時，PVE 會卡滿
3 秒的 guest-ping 逾時才回「QEMU guest agent is not running」。拓撲與清單每次載入
都逐台去問，開機中的機器一多 /firewall/topology 就超過 10 秒，等完拿到的還是
None、照樣回退到 DB 快取。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from typing import Any

import pytest

from app.services.resource import live_ip


def _vm(vmid: int, *, status: str = "running") -> dict[str, Any]:
    return {"node": "pve1", "type": "qemu", "vmid": vmid, "status": status}


@pytest.fixture
def stuck_agent() -> Generator[threading.Event, None, None]:
    """測試結束時放行卡住的查詢，不佔著共用執行緒池。"""
    release = threading.Event()
    yield release
    release.set()


def test_unresponsive_agent_does_not_hold_up_the_others(
    stuck_agent: threading.Event,
) -> None:
    def lookup(node: str, vmid: int, rtype: str) -> str | None:
        if vmid == 101:
            stuck_agent.wait(timeout=10)
            return None
        return f"10.0.1.{vmid}"

    started = time.monotonic()
    ips = live_ip.probe_live_ips([_vm(100), _vm(101)], lookup, wait_seconds=0.2)

    assert time.monotonic() - started < 2
    assert ips == {100: "10.0.1.100", 101: None}


def test_late_answer_is_served_on_the_next_call_without_asking_again(
    stuck_agent: threading.Event,
) -> None:
    calls: list[int] = []
    finished = threading.Event()

    def lookup(node: str, vmid: int, rtype: str) -> str | None:
        calls.append(vmid)
        stuck_agent.wait(timeout=10)
        finished.set()
        return "10.0.1.50"

    assert live_ip.probe_live_ips([_vm(100)], lookup, wait_seconds=0.05) == {100: None}

    stuck_agent.set()
    assert finished.wait(timeout=5)
    assert live_ip.probe_live_ips([_vm(100)], lookup, wait_seconds=2) == {
        100: "10.0.1.50"
    }
    assert calls == [100]


def test_machine_is_not_asked_twice_while_a_query_is_running(
    stuck_agent: threading.Event,
) -> None:
    calls: list[int] = []

    def lookup(node: str, vmid: int, rtype: str) -> str | None:
        calls.append(vmid)
        stuck_agent.wait(timeout=10)
        return None

    live_ip.probe_live_ips([_vm(100)], lookup, wait_seconds=0.05)
    live_ip.probe_live_ips([_vm(100)], lookup, wait_seconds=0.05)

    assert calls == [100]


def test_machine_without_an_answer_is_not_asked_again_right_away() -> None:
    calls: list[int] = []

    def lookup(node: str, vmid: int, rtype: str) -> str | None:
        calls.append(vmid)
        return None

    assert live_ip.probe_live_ips([_vm(100)], lookup) == {100: None}
    assert live_ip.probe_live_ips([_vm(100)], lookup) == {100: None}

    assert calls == [100]


def test_older_answer_is_served_at_once_and_refreshed_in_the_background(
    monkeypatch: pytest.MonkeyPatch, stuck_agent: threading.Event
) -> None:
    answers = iter(["10.0.1.50", "10.0.1.99"])
    refreshed = threading.Event()

    def lookup(node: str, vmid: int, rtype: str) -> str | None:
        ip = next(answers)
        if ip == "10.0.1.99":
            stuck_agent.wait(timeout=10)
            refreshed.set()
        return ip

    assert live_ip.probe_live_ips([_vm(100)], lookup) == {100: "10.0.1.50"}

    # 過了「不重問」的時間：先回上次的結果，不等背景那次重問
    monkeypatch.setattr(live_ip, "HIT_TTL", 0.0)
    started = time.monotonic()
    assert live_ip.probe_live_ips([_vm(100)], lookup) == {100: "10.0.1.50"}
    assert time.monotonic() - started < 0.5

    stuck_agent.set()
    assert refreshed.wait(timeout=5)
    monkeypatch.setattr(live_ip, "HIT_TTL", 60.0)
    assert live_ip.probe_live_ips([_vm(100)], lookup) == {100: "10.0.1.99"}


def test_answer_that_is_too_old_is_not_served() -> None:
    answers = iter(["10.0.1.50", "10.0.1.99"])

    def lookup(node: str, vmid: int, rtype: str) -> str | None:
        return next(answers)

    assert live_ip.probe_live_ips([_vm(100)], lookup) == {100: "10.0.1.50"}

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(live_ip, "STALE_TTL", 0.0)
        assert live_ip.probe_live_ips([_vm(100)], lookup) == {100: "10.0.1.99"}


def test_stopped_machines_are_never_asked() -> None:
    calls: list[int] = []

    def lookup(node: str, vmid: int, rtype: str) -> str | None:
        calls.append(vmid)
        return f"10.0.1.{vmid}"

    ips = live_ip.probe_live_ips([_vm(100), _vm(101, status="stopped")], lookup)

    assert calls == [100]
    assert ips == {100: "10.0.1.100", 101: None}


def test_lookup_error_counts_as_no_answer() -> None:
    def lookup(node: str, vmid: int, rtype: str) -> str | None:
        raise RuntimeError("node unreachable")

    assert live_ip.probe_live_ips([_vm(100)], lookup) == {100: None}


def test_batch_started_early_only_waits_for_the_time_that_is_left(
    stuck_agent: threading.Event,
) -> None:
    def lookup(node: str, vmid: int, rtype: str) -> str | None:
        stuck_agent.wait(timeout=10)
        return None

    batch = live_ip.start_live_ip_probes([_vm(100)], lookup, wait_seconds=0.3)
    time.sleep(0.4)  # 呼叫端這段時間在做別的事（拓撲：讀防火牆規則）

    started = time.monotonic()
    assert batch.collect() == {100: None}
    assert time.monotonic() - started < 0.2
