"""Coverage tests for app.services.system.setup_service (and its package)."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.exceptions import BadRequestError
from app.services import system as system_pkg
from app.services.system import setup_service as svc


class _FakeSession:
    def __init__(self, get_result=None):
        self.get_result = get_result

    def add(self, obj):
        pass

    def commit(self):
        pass

    def refresh(self, obj):
        pass

    def get(self, model, key):
        return self.get_result


@pytest.fixture
def audits(monkeypatch):
    calls: list = []
    monkeypatch.setattr(svc.audit_service, "log_action", lambda **kw: calls.append(kw))
    return calls


@pytest.fixture
def open_state(monkeypatch):
    state = SimpleNamespace(completed=False, admin_user_id=uuid.uuid4())
    monkeypatch.setattr(svc, "ensure_setup_open", lambda *, session: state)
    return state


def test_package_getattr():
    assert system_pkg.setup_service is svc
    with pytest.raises(AttributeError):
        _ = system_pkg.does_not_exist


def test_takeover_keeps_new_full_name_and_skips_missing_default_admin(
    monkeypatch, audits, open_state
):
    monkeypatch.setattr(svc, "ensure_password_complexity", lambda _p: None)
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        email="boss@example.com",
        full_name="Old",
        is_superuser=True,
    )

    def by_email(*, session, email):
        return existing if email == existing.email else None

    monkeypatch.setattr(svc.user_repo, "get_user_by_email", by_email)
    updates: list = []

    def update_user(*, session, db_user, user_in):
        updates.append(user_in)
        db_user.full_name = user_in.full_name
        return db_user

    monkeypatch.setattr(svc.user_repo, "update_user", update_user)
    admins: list = []
    monkeypatch.setattr(
        svc.system_setup_repo,
        "set_admin_user",
        lambda *, session, user_id: admins.append(user_id),
    )
    assert existing.email != settings.FIRST_SUPERUSER

    result = svc.configure_admin(
        session=_FakeSession(),  # type: ignore[arg-type]
        data=svc.SetupAdminCreate(
            email="boss@example.com",
            full_name="New Name",
            password="Str0ng!Passw0rd",
        ),
    )
    assert result.created is False
    assert result.full_name == "New Name"
    assert result.default_admin_disabled is False
    assert updates[0].full_name == "New Name"
    assert admins == [existing.id]
    assert "took over" in audits[0]["details"]


def test_collect_storages_skips_unreachable_node(monkeypatch):
    def storages(_client, node_name):
        if node_name == "down":
            raise RuntimeError("timeout")
        return [{"storage": "local"}]

    monkeypatch.setattr(svc, "list_node_storages", storages)
    monkeypatch.setattr(
        svc.connection_sync_service, "is_usable_storage", lambda st: True
    )
    monkeypatch.setattr(
        svc.connection_sync_service,
        "storage_row_from_pve",
        lambda node, st: {
            "storage": st["storage"],
            "storage_type": "dir",
            "can_iso": True,
            "can_vm": True,
            "can_lxc": True,
            "is_shared": False,
            "avail_gb": 10.0,
        },
    )
    result = svc._collect_storages(object(), ["down", "up"])
    assert [(s.storage, s.nodes) for s in result] == [("local", ["up"])]


def _proxmox_data():
    return SimpleNamespace(
        is_default=True,
        name="lab",
        host="pve.example.com",
        port=8006,
        user="root@pam",
        password="pw",
        verify_ssl=False,
        ca_cert=None,
        api_timeout=30,
        pool_name=None,
        iso_storage=None,
        data_storage=None,
        backup_storage=None,
        task_check_interval=2,
        gateway_ip=None,
        local_subnet=None,
        default_node=None,
        enabled=True,
    )


@pytest.fixture
def proxmox_repo(monkeypatch):
    monkeypatch.setattr(svc, "validate_ca_cert_pem", lambda _pem: None)
    monkeypatch.setattr(
        svc.proxmox_connection_repo, "get_all_connections", lambda _s: []
    )
    monkeypatch.setattr(
        svc.proxmox_connection_repo,
        "create_connection",
        lambda _s, **kw: SimpleNamespace(id=1, name=kw["name"], host=kw["host"]),
    )
    invalidations: list = []
    monkeypatch.setattr(
        svc, "invalidate_proxmox_client", lambda: invalidations.append(1)
    )
    return invalidations


def test_configure_proxmox_reports_generic_sync_failure(
    monkeypatch, audits, open_state, proxmox_repo
):
    def boom(_s, _conn):
        raise RuntimeError("network")

    monkeypatch.setattr(svc.connection_sync_service, "sync_connection_inventory", boom)
    result = svc.configure_proxmox(
        session=_FakeSession(),  # type: ignore[arg-type]
        data=_proxmox_data(),  # type: ignore[arg-type]
    )
    assert result.nodes == []
    assert result.sync_error
    assert proxmox_repo == [1]
    assert "sync error" in audits[0]["details"]


def test_configure_proxmox_refreshes_client_after_sync(
    monkeypatch, audits, open_state, proxmox_repo
):
    node = SimpleNamespace(
        id=3,
        name="pve1",
        host="10.0.0.1",
        port=8006,
        is_primary=True,
        is_online=True,
        last_checked=datetime(2026, 1, 1, tzinfo=timezone.utc),
        priority=1,
        enabled=True,
    )
    monkeypatch.setattr(
        svc.connection_sync_service,
        "sync_connection_inventory",
        lambda _s, _conn: ([node], 2),
    )
    result = svc.configure_proxmox(
        session=_FakeSession(),  # type: ignore[arg-type]
        data=_proxmox_data(),  # type: ignore[arg-type]
    )
    assert [n.name for n in result.nodes] == ["pve1"]
    assert result.storage_count == 2
    assert result.sync_error is None
    assert proxmox_repo == [1, 1]


@pytest.mark.parametrize(
    "admin",
    [
        None,
        SimpleNamespace(is_active=False, is_superuser=True),
        SimpleNamespace(is_active=True, is_superuser=False),
    ],
)
def test_complete_rejects_unusable_admin(open_state, admin):
    with pytest.raises(BadRequestError):
        svc.complete(session=_FakeSession(admin))  # type: ignore[arg-type]
