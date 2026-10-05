"""等待 PVE 任務時的輪詢容錯。

輪詢 task 狀態只是「問進度」：中途暫時連不到 API（connect timeout、連線被重設）
不代表任務失敗，任務在 PVE 端照跑。曾因一次 connect timeout 就把還在 qmclone
的申請標成建置失敗，linked clone 退 full clone 的路徑還會去刪那台正在複製的機器。
"""

from __future__ import annotations

from typing import Any

import pytest
from requests.exceptions import ConnectTimeout, ReadTimeout

from app.exceptions import ProxmoxError
from app.infrastructure.proxmox import client


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _FakeApi:
    """依序回放 status 結果；元素是例外就拋出。"""

    def __init__(self, results: list[Any], clock: _Clock, call_cost: float = 0.0):
        self.results = list(results)
        self.clock = clock
        self.call_cost = call_cost
        self.calls = 0

    def nodes(self, _node: str) -> _FakeApi:
        return self

    def tasks(self, _task: str) -> _FakeApi:
        return self

    @property
    def status(self) -> _FakeApi:
        return self

    @property
    def log(self) -> _FakeApi:
        return self

    def get(self) -> Any:
        self.calls += 1
        self.clock.now += self.call_cost
        result = self.results.pop(0) if self.results else self.results_tail
        if isinstance(result, BaseException):
            raise result
        return result

    results_tail: Any = None


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(client.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(client.time, "sleep", fake.sleep)
    return fake


def _use_api(monkeypatch: pytest.MonkeyPatch, api: _FakeApi) -> None:
    monkeypatch.setattr(client, "get_proxmox_api_for_node", lambda _node: api)


RUNNING = {"status": "running"}
DONE = {"status": "stopped", "exitstatus": "OK"}


def test_transient_connect_timeout_keeps_waiting(monkeypatch, clock) -> None:
    api = _FakeApi(
        [RUNNING, ConnectTimeout("connect timeout=30"), ReadTimeout("read"), DONE],
        clock,
        call_cost=30.0,
    )
    _use_api(monkeypatch, api)

    result = client.basic_blocking_task_status("pve19", "UPID:x", check_interval=5)

    assert result == DONE
    assert api.calls == 4


def test_outage_counter_resets_after_successful_poll(monkeypatch, clock) -> None:
    window = client.TASK_STATUS_OUTAGE_TOLERANCE_SECONDS
    # 兩段斷線各自都在容忍範圍內，加起來超過也不該放棄
    outage = [ConnectTimeout("x")] * int(window // 60)
    api = _FakeApi([*outage, RUNNING, *outage, DONE], clock, call_cost=30.0)
    _use_api(monkeypatch, api)

    result = client.basic_blocking_task_status("pve19", "UPID:x", check_interval=30)

    assert result == DONE


def test_persistent_outage_gives_up_with_clear_error(monkeypatch, clock) -> None:
    api = _FakeApi([RUNNING], clock, call_cost=30.0)
    api.results_tail = ConnectTimeout("connect timeout=30")
    _use_api(monkeypatch, api)

    with pytest.raises(ProxmoxError, match="may still be running") as excinfo:
        client.basic_blocking_task_status("pve19", "UPID:x", check_interval=5)

    assert isinstance(excinfo.value.__cause__, ConnectTimeout)
    waited = clock.now - 1000.0
    assert waited >= client.TASK_STATUS_OUTAGE_TOLERANCE_SECONDS


def test_task_failure_still_raises_immediately(monkeypatch, clock) -> None:
    api = _FakeApi([{"status": "stopped", "exitstatus": "clone failed"}, []], clock)
    _use_api(monkeypatch, api)

    with pytest.raises(ProxmoxError, match="clone failed"):
        client.basic_blocking_task_status("pve19", "UPID:x", check_interval=5)


def test_non_transport_error_is_not_retried(monkeypatch, clock) -> None:
    api = _FakeApi([ValueError("bad payload"), DONE], clock)
    _use_api(monkeypatch, api)

    with pytest.raises(ValueError):
        client.basic_blocking_task_status("pve19", "UPID:x", check_interval=5)
    assert api.calls == 1


def test_overall_timeout_still_applies_during_outage(monkeypatch, clock) -> None:
    api = _FakeApi([], clock, call_cost=30.0)
    api.results_tail = ConnectTimeout("x")
    _use_api(monkeypatch, api)

    with pytest.raises(TimeoutError):
        client.basic_blocking_task_status(
            "pve19", "UPID:x", check_interval=5, timeout_seconds=60
        )
