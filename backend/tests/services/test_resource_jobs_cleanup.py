"""整理（services/resource、services/jobs）的行為保持測試。

- 刪除與孤兒清理共用同一份收尾流程
- jobs 的管理員判斷改用 core.permissions.is_admin
- regenerate_ssh_key 走 _current_keys／_write_keys
- resource_type／is_running／read_config 共用 helper
- 進行中刪除單的查詢集中在 _find_active
- 使用層級存取規則搬到 services/resource/access
- list_teaching_class_ids_owned_by
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

from app.exceptions import PermissionDeniedError, ProxmoxError
from app.services.jobs import jobs_service
from app.services.resource import (
    _guest_helpers,
    access,
    credentials_service,
    deletion_service,
    resource_service,
    settings_service,
)


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None




@pytest.fixture
def cleanup_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from app.services.network import (
        ip_management_service,
        nat_service,
        reverse_proxy_service,
    )

    calls: list[str] = []

    def rec(name: str, result: Any = None):
        def _fn(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            return result

        return _fn

    monkeypatch.setattr(
        reverse_proxy_service, "remove_reverse_proxy_rules_for_vmid", rec("proxy")
    )
    monkeypatch.setattr(nat_service, "remove_nat_rules_for_vmid", rec("nat"))
    monkeypatch.setattr(ip_management_service, "release_ip", rec("ip"))
    monkeypatch.setattr(
        resource_service.batch_provision_repo,
        "clear_task_vmid_references",
        rec("batch", 0),
    )
    monkeypatch.setattr(
        resource_service, "_cancel_open_spec_change_requests", rec("spec")
    )
    monkeypatch.setattr(
        resource_service, "_mark_class_machine_reclaimed", rec("class_machine")
    )
    monkeypatch.setattr(resource_service.resource_repo, "delete_resource", rec("row"))
    monkeypatch.setattr(
        resource_service, "_mark_class_reclaimed_if_empty", rec("class_empty")
    )
    monkeypatch.setattr(
        resource_service, "mark_linked_request_consumed", rec("consume")
    )
    monkeypatch.setattr(resource_service.audit_service, "log_action", rec("log"))
    return calls


_CLEANUP_ORDER = [
    "proxy",
    "nat",
    "ip",
    "batch",
    "spec",
    "class_machine",
    "row",
    "class_empty",
]


def test_orphan_cleanup_runs_shared_steps_then_consumes_request(
    monkeypatch: pytest.MonkeyPatch, cleanup_calls: list[str]
) -> None:
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: SimpleNamespace(teaching_class_id=uuid.uuid4()),
    )

    resource_service.delete_orphan_db_record(
        session=MagicMock(), vmid=301, user_id=uuid.uuid4()
    )

    assert cleanup_calls == [*_CLEANUP_ORDER, "consume", "log"]


def test_delete_consumes_request_first_then_runs_shared_steps(
    monkeypatch: pytest.MonkeyPatch, cleanup_calls: list[str]
) -> None:
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: None,
    )
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "get_status",
        lambda *a: {"status": "stopped"},
    )
    monkeypatch.setattr(
        resource_service.proxmox_service, "delete_resource", lambda *a, **k: None
    )

    resource_service.delete(
        session=MagicMock(),
        vmid=302,
        resource_info={"node": "pve1", "type": "qemu", "name": "vm"},
        user_id=uuid.uuid4(),
    )

    # 沒有班級：跳過 class_machine
    expected = [s for s in _CLEANUP_ORDER if s != "class_machine"]
    assert cleanup_calls == ["consume", *expected, "log"]


def test_cleanup_step_failure_does_not_stop_the_rest(
    monkeypatch: pytest.MonkeyPatch, cleanup_calls: list[str]
) -> None:
    from app.services.network import nat_service

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("gateway down")

    monkeypatch.setattr(nat_service, "remove_nat_rules_for_vmid", boom)

    resource_service._cleanup_after_resource_removed(
        session=MagicMock(),
        vmid=303,
        teaching_class_id=None,
        marker="m",
        log_prefix="Orphan cleanup: ",
    )

    assert "nat" not in cleanup_calls
    assert cleanup_calls[-1] == "class_empty"




@pytest.mark.parametrize(
    ("user", "expected"),
    [
        (SimpleNamespace(is_superuser=True, role="student"), True),
        (SimpleNamespace(is_superuser=False, role="admin"), True),
        (SimpleNamespace(is_superuser=False, role="teacher"), False),
        (SimpleNamespace(is_superuser=False, role="student"), False),
    ],
)
def test_sees_all_jobs_follows_permission_rules(user: Any, expected: bool) -> None:
    assert jobs_service._sees_all_jobs(user, own_only=False) is expected
    assert jobs_service._sees_all_jobs(user, own_only=True) is False


def test_ensure_owner_or_admin() -> None:
    owner = uuid.uuid4()
    admin = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="admin")
    student = SimpleNamespace(id=owner, is_superuser=False, role="student")
    other = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="student")

    jobs_service._ensure_owner_or_admin(admin, owner)  # type: ignore[arg-type]
    jobs_service._ensure_owner_or_admin(student, owner)  # type: ignore[arg-type]
    with pytest.raises(jobs_service.JobAccessDeniedError):
        jobs_service._ensure_owner_or_admin(other, owner)  # type: ignore[arg-type]



_OLD = "ssh-ed25519 AAAAOLDPLATFORMKEY skylab-old"
_USER = "ssh-ed25519 AAAAUSERKEY me@laptop"


@pytest.fixture
def key_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    db_resource = SimpleNamespace(
        ssh_public_key=_OLD, ssh_private_key_encrypted=None
    )
    monkeypatch.setattr(
        credentials_service, "_get_db_resource", lambda _s, _v: db_resource
    )
    monkeypatch.setattr(
        credentials_service,
        "generate_ed25519_keypair",
        lambda comment: ("PRIVATE-PEM", "ssh-ed25519 AAAANEWKEY skylab-new"),
    )
    monkeypatch.setattr(credentials_service.audit_service, "log_action", Mock())
    return SimpleNamespace(db_resource=db_resource)


def test_regenerate_qemu_replaces_platform_key_and_needs_reboot(
    monkeypatch: pytest.MonkeyPatch, key_env: SimpleNamespace
) -> None:
    from urllib.parse import quote

    monkeypatch.setattr(
        credentials_service.proxmox_service,
        "get_config",
        lambda node, vmid, rtype: {"sshkeys": quote(f"{_OLD}\n{_USER}", safe="")},
    )
    update_config = Mock()
    monkeypatch.setattr(
        credentials_service.proxmox_service, "update_config", update_config
    )

    res = credentials_service.regenerate_ssh_key(
        session=Mock(),
        vmid=101,
        resource_info={"node": "pve1", "type": "qemu", "status": "running"},
        user_id=uuid.uuid4(),
    )

    written = update_config.call_args.kwargs["sshkeys"]
    assert "AAAAOLDPLATFORMKEY" not in written
    assert "AAAAUSERKEY" in written and "AAAANEWKEY" in written
    assert res.applied_immediately is False
    assert res.message == credentials_service._keys_message(False)
    assert key_env.db_resource.ssh_public_key.endswith("skylab-new")


def test_regenerate_lxc_rewrites_file_and_applies_now(
    monkeypatch: pytest.MonkeyPatch, key_env: SimpleNamespace
) -> None:
    commands: list[str] = []

    def fake_exec(node: str, vmid: int, command: str, **kw: Any):
        commands.append(command)
        if command.startswith("cat "):
            return 0, f"{_OLD}\n{_USER}\n", ""
        return 0, "", ""

    monkeypatch.setattr(credentials_service.guest, "exec_lxc", fake_exec)

    res = credentials_service.regenerate_ssh_key(
        session=Mock(),
        vmid=120,
        resource_info={"node": "pve1", "type": "lxc", "status": "running"},
        user_id=uuid.uuid4(),
    )

    assert len(commands) == 2
    assert "AAAAOLDPLATFORMKEY" not in commands[1]
    assert "AAAAUSERKEY" in commands[1] and "AAAANEWKEY" in commands[1]
    assert res.applied_immediately is True
    assert res.message == credentials_service._keys_message(True)


def test_regenerate_lxc_requires_running(key_env: SimpleNamespace) -> None:
    from app.exceptions import BadRequestError

    with pytest.raises(BadRequestError):
        credentials_service.regenerate_ssh_key(
            session=Mock(),
            vmid=120,
            resource_info={"node": "pve1", "type": "lxc", "status": "stopped"},
            user_id=uuid.uuid4(),
        )




def test_guest_helpers_basics() -> None:
    assert _guest_helpers.resource_type({"type": "lxc"}) == "lxc"
    assert _guest_helpers.resource_type({"type": "qemu"}) == "qemu"
    assert _guest_helpers.resource_type({}) == "qemu"
    assert _guest_helpers.is_running({"status": "running"}) is True
    assert _guest_helpers.is_running({"status": "stopped"}) is False


def test_boot_options_read_failure_is_proxmox_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: Any) -> None:
        raise RuntimeError("pve down")

    monkeypatch.setattr(settings_service.proxmox_service, "get_config", boom)

    with pytest.raises(ProxmoxError):
        settings_service.get_boot_options(
            vmid=101, resource_info={"node": "pve1", "type": "qemu"}
        )




def test_create_deletion_request_returns_existing_active_request() -> None:
    existing = SimpleNamespace(id=uuid.uuid4(), vmid=150)
    session = Mock()
    session.exec.return_value = _Result([existing])

    got = deletion_service.create_deletion_request(
        session=session, user_id=uuid.uuid4(), vmid=150, resource_info={}
    )

    assert got is existing
    session.add.assert_not_called()
    stmt = str(session.exec.call_args.args[0])
    assert "deletion_requests.status IN" in stmt




def _student() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), email="s@example.com", is_superuser=False, role="student"
    )


def test_require_resource_use_falls_back_to_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.resource import sharing_service

    user = _student()
    owner_row = SimpleNamespace(
        teaching_class_id=None, allocation_scope="personal", user_id=uuid.uuid4()
    )
    monkeypatch.setattr(
        access.resource_repo, "get_resource_by_vmid", lambda *, session, vmid: owner_row
    )
    shared = {"value": True}
    monkeypatch.setattr(
        sharing_service,
        "user_has_share",
        lambda *, session, vmid, user_id: shared["value"],
    )

    with pytest.raises(PermissionDeniedError):
        access.require_resource_ownership(session=Mock(), user=user, vmid=7)
    access.require_resource_use(session=Mock(), user=user, vmid=7)

    shared["value"] = False
    with pytest.raises(PermissionDeniedError):
        access.require_resource_use(session=Mock(), user=user, vmid=7)


def test_require_resource_ownership_lets_owner_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _student()
    row = SimpleNamespace(
        teaching_class_id=None, allocation_scope="personal", user_id=user.id
    )
    monkeypatch.setattr(
        access.resource_repo, "get_resource_by_vmid", lambda *, session, vmid: row
    )

    access.require_resource_ownership(session=Mock(), user=user, vmid=7)


def test_api_dep_wrappers_delegate_to_the_service_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deps 只是薄包裝：權限判斷只有 services.resource.access 這一份。"""
    from app.api.deps import proxmox as proxmox_deps

    calls: list[tuple[str, Any, Any, int]] = []

    def fake_ownership(*, session: Any, user: Any, vmid: int) -> None:
        calls.append(("ownership", session, user, vmid))

    def fake_use(*, session: Any, user: Any, vmid: int) -> None:
        calls.append(("use", session, user, vmid))

    monkeypatch.setattr(proxmox_deps, "require_resource_ownership", fake_ownership)
    monkeypatch.setattr(proxmox_deps, "require_resource_use", fake_use)

    user = _student()
    session = Mock()
    proxmox_deps.check_resource_ownership(7, user, session)  # type: ignore[arg-type]
    proxmox_deps.check_resource_control_access(8, user, session)  # type: ignore[arg-type]

    assert calls == [
        ("ownership", session, user, 7),
        ("use", session, user, 8),
    ]
    # deps 不再自帶一份規則（resource_repo／authorizers 都不該留在 deps 裡）
    assert not hasattr(proxmox_deps, "resource_repo")
    assert not hasattr(proxmox_deps, "require_resource_access")


def test_batch_power_action_uses_service_use_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []

    def deny(*, session: Any, user: Any, vmid: int) -> None:
        seen.append(vmid)
        raise PermissionDeniedError("no")

    monkeypatch.setattr(resource_service, "require_resource_use", deny)
    monkeypatch.setattr(
        resource_service.proxmox_service, "find_resource", lambda vmid: {"vmid": vmid}
    )

    res = resource_service.batch_action(
        session=Mock(), vmids=[5, 6], action="start", user=_student()  # type: ignore[arg-type]
    )

    assert seen == [5, 6]
    assert res.failed == 2
    assert all(r.message == "Permission denied" for r in res.results)




def test_owned_class_ids_by_user_id_and_user() -> None:
    class_id = uuid.uuid4()
    session = Mock()
    session.exec.return_value = _Result([class_id])
    user_id = uuid.uuid4()

    assert access.list_teaching_class_ids_owned_by(
        session=session, user_id=user_id
    ) == {class_id}
    assert access.list_owned_teaching_class_ids(
        session=session, user=SimpleNamespace(id=user_id)
    ) == {class_id}
