"""自動關機送出 shutdown 後，機器被其他流程開回來時要清排程、不能強制斷電。

排程器每個 tick 先跑 process_due_request_starts 再跑 process_auto_stops：
申請時段內的機器被自動關機後，下一輪會先被開回來。自動關機必須看出
「這是關機之後才開的機」（uptime 比送出關機至今還短）並清掉排程，否則
每一輪都會「開機 → 強制斷電」循環。guest 真的不理 ACPI 時仍要升級成 stop。
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.scheduling import recurrence_scheduler

VMID = 321
TICK_SECONDS = 60
START_TASK_SECONDS = 5


@pytest.fixture()
def pve(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """模擬一台 PVE 機器、它的自動關機排程，以及排程器的時鐘。"""
    env: dict[str, Any] = {
        "clock": 10_000.0,
        "status": "running",
        "booted_at": 10_000.0 - 3600,  # 已開機一小時
        "honour_shutdown": True,
        "uptime_lag": 0,  # pvestatd 尚未更新造成的 uptime 落後秒數
        "omit_uptime": False,
        "actions": [],
        "cleared": [],
        "due": SimpleNamespace(
            vmid=VMID,
            auto_stop_at=datetime.now(UTC) - timedelta(minutes=1),
            auto_stop_reason="idle",
        ),
    }

    def resource_info(*, vmid: int) -> dict[str, Any]:
        row: dict[str, Any] = {"node": "pve1", "type": "qemu", "status": env["status"]}
        if not env["omit_uptime"]:
            row["uptime"] = (
                max(int(env["clock"] - env["booted_at"]) - env["uptime_lag"], 0)
                if env["status"] == "running"
                else 0
            )
        return row

    def control(node: str, vmid: int, rtype: str, action: str) -> None:
        env["actions"].append(action)
        if action == "stop" or (action == "shutdown" and env["honour_shutdown"]):
            env["status"] = "stopped"

    def set_auto_stop(**kw: Any) -> None:
        if kw["auto_stop_at"] is None:
            env["cleared"].append(kw["vmid"])
            env["due"] = None

    monkeypatch.setattr(recurrence_scheduler, "_shutdown_requested_at", {})
    monkeypatch.setattr(recurrence_scheduler, "_resource_info", resource_info)
    monkeypatch.setattr(recurrence_scheduler.proxmox_service, "control", control)
    monkeypatch.setattr(
        recurrence_scheduler, "Session", lambda engine: nullcontext(None)
    )
    monkeypatch.setattr(
        recurrence_scheduler.resource_repo,
        "list_due_auto_stops",
        lambda **kw: [env["due"]] if env["due"] is not None else [],
    )
    monkeypatch.setattr(
        recurrence_scheduler.resource_repo, "set_auto_stop", set_auto_stop
    )
    monkeypatch.setattr(recurrence_scheduler.time, "monotonic", lambda: env["clock"])
    return env


def _tick(env: dict[str, Any]) -> None:
    """一輪排程：申請時段內的機器沒在跑就先開回來，再處理自動關機。"""
    env["clock"] += TICK_SECONDS
    if env["status"] != "running":
        env["actions"].append("start")
        env["status"] = "running"
        env["booted_at"] = env["clock"]
        env["clock"] += START_TASK_SECONDS
    recurrence_scheduler.process_auto_stops()


def test_restarted_vm_is_not_hard_stopped_in_a_loop(pve: dict[str, Any]) -> None:
    recurrence_scheduler.process_auto_stops()
    assert pve["actions"] == ["shutdown"]
    assert pve["cleared"] == []

    for _ in range(20):
        _tick(pve)

    # 關機成功、被申請時段開回來一次，之後就一直開著，沒有強制斷電
    assert pve["actions"] == ["shutdown", "start"]
    assert pve["cleared"] == [VMID]
    assert pve["status"] == "running"
    assert recurrence_scheduler._shutdown_requested_at == {}


def test_guest_ignoring_shutdown_is_hard_stopped_only_once(
    pve: dict[str, Any],
) -> None:
    pve["honour_shutdown"] = False

    recurrence_scheduler.process_auto_stops()
    for _ in range(20):
        _tick(pve)

    # 不理 ACPI 的 guest 仍會被強制斷電，但被開回來後就清排程，不再循環
    assert pve["actions"] == ["shutdown", "stop", "start"]
    assert pve["cleared"] == [VMID]
    assert pve["status"] == "running"
    assert recurrence_scheduler._shutdown_requested_at == {}


def test_vm_booted_just_before_shutdown_is_not_mistaken_for_restart(
    pve: dict[str, Any],
) -> None:
    # 剛開機幾秒就收到關機、開機中不理 ACPI，且 PVE 回報的 uptime 還沒更新
    pve["honour_shutdown"] = False
    pve["booted_at"] = pve["clock"] - 2
    pve["uptime_lag"] = 8

    recurrence_scheduler._stop_one(resource=pve["due"])
    assert pve["actions"] == ["shutdown"]

    pve["clock"] += TICK_SECONDS
    recurrence_scheduler._stop_one(resource=pve["due"])
    assert pve["actions"] == ["shutdown"]
    assert pve["cleared"] == []

    pve["clock"] += recurrence_scheduler.FORCE_STOP_AFTER_SECONDS
    recurrence_scheduler._stop_one(resource=pve["due"])
    assert pve["actions"] == ["shutdown", "stop"]


def test_missing_uptime_keeps_escalating_to_hard_stop(pve: dict[str, Any]) -> None:
    pve["honour_shutdown"] = False
    pve["omit_uptime"] = True

    recurrence_scheduler._stop_one(resource=pve["due"])
    pve["clock"] += recurrence_scheduler.FORCE_STOP_AFTER_SECONDS
    recurrence_scheduler._stop_one(resource=pve["due"])

    assert pve["actions"] == ["shutdown", "stop"]
    assert pve["cleared"] == []
