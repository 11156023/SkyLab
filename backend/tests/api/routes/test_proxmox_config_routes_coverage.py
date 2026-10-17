"""Coverage tests for app/api/routes/proxmox_config.py route branches."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import proxmox_config as routes
from app.exceptions import BadRequestError
from app.schemas.proxmox_config import (
    ProxmoxConfigPublic,
    ProxmoxConfigUpdate,
    ProxmoxConnectionUpdateIn,
    ProxmoxNodeUpdate,
    ProxmoxStorageUpdate,
)

ADMIN = SimpleNamespace(id=uuid.uuid4(), is_superuser=True)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    """Minimal session whose ``exec`` returns queued result rows."""

    def __init__(self, *results):
        self._results = list(results)

    def exec(self, statement):
        return _Result(self._results.pop(0))


SESSION = _FakeSession()


def _raise(exc: BaseException):
    def raiser(*args, **kwargs):
        raise exc

    return raiser


@pytest.fixture
def audits(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        routes.audit_service, "log_action", lambda **kwargs: calls.append(kwargs)
    )
    invalidations: list[bool] = []
    monkeypatch.setattr(
        routes, "invalidate_proxmox_client", lambda: invalidations.append(True)
    )
    calls_with_invalidations = SimpleNamespace(audit=calls, invalidated=invalidations)
    return calls_with_invalidations


def _config(**overrides):
    values = ProxmoxConfigPublic(is_configured=True).model_dump()
    values.pop("is_configured")
    values.update(overrides)
    return SimpleNamespace(**values)


def _node(name: str = "pve1", node_id: int = 1):
    # no ``enabled`` attribute: _node_to_public must default it to True
    return SimpleNamespace(
        id=node_id,
        name=name,
        host="10.0.0.1",
        port=8006,
        is_primary=True,
        is_online=True,
        last_checked=None,
        priority=5,
    )


def _storage(storage_id: int = 7, node_name: str = "pve1"):
    return SimpleNamespace(
        id=storage_id,
        node_name=node_name,
        storage="ceph",
        storage_type="rbd",
        total_gb=100.0,
        used_gb=10.0,
        avail_gb=90.0,
        can_vm=True,
        can_lxc=True,
        can_iso=False,
        can_backup=False,
        is_shared=True,
        active=True,
        enabled=True,
        speed_tier="ssd",
        user_priority=5,
    )


def _conn(conn_id: int = 1, *, is_default: bool = False, name: str = "lab"):
    return SimpleNamespace(
        id=conn_id,
        name=name,
        host="pve.example.com",
        port=8006,
        user="root@pam",
        verify_ssl=False,
        ca_cert=None,
        api_timeout=30,
        is_default=is_default,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_resource_vmids_fall_back_to_recorded_when_pve_unreachable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        routes,
        "_resource_vmids_on_pve",
        _raise(routes._ResourceCheckUnavailable("unreachable")),
    )

    assert routes._resource_vmids_on_connection(
        _FakeSession([105, 101]), 1, {"pve1"}
    ) == [101, 105]

    with pytest.raises(routes._ResourceCheckUnavailable):
        routes._resource_vmids_on_connection(_FakeSession([]), 1, {"pve1"})


def test_resource_vmids_on_pve_without_nodes_is_empty() -> None:
    assert routes._resource_vmids_on_pve(SESSION, 1, set()) == []


def test_resource_vmids_on_pve_ignores_malformed_entries(monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "list_connection_vms",
        lambda connection_id: [{"name": "no-vmid"}, {"vmid": None}, {"vmid": "x"}],
    )

    assert routes._resource_vmids_on_pve(SESSION, 1, {"pve1"}) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"loadavg_warn": 1.5, "loadavg_max": 1.5, "disk_warn": 0.5, "disk_high": 0.9},
        {"loadavg_warn": 0.5, "loadavg_max": 1.5, "disk_warn": 0.9, "disk_high": 0.8},
    ],
)
def test_threshold_ordering_rejects_inverted_pairs(kwargs) -> None:
    with pytest.raises(BadRequestError):
        routes._validate_threshold_ordering(**kwargs)


# ---------------------------------------------------------------------------
# placement config
# ---------------------------------------------------------------------------


def test_get_proxmox_config(monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "proxmox_config_repo",
        SimpleNamespace(get_proxmox_config=lambda session: None),
    )
    assert routes.get_proxmox_config(SESSION, ADMIN).is_configured is False

    monkeypatch.setattr(
        routes,
        "proxmox_config_repo",
        SimpleNamespace(
            get_proxmox_config=lambda session: _config(cpu_overcommit_ratio=3.0)
        ),
    )
    result = routes.get_proxmox_config(SESSION, ADMIN)
    assert result.is_configured is True
    assert result.cpu_overcommit_ratio == 3.0


def _config_repo(monkeypatch, existing):
    upserts: list[dict] = []

    def upsert(session, **fields):
        upserts.append(fields)
        return _config(**fields)

    monkeypatch.setattr(
        routes,
        "proxmox_config_repo",
        SimpleNamespace(
            get_proxmox_config=lambda session: existing,
            upsert_proxmox_config=upsert,
        ),
    )
    return upserts


def test_update_config_creates_with_defaults(monkeypatch, audits) -> None:
    upserts = _config_repo(monkeypatch, None)

    result = routes.update_proxmox_config(
        SESSION, ADMIN, ProxmoxConfigUpdate(placement_loadavg_warn_per_core=0.5)
    )

    assert result.placement_loadavg_warn_per_core == 0.5
    assert upserts == [{"placement_loadavg_warn_per_core": 0.5}]
    assert "placement_loadavg_warn_per_core" in audits.audit[0]["details"]


def test_update_config_merges_existing_values(monkeypatch, audits) -> None:
    upserts = _config_repo(monkeypatch, _config(placement_loadavg_max_per_core=1.0))

    # warn 1.2 is valid on its own but not against the stored max of 1.0
    with pytest.raises(BadRequestError):
        routes.update_proxmox_config(
            SESSION, ADMIN, ProxmoxConfigUpdate(placement_loadavg_warn_per_core=1.2)
        )

    result = routes.update_proxmox_config(SESSION, ADMIN, ProxmoxConfigUpdate())
    assert result.is_configured is True
    assert upserts == [{}]
    assert audits.audit[0]["details"].endswith("(none)")


# ---------------------------------------------------------------------------
# nodes and storages
# ---------------------------------------------------------------------------


def test_get_nodes(monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "proxmox_node_repo",
        SimpleNamespace(get_all_nodes=lambda session: [_node("a"), _node("b", 2)]),
    )

    result = routes.get_nodes(SESSION, ADMIN)

    assert [node.name for node in result] == ["a", "b"]
    assert result[0].enabled is True


def test_update_node(monkeypatch, audits) -> None:
    updated: list[dict] = []

    def update_node(session, **kwargs):
        updated.append(kwargs)
        return _node() if kwargs["node_id"] == 1 else None

    monkeypatch.setattr(
        routes, "proxmox_node_repo", SimpleNamespace(update_node=update_node)
    )
    body = ProxmoxNodeUpdate(host="10.0.0.2", priority=3, enabled=False)

    result = routes.update_node(1, SESSION, ADMIN, body)
    assert result.name == "pve1"
    assert updated[0]["enabled"] is False
    assert "priority=3" in audits.audit[0]["details"]

    with pytest.raises(HTTPException) as exc:
        routes.update_node(99, SESSION, ADMIN, body)
    assert exc.value.status_code == 404


def test_get_storages_dedupes(monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "proxmox_storage_repo",
        SimpleNamespace(
            get_all_storages=lambda session: [_storage(1, "pve1"), _storage(2, "pve2")]
        ),
    )
    monkeypatch.setattr(
        routes,
        "proxmox_node_repo",
        SimpleNamespace(
            get_node_connection_map=lambda session: {
                "pve1": (1, "lab"),
                "pve2": (1, "lab"),
            }
        ),
    )

    result = routes.get_storages(SESSION, ADMIN)

    assert len(result) == 1
    assert result[0].node_names == ["pve1", "pve2"]


def _storage_repos(monkeypatch, *, target, update_result):
    calls: list[dict] = []

    def update_settings(session, **kwargs):
        calls.append(kwargs)
        return update_result

    monkeypatch.setattr(
        routes,
        "proxmox_storage_repo",
        SimpleNamespace(
            get_storage=lambda session, storage_id: target,
            update_storage_settings=update_settings,
        ),
    )
    monkeypatch.setattr(
        routes,
        "proxmox_node_repo",
        SimpleNamespace(
            get_node_connection_map=lambda session: {
                "pve1": (1, "lab"),
                "pve2": (1, "lab"),
                "other": (2, "other"),
            }
        ),
    )
    return calls


STORAGE_BODY = ProxmoxStorageUpdate(enabled=False, speed_tier="nvme", user_priority=2)


def test_update_storage_missing_target_is_404(monkeypatch) -> None:
    _storage_repos(monkeypatch, target=None, update_result=None)

    with pytest.raises(HTTPException) as exc:
        routes.update_storage(7, SESSION, ADMIN, STORAGE_BODY)
    assert exc.value.status_code == 404


def test_update_storage_vanished_during_update_is_404(monkeypatch) -> None:
    _storage_repos(monkeypatch, target=_storage(), update_result=None)

    with pytest.raises(HTTPException) as exc:
        routes.update_storage(7, SESSION, ADMIN, STORAGE_BODY)
    assert exc.value.status_code == 404


def test_update_storage_applies_to_cluster_peers(monkeypatch, audits) -> None:
    calls = _storage_repos(
        monkeypatch,
        target=_storage(),
        update_result=(_storage(), ["pve1", "pve2"]),
    )

    result = routes.update_storage(7, SESSION, ADMIN, STORAGE_BODY)

    assert calls[0]["peer_node_names"] == {"pve1", "pve2"}
    assert result.node_names == ["pve1", "pve2"]
    assert result.connection_name == "lab"
    assert "2 node(s)" in audits.audit[0]["details"]


# ---------------------------------------------------------------------------
# connections
# ---------------------------------------------------------------------------


def test_update_connection_missing_is_404(monkeypatch) -> None:
    monkeypatch.setattr(routes, "validate_ca_cert_pem", lambda pem: None)
    monkeypatch.setattr(
        routes,
        "proxmox_connection_repo",
        SimpleNamespace(update_connection=lambda session, cid, *, updates: None),
    )

    with pytest.raises(HTTPException) as exc:
        routes.update_connection(
            5, SESSION, ADMIN, ProxmoxConnectionUpdateIn(name="renamed")
        )
    assert exc.value.status_code == 404


def test_delete_connection_missing_is_404(monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "proxmox_connection_repo",
        SimpleNamespace(get_connection=lambda session, cid: None),
    )

    with pytest.raises(HTTPException) as exc:
        routes.delete_connection(5, SESSION, ADMIN)
    assert exc.value.status_code == 404


def test_delete_default_connection_with_others_is_rejected(monkeypatch) -> None:
    default = _conn(1, is_default=True)
    monkeypatch.setattr(
        routes,
        "proxmox_connection_repo",
        SimpleNamespace(
            get_connection=lambda session, cid: default,
            get_all_connections=lambda session: [default, _conn(2)],
        ),
    )

    with pytest.raises(BadRequestError):
        routes.delete_connection(1, SESSION, ADMIN)


def _connection_repo(monkeypatch, conn, *, password=None):
    def decrypt(c):
        if password is None:
            raise RuntimeError("cannot decrypt")
        return password

    monkeypatch.setattr(
        routes,
        "proxmox_connection_repo",
        SimpleNamespace(
            get_connection=lambda session, cid: conn,
            get_decrypted_password=decrypt,
        ),
    )


def test_test_connection_missing_is_404(monkeypatch) -> None:
    _connection_repo(monkeypatch, None)

    with pytest.raises(HTTPException) as exc:
        routes.test_connection_by_id(5, SESSION, ADMIN)
    assert exc.value.status_code == 404


def test_test_connection_failure_is_reported(monkeypatch) -> None:
    _connection_repo(monkeypatch, _conn())

    result = routes.test_connection_by_id(1, SESSION, ADMIN)

    assert result.success is False
    assert "cannot decrypt" not in result.message


def test_sync_connection_missing_is_404(monkeypatch) -> None:
    _connection_repo(monkeypatch, None)

    with pytest.raises(HTTPException) as exc:
        routes.sync_connection(5, SESSION, ADMIN)
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ValueError("no nodes reported"), "no nodes reported"),
        (RuntimeError("tls handshake failed"), None),
    ],
)
def test_sync_connection_errors(monkeypatch, audits, error, expected) -> None:
    _connection_repo(monkeypatch, _conn())
    monkeypatch.setattr(
        routes.connection_sync_service, "sync_connection_inventory", _raise(error)
    )

    result = routes.sync_connection(1, SESSION, ADMIN)

    assert result.success is False
    assert result.nodes == []
    if expected:
        assert result.error == expected
    else:
        assert "tls" not in result.error
    assert audits.audit == []


def test_sync_connection_success(monkeypatch, audits) -> None:
    _connection_repo(monkeypatch, _conn())
    monkeypatch.setattr(
        routes.connection_sync_service,
        "sync_connection_inventory",
        lambda session, conn: ([_node("a"), _node("b", 2)], 4),
    )

    result = routes.sync_connection(1, SESSION, ADMIN)

    assert result.success is True
    assert [node.name for node in result.nodes] == ["a", "b"]
    assert result.storage_count == 4
    assert audits.invalidated == [True]
    assert "2 nodes, 4 storages" in audits.audit[0]["details"]


# ---------------------------------------------------------------------------
# sync-now
# ---------------------------------------------------------------------------


def _all_connections(monkeypatch, connections):
    monkeypatch.setattr(
        routes,
        "proxmox_connection_repo",
        SimpleNamespace(
            get_all_connections=lambda session, *, enabled_only: connections
        ),
    )


def test_sync_now_without_connections(monkeypatch) -> None:
    _all_connections(monkeypatch, [])

    result = routes.sync_now(SESSION, ADMIN)

    assert result.success is False
    assert result.error


def test_sync_now_collects_partial_errors(monkeypatch, audits) -> None:
    connections = [_conn(1, name="ok"), _conn(2, name="bad"), _conn(3, name="down")]
    _all_connections(monkeypatch, connections)

    def inventory(session, conn):
        if conn.name == "bad":
            raise ValueError("bad config")
        if conn.name == "down":
            raise RuntimeError("timeout")
        return [_node("a")], 3

    monkeypatch.setattr(
        routes.connection_sync_service, "sync_connection_inventory", inventory
    )

    result = routes.sync_now(SESSION, ADMIN)

    assert result.success is True
    assert [node.name for node in result.nodes] == ["a"]
    assert result.storage_count == 3
    assert result.error.startswith("bad config；")
    assert "down" in result.error
    assert "2 errors" in audits.audit[0]["details"]


def test_sync_now_all_failed(monkeypatch, audits) -> None:
    _all_connections(monkeypatch, [_conn(1, name="bad")])
    monkeypatch.setattr(
        routes.connection_sync_service,
        "sync_connection_inventory",
        _raise(ValueError("bad config")),
    )

    result = routes.sync_now(SESSION, ADMIN)

    assert result.success is False
    assert result.error == "bad config"
    assert audits.invalidated == [True]
