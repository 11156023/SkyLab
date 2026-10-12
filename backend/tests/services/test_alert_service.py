"""警告評估純函式測試（不依賴 PVE/DB/SMTP）。"""

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

    # 回落 84% → resolve（鍵為 (connection_id, target, metric)）
    decision = evaluate([_sample(84.0)], [open_alert], FakeConfig(), NOW)
    assert decision.resolved_targets == [(None, "pve1", "cpu")]


def test_evaluate_missing_sample_does_not_resolve() -> None:
    """節點短暫消失（PVE 查詢缺漏）不觸發 resolve。"""
    open_alert = FakeAlert(target="pve1", metric="cpu", created_at=NOW - timedelta(hours=1))
    decision = evaluate([], [open_alert], FakeConfig(), NOW)
    assert decision.resolved_targets == []
    assert decision.new_alerts == []


def test_collect_samples_skips_offline_node() -> None:
    """離線節點沒有 cpu 欄位，不能補成 0% 樣本把開著的 CPU 警告收掉。"""
    nodes = [
        {"node": "pve1", "status": "offline"},
        {"node": "pve2", "status": "online", "cpu": 0.2, "maxcpu": 4},
    ]
    samples = collect_samples(nodes, [])
    assert {(s.target, s.metric) for s in samples} == {("pve2", "cpu")}

    open_alert = FakeAlert(
        target="pve1", metric="cpu", created_at=NOW - timedelta(hours=1)
    )
    decision = evaluate(samples, [open_alert], FakeConfig(), NOW)
    assert decision.resolved_targets == []


# ─── 多叢集：告警以 (連線, 目標, 指標) 為鍵 ───────────────────────────


class ConnAlert(FakeAlert):
    def __init__(self, *, connection_id: int | None, scope: str = "vm", **kw) -> None:
        super().__init__(**kw)
        self.connection_id = connection_id
        self.scope = scope


def _vm(value: float, connection_id: int | None, vmid: str = "100") -> MetricSample:
    return MetricSample(
        scope="vm", target=vmid, metric="cpu", value=value, connection_id=connection_id
    )


def test_collect_samples_tags_connection_by_node() -> None:
    nodes = [
        {"node": "a1", "cpu": 0.5, "maxcpu": 4},
        {"node": "orphan", "cpu": 0.5, "maxcpu": 4},
    ]
    resources = [
        {"vmid": 100, "node": "a1", "status": "running", "cpu": 0.9},
        {"vmid": 100, "node": "b1", "status": "running", "cpu": 0.1},
    ]
    samples = collect_samples(nodes, resources, {"a1": 1, "b1": 2, "orphan": None})
    keyed = {(s.connection_id, s.scope, s.target, s.metric) for s in samples}
    assert (1, "node", "a1", "cpu") in keyed
    assert (None, "node", "orphan", "cpu") in keyed
    # 同號 VM 在兩個叢集各自成一個樣本
    assert (1, "vm", "100", "cpu") in keyed
    assert (2, "vm", "100", "cpu") in keyed


def test_same_vmid_in_other_cluster_does_not_resolve_or_suppress() -> None:
    open_alert = ConnAlert(
        connection_id=1, target="100", metric="cpu", created_at=NOW - timedelta(hours=1)
    )
    # 叢集 2 的 100 號低用量：不能把叢集 1 的告警解除
    decision = evaluate([_vm(95.0, 1), _vm(10.0, 2)], [open_alert], FakeConfig(), NOW)
    assert decision.resolved_targets == []
    assert decision.new_alerts == []

    # 叢集 2 的 100 號也爆了：要另開一筆，不能被叢集 1 的告警擋掉
    decision = evaluate([_vm(95.0, 1), _vm(96.0, 2)], [open_alert], FakeConfig(), NOW)
    assert [(s.connection_id, s.target) for s in decision.new_alerts] == [(2, "100")]

    # 叢集 1 回落：只解除叢集 1 那一筆
    decision = evaluate([_vm(10.0, 1), _vm(96.0, 2)], [open_alert], FakeConfig(), NOW)
    assert decision.resolved_targets == [(1, "100", "cpu")]


def test_cooldown_is_per_connection() -> None:
    recent = ConnAlert(
        connection_id=1,
        target="100",
        metric="cpu",
        created_at=NOW - timedelta(minutes=10),
        resolved_at=NOW - timedelta(minutes=5),
    )
    decision = evaluate([_vm(95.0, 1), _vm(95.0, 2)], [recent], FakeConfig(), NOW)
    assert [s.connection_id for s in decision.new_alerts] == [2]


def test_null_connection_alert_matches_null_connection_sample() -> None:
    legacy = ConnAlert(
        connection_id=None,
        scope="node",
        target="pve1",
        metric="cpu",
        created_at=NOW - timedelta(hours=1),
    )
    decision = evaluate([_sample(95.0)], [legacy], FakeConfig(), NOW)
    assert decision.new_alerts == []
    decision = evaluate([_sample(10.0)], [legacy], FakeConfig(), NOW)
    assert decision.resolved_targets == [(None, "pve1", "cpu")]


def test_legacy_null_alert_still_resolves_once_samples_carry_connection() -> None:
    """升級前對不到連線的告警：樣本帶了連線後仍會解除，也不會再開重複的一筆。"""
    legacy = ConnAlert(
        connection_id=None, target="100", metric="cpu", created_at=NOW - timedelta(hours=1)
    )
    decision = evaluate([_vm(95.0, 1)], [legacy], FakeConfig(), NOW)
    assert decision.new_alerts == []
    assert decision.resolved_targets == []

    decision = evaluate([_vm(10.0, 1)], [legacy], FakeConfig(), NOW)
    assert decision.resolved_targets == [(None, "100", "cpu")]

    # 同號 VM 在另一個叢集仍超標：舊告警分不出是哪一台，先不解除
    decision = evaluate([_vm(10.0, 1), _vm(95.0, 2)], [legacy], FakeConfig(), NOW)
    assert decision.resolved_targets == []
    assert decision.new_alerts == []


def test_inactive_vm_targets_are_per_connection() -> None:
    from app.services.monitoring.alert_service import inactive_vm_targets

    resources = [
        {"vmid": 100, "node": "a1", "status": "running"},
        {"vmid": 100, "node": "b1", "status": "stopped"},
    ]
    node_connections = {"a1": 1, "b1": 2}
    open_targets = {(1, "100"), (2, "100"), (3, "100"), (None, "100")}
    assert inactive_vm_targets(
        resources,
        open_targets,
        complete=True,
        node_connections=node_connections,
    ) == {(2, "100"), (3, "100")}

    decision = evaluate(
        [],
        [
            ConnAlert(connection_id=1, target="100", metric="cpu", created_at=NOW),
            ConnAlert(connection_id=2, target="100", metric="cpu", created_at=NOW),
        ],
        FakeConfig(),
        NOW,
        inactive_targets={(2, "100")},
    )
    assert decision.resolved_targets == [(2, "100", "cpu")]
