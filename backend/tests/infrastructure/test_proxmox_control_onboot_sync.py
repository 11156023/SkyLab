"""``operations.control`` 在電源動作後自動對齊 onboot：開機→1、關機→0。

主機開機時自動啟動不開放使用者設定，所有開關機路徑（使用者按鈕、排程器、
治理回收、反挖礦處置…）都經過 ``control``，所以在這一層對齊即可涵蓋全部。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.exceptions import ProxmoxError
from app.infrastructure.proxmox import operations


class _FakeResourceApi:
    def __init__(self, calls: list[tuple[str, object]], *, config_error: Exception | None = None):
        self._calls = calls
        self._config_error = config_error

    @property
    def status(self) -> SimpleNamespace:
        def _make(action: str):
            def _post() -> str:
                self._calls.append(("power", action))
                return f"UPID:pve1:{action}"

            return SimpleNamespace(post=_post)

        return SimpleNamespace(
            start=_make("start"),
            stop=_make("stop"),
            shutdown=_make("shutdown"),
            reboot=_make("reboot"),
            reset=_make("reset"),
            suspend=_make("suspend"),
            resume=_make("resume"),
        )

    @property
    def config(self) -> SimpleNamespace:
        def _put(**params) -> None:
            if self._config_error is not None:
                raise self._config_error
            self._calls.append(("config", params))

        return SimpleNamespace(put=_put)


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    recorded: list[tuple[str, object]] = []
    monkeypatch.setattr(
        operations,
        "_resource_api",
        lambda node, vmid, rtype: _FakeResourceApi(recorded),
    )
    return recorded


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("start", 1),
        ("resume", 1),
        ("reboot", 1),
        ("reset", 1),
        ("stop", 0),
        ("shutdown", 0),
        ("suspend", 0),
    ],
)
def test_control_syncs_onboot_after_power_action(
    calls: list[tuple[str, object]], action: str, expected: int
) -> None:
    operations.control("pve1", 150, "qemu", action)

    assert calls == [("power", action), ("config", {"onboot": expected})]


def test_control_syncs_onboot_for_lxc_too(calls: list[tuple[str, object]]) -> None:
    operations.control("pve1", 151, "lxc", "start")
    operations.control("pve1", 151, "lxc", "shutdown")

    assert [c for c in calls if c[0] == "config"] == [
        ("config", {"onboot": 1}),
        ("config", {"onboot": 0}),
    ]


def test_control_syncs_onboot_only_after_blocking_task_succeeds(
    calls: list[tuple[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(node: str, upid: str, timeout_seconds: float | None = None) -> None:
        raise ProxmoxError("vGPU allocation failed")

    monkeypatch.setattr(operations, "basic_blocking_task_status", _fail)

    with pytest.raises(ProxmoxError):
        operations.control("pve1", 150, "qemu", "start", wait_timeout_seconds=5)

    # 開機任務失敗 → 機器沒有真的開起來，不該把 onboot 打開
    assert calls == [("power", "start")]


def test_control_power_action_not_failed_by_onboot_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, object]] = []
    monkeypatch.setattr(
        operations,
        "_resource_api",
        lambda node, vmid, rtype: _FakeResourceApi(
            recorded, config_error=RuntimeError("VM is locked (backup)")
        ),
    )

    # onboot 寫入失敗只記 warning；電源動作本身已送出，不能對外報錯
    operations.control("pve1", 150, "qemu", "stop")

    assert recorded == [("power", "stop")]


def test_sync_onboot_ignores_unknown_action(calls: list[tuple[str, object]]) -> None:
    operations.sync_onboot("pve1", 150, "qemu", "hibernate")
    assert calls == []
