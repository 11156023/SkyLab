"""告警評估純函式測試（不依賴 PVE/DB/SMTP）。"""

from datetime import datetime, timedelta, timezone

from app.services.monitoring.alert_service import (
    AlertDecision,
    MetricSample,
    collect_samples,
    evaluate,
)

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


class FakeConfig:
    alerts_enabled = True
    alert_cpu_threshold = 90.0
    alert_memory_threshold = 90.0
    alert_disk_threshold = 90.0
    alert_cooldown_minutes = 30


class FakeAlert:
    """模擬 AlertEvent 供 evaluate 使用。"""

    def __init__(
        self,
        *,
        target: str,
        metric: str,
        created_at: datetime,
        resolved_at: datetime | None = None,
    ) -> None:
        self.target = target
        self.metric = metric
        self.created_at = created_at
        self.resolved_at = resolved_at


def test_collect_samples_nodes_and_running_vms() -> None:
    nodes = [
        {
            "node": "pve1",
            "cpu": 0.95,
            "maxcpu": 8,
            "mem": 9,
            "maxmem": 10,
            "disk": 50,
            "maxdisk": 100,
        },
        # maxmem/maxdisk 為 0：該指標略過
        {"node": "pve2", "cpu": 0.5, "maxcpu": 4, "mem": 1, "maxmem": 0,
         "disk": 1, "maxdisk": 0},
    ]
    resources = [
        {"vmid": 100, "type": "qemu", "status": "running",
         "cpu": 0.99, "mem": 95, "maxmem": 100},
        {"vmid": 101, "type": "qemu", "status": "stopped",
         "cpu": 0.0, "mem": 0, "maxmem": 100},
    ]

    samples = collect_samples(nodes, resources)
    keyed = {(s.scope, s.target, s.metric): s.value for s in samples}

    assert keyed[("node", "pve1", "cpu")] == 95.0
    assert keyed[("node", "pve1", "memory")] == 90.0
    assert keyed[("node", "pve1", "disk")] == 50.0
    # pve2 只有 cpu 樣本
    assert ("node", "pve2", "memory") not in keyed
    assert ("node", "pve2", "disk") not in keyed
    # running VM 取 cpu/memory；stopped 不取樣
    assert keyed[("vm", "100", "cpu")] == 99.0
    assert keyed[("vm", "100", "memory")] == 95.0
    assert ("vm", "101", "cpu") not in keyed


def _sample(value: float, target: str = "pve1", metric: str = "cpu") -> MetricSample:
    return MetricSample(scope="node", target=target, metric=metric, value=value)


def test_evaluate_creates_new_alert_over_threshold() -> None:
    decision = evaluate([_sample(95.0)], [], FakeConfig(), NOW)
    assert isinstance(decision, AlertDecision)
    assert len(decision.new_alerts) == 1
    assert decision.new_alerts[0].value == 95.0
    assert decision.resolved_targets == []


def test_evaluate_does_not_duplicate_open_alert() -> None:
    open_alert = FakeAlert(target="pve1", metric="cpu", created_at=NOW - timedelta(hours=1))
    decision = evaluate([_sample(96.0)], [open_alert], FakeConfig(), NOW)
    assert decision.new_alerts == []


def test_evaluate_respects_cooldown_after_resolved() -> None:
    # 10 分鐘前有同鍵事件（已 resolved），cooldown=30 → 不建新事件
    recent = FakeAlert(
        target="pve1", metric="cpu",
        created_at=NOW - timedelta(minutes=10),
        resolved_at=NOW - timedelta(minutes=5),
    )
    decision = evaluate([_sample(95.0)], [recent], FakeConfig(), NOW)
    assert decision.new_alerts == []

    # 冷卻期過（40 分鐘前）→ 建新事件
    old = FakeAlert(
        target="pve1", metric="cpu",
        created_at=NOW - timedelta(minutes=40),
        resolved_at=NOW - timedelta(minutes=35),
    )
    decision = evaluate([_sample(95.0)], [old], FakeConfig(), NOW)
    assert len(decision.new_alerts) == 1


def test_evaluate_hysteresis_resolve() -> None:
    open_alert = FakeAlert(target="pve1", metric="cpu", created_at=NOW - timedelta(hours=1))

    # 回落 87%（threshold 90，遲滯下限 85）→ 尚不 resolve
    decision = evaluate([_sample(87.0)], [open_alert], FakeConfig(), NOW)
    assert decision.resolved_targets == []

    # 回落 84% → resolve
    decision = evaluate([_sample(84.0)], [open_alert], FakeConfig(), NOW)
    assert decision.resolved_targets == [("pve1", "cpu")]


def test_evaluate_missing_sample_does_not_resolve() -> None:
    """節點短暫消失（PVE 查詢缺漏）不觸發 resolve。"""
    open_alert = FakeAlert(target="pve1", metric="cpu", created_at=NOW - timedelta(hours=1))
    decision = evaluate([], [open_alert], FakeConfig(), NOW)
    assert decision.resolved_targets == []
    assert decision.new_alerts == []
