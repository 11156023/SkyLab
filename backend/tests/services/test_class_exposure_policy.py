"""老師開放給班級 → 學生發起連線的非對稱授權規則。

鎖住的行為：
- 目標有管理權直接過；沒有就看開放設定：沒開放 403、雙向 400、埠不在清單 400
- 拆連線只要管得到任一端
- 老師縮小埠清單／關閉開放時，只拆學生連進來且埠已不允許的那幾條
- 同一台機器開放給多個我在的班級時，允許的埠取聯集
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import BadRequestError, PermissionDeniedError
from app.repositories import class_exposure as exposure_repo
from app.schemas.firewall import PortSpec, TopologyEdge
from app.services.network import class_exposure_service as svc
from app.services.network import firewall_service as fw

TEACHER_VM = 100
STUDENT_VM = 210
OTHER_STUDENT_VM = 220
STUDENT = SimpleNamespace(id=uuid.uuid4(), role="student", is_superuser=False)


def _ports(*specs: str) -> list[PortSpec]:
    out = []
    for s in specs:
        port, proto = s.split("/")
        out.append(PortSpec(port=int(port), protocol=proto))
    return out


# ─── 埠比對純函式 ─────────────────────────────────────────────────────────────


def test_normalize_ports_dedupes_and_sorts() -> None:
    raw = svc.normalize_ports(_ports("443/tcp", "80/tcp", "80/tcp", "53/udp"))
    assert raw == [
        {"port": 80, "protocol": "tcp"},
        {"port": 443, "protocol": "tcp"},
        {"port": 53, "protocol": "udp"},
    ]


def test_disallowed_ports_matches_port_and_protocol() -> None:
    allowed = _ports("80/tcp", "0/icmp")
    stale = svc.disallowed_ports(_ports("80/tcp", "80/udp", "0/icmp", "22/tcp"), allowed)
    assert [(p.port, p.protocol) for p in stale] == [(80, "udp"), (22, "tcp")]


def test_merge_ports_unions_groups() -> None:
    merged = svc.merge_ports(_ports("80/tcp"), _ports("443/tcp", "80/tcp"))
    assert [(p.port, p.protocol) for p in merged] == [(80, "tcp"), (443, "tcp")]


# ─── 學生建立連線到老師機器 ───────────────────────────────────────────────────


def _allow(monkeypatch: pytest.MonkeyPatch, allowed: list[PortSpec] | None) -> None:
    monkeypatch.setattr(
        svc, "allowed_peer_ports", lambda *, session, user, target_vmid: allowed
    )


def test_peer_connection_denied_when_not_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch, None)
    with pytest.raises(PermissionDeniedError):
        svc.require_peer_connection(
            session=None, user=STUDENT, target_vmid=TEACHER_VM,  # type: ignore[arg-type]
            ports=_ports("80/tcp"), direction="one_way",
        )


def test_peer_connection_must_be_one_way(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch, _ports("80/tcp"))
    with pytest.raises(BadRequestError):
        svc.require_peer_connection(
            session=None, user=STUDENT, target_vmid=TEACHER_VM,  # type: ignore[arg-type]
            ports=_ports("80/tcp"), direction="bidirectional",
        )


def test_peer_connection_rejects_ports_outside_exposure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow(monkeypatch, _ports("80/tcp", "443/tcp"))
    with pytest.raises(BadRequestError) as exc:
        svc.require_peer_connection(
            session=None, user=STUDENT, target_vmid=TEACHER_VM,  # type: ignore[arg-type]
            ports=_ports("80/tcp", "8080/tcp"), direction="one_way",
        )
    assert "8080/tcp" in str(exc.value)


def test_peer_connection_passes_within_exposure(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow(monkeypatch, _ports("80/tcp", "443/tcp"))
    svc.require_peer_connection(
        session=None, user=STUDENT, target_vmid=TEACHER_VM,  # type: ignore[arg-type]
        ports=_ports("443/tcp"), direction="one_way",
    )


def test_connection_target_skips_peer_check_when_manageable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        svc, "require_resource_management", lambda *, session, user, vmid: None
    )
    _allow(monkeypatch, None)  # 就算沒開放，有管理權就不會走到這裡
    svc.require_connection_target(
        session=None, user=STUDENT, target_vmid=TEACHER_VM,  # type: ignore[arg-type]
        ports=_ports("22/tcp"), direction="bidirectional",
    )


def test_connection_target_falls_back_to_peer_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny(*, session: Any, user: Any, vmid: int) -> None:
        raise PermissionDeniedError("nope")

    monkeypatch.setattr(svc, "require_resource_management", deny)
    _allow(monkeypatch, _ports("80/tcp"))
    svc.require_connection_target(
        session=None, user=STUDENT, target_vmid=TEACHER_VM,  # type: ignore[arg-type]
        ports=_ports("80/tcp"), direction="one_way",
    )
    with pytest.raises(BadRequestError):
        svc.require_connection_target(
            session=None, user=STUDENT, target_vmid=TEACHER_VM,  # type: ignore[arg-type]
            ports=_ports("22/tcp"), direction="one_way",
        )


# ─── 拆連線：管任一端即可 ─────────────────────────────────────────────────────


def _manage_only(monkeypatch: pytest.MonkeyPatch, *vmids: int) -> None:
    def check(*, session: Any, user: Any, vmid: int) -> None:
        if vmid not in vmids:
            raise PermissionDeniedError("nope")

    monkeypatch.setattr(svc, "require_resource_management", check)


def test_delete_allowed_when_managing_source_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _manage_only(monkeypatch, STUDENT_VM)
    svc.require_connection_delete(
        session=None, user=STUDENT, source_vmid=STUDENT_VM, target_vmid=TEACHER_VM  # type: ignore[arg-type]
    )


def test_delete_allowed_when_managing_target_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _manage_only(monkeypatch, TEACHER_VM)
    svc.require_connection_delete(
        session=None, user=STUDENT, source_vmid=STUDENT_VM, target_vmid=TEACHER_VM  # type: ignore[arg-type]
    )


def test_delete_denied_when_managing_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    _manage_only(monkeypatch)
    with pytest.raises(PermissionDeniedError):
        svc.require_connection_delete(
            session=None, user=STUDENT, source_vmid=STUDENT_VM, target_vmid=TEACHER_VM  # type: ignore[arg-type]
        )


# ─── 老師縮小／關閉開放時拆學生的連線 ────────────────────────────────────────


def test_prune_removes_only_stale_ports_of_class_students(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class_id = uuid.uuid4()
    monkeypatch.setattr(
        svc, "_student_vmids_of_class", lambda session, cid: {STUDENT_VM}
    )
    monkeypatch.setattr(
        fw,
        "get_connections_from_rules",
        lambda vmids: [
            # 班上學生連進來：80 保留、8080 要拆
            TopologyEdge(source_vmid=STUDENT_VM, target_vmid=TEACHER_VM, ports=_ports("80/tcp", "8080/tcp")),
            # 不是班上學生的機器：不動
            TopologyEdge(source_vmid=OTHER_STUDENT_VM, target_vmid=TEACHER_VM, ports=_ports("8080/tcp")),
            # 老師自己往外連的：不動
            TopologyEdge(source_vmid=TEACHER_VM, target_vmid=STUDENT_VM, ports=_ports("22/tcp")),
        ],
    )
    deleted: list[tuple[int | None, int | None, list[str]]] = []
    monkeypatch.setattr(
        fw,
        "delete_connection",
        lambda source_vmid, target_vmid, ports=None, session=None: deleted.append(
            (source_vmid, target_vmid, [f"{p.port}/{p.protocol}" for p in ports or []])
        ),
    )

    removed = svc.prune_student_connections(
        session=None, vmid=TEACHER_VM, class_id=class_id, keep_ports=_ports("80/tcp")  # type: ignore[arg-type]
    )

    assert removed == 1
    assert deleted == [(STUDENT_VM, TEACHER_VM, ["8080/tcp"])]


def test_prune_with_no_students_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "_student_vmids_of_class", lambda session, cid: set())
    monkeypatch.setattr(fw, "get_connections_from_rules", lambda vmids: pytest.fail("不該讀規則"))
    assert svc.prune_student_connections(
        session=None, vmid=TEACHER_VM, class_id=uuid.uuid4(), keep_ports=[]  # type: ignore[arg-type]
    ) == 0


# ─── 多班級開放取聯集 ─────────────────────────────────────────────────────────


def test_exposures_for_student_union_ports_across_classes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c1, c2 = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(svc, "_active_class_ids_of_student", lambda session, uid: {c1, c2})
    monkeypatch.setattr(
        exposure_repo,
        "list_for_classes",
        lambda *, session, class_ids: [
            SimpleNamespace(resource_vmid=TEACHER_VM, class_id=c1, ports=[{"port": 80, "protocol": "tcp"}]),
            SimpleNamespace(resource_vmid=TEACHER_VM, class_id=c2, ports=[{"port": 443, "protocol": "tcp"}]),
            SimpleNamespace(resource_vmid=101, class_id=c2, ports=[{"port": 22, "protocol": "tcp"}]),
        ],
    )

    result = svc._exposures_for_student(None, STUDENT)  # type: ignore[arg-type]

    ports, classes = result[TEACHER_VM]
    assert [(p.port, p.protocol) for p in ports] == [(80, "tcp"), (443, "tcp")]
    assert classes == {c1, c2}
    assert [(p.port, p.protocol) for p in result[101][0]] == [(22, "tcp")]
