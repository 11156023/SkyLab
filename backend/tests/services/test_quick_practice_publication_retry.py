"""快速練習：對外發布失敗不再毀掉整組、修復期限從機器建好起算、開環境前先檢查發布設定。

背景：Gateway 連不上、Cloudflare 或配號池有問題時，舊行為會把整組已經建好的
機器標成失敗、15 分鐘後全部回收，學生看到的就是「機器建不起來」。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import BadRequestError
from app.models import QuickPracticeSession, Resource
from app.schemas.firewall import PublishedService
from app.services import quick_practice
from app.services.network import nat_service
from app.services.teaching import class_network_service, course_publication_service
from tests.services.test_quick_practice_coverage import _session_graph


@pytest.fixture
def qdb():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def _clear_retry_state():
    quick_practice._publication_retry_at.clear()
    yield
    quick_practice._publication_retry_at.clear()


def _stub_network(monkeypatch, *, topology=None, publication=None):
    calls = {"publish": 0}
    monkeypatch.setattr(
        class_network_service, "sync_scope_rules", lambda **_kw: list(topology or [])
    )

    def publish(*_a, **_kw):
        calls["publish"] += 1
        return list(publication() if callable(publication) else publication or [])

    monkeypatch.setattr(course_publication_service, "apply_for_machines", publish)
    return calls


# ------------------------------------------------------------ reconcile_session


def test_publication_failure_keeps_environment_ready(qdb, monkeypatch):
    practice, _requests = _session_graph(qdb, with_edge=False)
    _stub_network(monkeypatch, publication=["9100: publishing port 80 failed"])

    result = quick_practice.reconcile_session(qdb, practice_id=practice.id)

    assert result is not None
    assert result.status == "ready"
    assert result.last_error.startswith(quick_practice.PUBLICATION_PENDING_PREFIX)
    # 維持空值，lifecycle 才會再進來重試發布
    assert result.topology_applied_at is None
    assert quick_practice._publication_pending(result)


def test_publication_is_retried_after_the_interval(qdb, monkeypatch):
    practice, _requests = _session_graph(qdb, with_edge=False)
    outcomes = iter([["9100: publishing port 80 failed"], []])
    calls = _stub_network(monkeypatch, publication=lambda: next(outcomes))

    quick_practice.reconcile_session(qdb, practice_id=practice.id)
    qdb.commit()
    # 還沒到重試時間：不重連 Gateway
    quick_practice.reconcile_session(qdb, practice_id=practice.id)
    assert calls["publish"] == 1

    quick_practice._publication_retry_at[practice.id] = datetime.now(UTC) - timedelta(
        seconds=1
    )
    result = quick_practice.reconcile_session(qdb, practice_id=practice.id)
    assert calls["publish"] == 2
    assert result.status == "ready"
    assert result.last_error is None
    assert result.topology_applied_at is not None


def test_topology_failure_still_blocks_the_environment(qdb, monkeypatch):
    practice, _requests = _session_graph(qdb, with_edge=False)
    _stub_network(
        monkeypatch,
        topology=["9100: firewall configuration failed"],
        publication=["9100: publishing port 80 failed"],
    )

    result = quick_practice.reconcile_session(qdb, practice_id=practice.id)

    assert result.status == "partial_failed"
    assert "firewall" in result.last_error
    assert not quick_practice._publication_pending(result)


def test_unexpected_error_becomes_a_retryable_topology_failure(qdb, monkeypatch):
    practice, _requests = _session_graph(qdb, with_edge=False)

    def broken(_session, *, practice):
        raise RuntimeError("pve exploded")

    monkeypatch.setattr(quick_practice, "_apply_session_topology", broken)

    result = quick_practice.reconcile_session(qdb, practice_id=practice.id)

    assert result is not None
    assert result.status == "partial_failed"
    assert result.last_error == "topology failed"


# ------------------------------------------------------------ 修復期限


def _resources(db, practice, requests, *, created_at):
    for request in requests:
        db.add(
            Resource(
                vmid=request.vmid,
                user_id=practice.user_id,
                request_id=request.id,
                environment_type="快速練習",
                created_at=created_at,
            )
        )
    db.commit()


def test_repair_window_starts_when_the_last_machine_is_ready(qdb):
    practice, requests = _session_graph(qdb, with_edge=False)
    practice.created_at = datetime.now(UTC) - timedelta(hours=1)
    ready = datetime.now(UTC) - timedelta(minutes=2)
    _resources(qdb, practice, requests, created_at=ready)

    assert abs(
        quick_practice._machines_ready_at(qdb, practice) - ready
    ) < timedelta(seconds=1)


def test_repair_window_falls_back_to_session_creation(qdb):
    practice, _requests = _session_graph(qdb, with_edge=False)
    assert quick_practice._machines_ready_at(qdb, practice) == quick_practice._ensure_utc(
        practice.created_at
    )


def _run_lifecycle(qdb, monkeypatch, *, machines_ready_minutes_ago):
    practice, requests = _session_graph(qdb, with_edge=False)
    # 開了一小時，但機器排隊很久，最近才建好
    practice.created_at = datetime.now(UTC) - timedelta(hours=1)
    qdb.add(practice)
    qdb.commit()
    _resources(
        qdb,
        practice,
        requests,
        created_at=datetime.now(UTC) - timedelta(minutes=machines_ready_minutes_ago),
    )
    practice_id = practice.id
    _stub_network(monkeypatch, topology=["9100: firewall configuration failed"])
    monkeypatch.setattr("app.core.db.engine", qdb.get_bind())
    reclaimed: list[uuid.UUID] = []
    monkeypatch.setattr(
        quick_practice,
        "_queue_session_reclaim",
        lambda _session, *, practice: reclaimed.append(practice.id),
    )
    quick_practice.process_lifecycle()
    return practice_id, reclaimed


def test_recently_ready_machines_get_their_repair_window(qdb, monkeypatch):
    practice_id, reclaimed = _run_lifecycle(
        qdb, monkeypatch, machines_ready_minutes_ago=2
    )
    assert reclaimed == []
    qdb.expire_all()
    assert qdb.get(QuickPracticeSession, practice_id).status == "partial_failed"


def test_topology_still_broken_after_the_window_is_reclaimed(qdb, monkeypatch):
    practice_id, reclaimed = _run_lifecycle(
        qdb, monkeypatch, machines_ready_minutes_ago=20
    )
    assert reclaimed == [practice_id]


# ------------------------------------------------------------ 開環境前的發布檢查


def _publications(monkeypatch, *items):
    monkeypatch.setattr(
        course_publication_service,
        "list_for_version",
        lambda _session, *, version_id: list(items),
    )


def _gateway(monkeypatch, ready=True):
    config = SimpleNamespace(host="10.0.0.2", encrypted_private_key="key") if ready else None
    monkeypatch.setattr(
        "app.repositories.gateway_config.get_gateway_config", lambda _session: config
    )


def _domain(zone="zone-1"):
    return SimpleNamespace(mode="domain", protocol="tcp", zone_id=zone)


def _forward(protocol="tcp"):
    return SimpleNamespace(mode="port_forward", protocol=protocol, zone_id=None)


def _check():
    quick_practice._assert_publications_ready(None, version_id=uuid.uuid4())


def test_no_publications_needs_no_setup(monkeypatch):
    _publications(monkeypatch)
    _check()


def test_missing_gateway_blocks_launch(monkeypatch):
    _publications(monkeypatch, _forward())
    _gateway(monkeypatch, ready=False)
    with pytest.raises(BadRequestError):
        _check()


def test_domain_publication_needs_reverse_proxy_setup(monkeypatch):
    from app.services.network import reverse_proxy_service

    _publications(monkeypatch, _domain())
    _gateway(monkeypatch)
    monkeypatch.setattr(
        reverse_proxy_service,
        "get_reverse_proxy_setup_context",
        lambda _s: SimpleNamespace(enabled=False, reasons=["Cloudflare 未設定"], zones=[]),
    )
    with pytest.raises(BadRequestError):
        _check()

    monkeypatch.setattr(
        reverse_proxy_service,
        "get_reverse_proxy_setup_context",
        lambda _s: SimpleNamespace(
            enabled=True, reasons=[], zones=[SimpleNamespace(id="other-zone")]
        ),
    )
    with pytest.raises(BadRequestError):
        _check()

    monkeypatch.setattr(
        reverse_proxy_service,
        "get_reverse_proxy_setup_context",
        lambda _s: SimpleNamespace(
            enabled=True, reasons=[], zones=[SimpleNamespace(id="zone-1")]
        ),
    )
    _check()


def test_port_forward_needs_a_pool_with_enough_ports(monkeypatch):
    _publications(monkeypatch, _forward(), _forward())
    _gateway(monkeypatch)
    monkeypatch.setattr(nat_service, "count_free_external_ports", lambda _s, _p: None)
    with pytest.raises(BadRequestError):
        _check()
    monkeypatch.setattr(nat_service, "count_free_external_ports", lambda _s, _p: 1)
    with pytest.raises(BadRequestError):
        _check()
    monkeypatch.setattr(nat_service, "count_free_external_ports", lambda _s, _p: 2)
    _check()


def test_count_free_external_ports_skips_taken_and_reserved(monkeypatch):
    from app.repositories import nat_rule as nat_repo
    from app.services.network import ip_management_service

    monkeypatch.setattr(ip_management_service, "get_subnet_config", lambda _s: object())
    monkeypatch.setattr(
        ip_management_service, "get_forward_port_range", lambda _c: (30000, 30004)
    )
    monkeypatch.setattr(
        nat_repo, "taken_external_ports", lambda _s, _p, _a, _b: {30001}
    )
    assert nat_service.count_free_external_ports(None, "tcp") == 4

    monkeypatch.setattr(ip_management_service, "get_forward_port_range", lambda _c: None)
    assert nat_service.count_free_external_ports(None, "tcp") is None


# ------------------------------------------------------------ 同時發布


def test_concurrent_publication_is_not_reported_as_failure(monkeypatch):
    """另一個建機回呼已經發布好了：這邊撞上「已發布」不算失敗。"""
    from app.services.network import firewall_service

    publication = SimpleNamespace(
        node_key="web",
        mode="port_forward",
        port=80,
        protocol="tcp",
    )
    monkeypatch.setattr(
        course_publication_service,
        "list_for_version",
        lambda _session, *, version_id: [publication],
    )
    listings = iter(
        [[], [PublishedService(port=80, protocol="tcp", mode="port_forward")]]
    )
    monkeypatch.setattr(
        firewall_service, "list_vm_published_services", lambda _vmid, _s: next(listings)
    )

    def already_published(*_a, **_kw):
        raise BadRequestError("port already published")

    monkeypatch.setattr(course_publication_service, "publish_forward", already_published)

    errors = course_publication_service.apply_for_machines(
        SimpleNamespace(rollback=lambda: None),
        version_id=uuid.uuid4(),
        vmid_by_key={"web": 9100},
        owner=SimpleNamespace(id=uuid.uuid4()),
    )
    assert errors == []


# ------------------------------------------------------------ 開環境前的叢集容量檢查


def _capacity_nodes():
    from app.models import CourseEnvironmentNode

    return [
        CourseEnvironmentNode(
            id=uuid.uuid4(),
            version_id=uuid.uuid4(),
            node_key="web",
            source_type="custom",
            custom_image_ref="local:vztmpl/debian.tar.zst",
            name="Web",
            role="server",
            resource_type="lxc",
            cpu=2,
            memory_mb=2048,
            disk_gb=8,
            network="lab-net",
            sort_order=0,
        )
    ]


def test_cluster_capacity_check_uses_the_class_algorithm(monkeypatch):
    from app.services.teaching import class_capacity_service

    seen: dict = {}

    def evaluate(_session, *, nodes, student_ids):
        seen.update(nodes=nodes, student_ids=student_ids)
        return {}, {}, []

    monkeypatch.setattr(class_capacity_service, "_evaluate_cluster_capacity", evaluate)
    monkeypatch.setattr(quick_practice, "_node_disk_gb", lambda _s, _n: 20)
    user_id = uuid.uuid4()

    quick_practice._assert_cluster_capacity(None, nodes=_capacity_nodes(), user_id=user_id)

    # 整組當成「一位學生」的班級來算；磁碟用含範本下限的實際大小
    assert seen["student_ids"] == [user_id]
    assert [(m.name, m.cpu, m.disk_gb) for m in seen["nodes"]] == [("Web", 2, 20)]


def test_cluster_capacity_shortage_blocks_launch(monkeypatch):
    from app.services.teaching import class_capacity_service

    monkeypatch.setattr(
        class_capacity_service,
        "_evaluate_cluster_capacity",
        lambda _s, *, nodes, student_ids: ({}, {}, ["pve1 RAM 不足：需要 2.0 GB，可用 0.5 GB"]),
    )
    monkeypatch.setattr(quick_practice, "_node_disk_gb", lambda _s, _n: 8)
    with pytest.raises(BadRequestError) as exc:
        quick_practice._assert_cluster_capacity(
            None, nodes=_capacity_nodes(), user_id=uuid.uuid4()
        )
    assert "pve1" in str(exc.value.message if hasattr(exc.value, "message") else exc.value)
