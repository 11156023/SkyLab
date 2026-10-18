"""app/api/routes/firewall.py 路由層的單元測試（直接呼叫路由函式，service 全部替換）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routes import firewall as fw
from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.schemas.firewall import (
    ConnectionCreate,
    ConnectionDelete,
    FirewallRuleCreate,
    FirewallRuleUpdate,
    LayoutNodeUpdate,
    LayoutUpdate,
    PortSpec,
    PublishedServiceCreate,
    PublishedServiceRef,
    PublishedServiceUpdate,
)

SESSION = object()
RESOURCE = {"node": "pve1", "type": "qemu"}


def _user() -> Any:
    return SimpleNamespace(id=uuid.uuid4())


def _raiser(exc: Exception):
    def _fn(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return _fn


@pytest.fixture()
def audits(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        fw.audit_service, "log_action", lambda **kwargs: calls.append(kwargs)
    )
    return calls


@pytest.fixture()
def access(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    checked: list[Any] = []
    monkeypatch.setattr(
        fw,
        "check_firewall_access",
        lambda **kwargs: checked.append(("fw", kwargs["vmid"])),
    )
    monkeypatch.setattr(
        fw,
        "require_resource_management",
        lambda **kwargs: checked.append(("mgmt", kwargs["vmid"])),
    )
    return checked


# ─── 拓撲 ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (NotFoundError("missing"), 404),
        (BadRequestError("bad"), 400),
        (ProxmoxError("down"), 502),
        (RuntimeError("boom"), 500),
    ],
)
def test_get_topology_errors(
    monkeypatch: pytest.MonkeyPatch, exc: Exception, status: int
) -> None:
    monkeypatch.setattr(fw.firewall_service, "get_topology", _raiser(exc))
    with pytest.raises(HTTPException) as info:
        fw.get_topology(session=SESSION, current_user=_user())
    assert info.value.status_code == status


# ─── 佈局 ─────────────────────────────────────────────────────────────────────


def test_save_layout(monkeypatch: pytest.MonkeyPatch, audits: list) -> None:
    saved: dict[str, Any] = {}
    monkeypatch.setattr(
        fw.layout_repo, "upsert_layout_batch", lambda **kwargs: saved.update(kwargs)
    )
    user = _user()
    body = LayoutUpdate(
        nodes=[
            LayoutNodeUpdate(vmid=101, node_type="vm", position_x=1.0, position_y=2.0),
            LayoutNodeUpdate(
                vmid=None, node_type="gateway", position_x=3, position_y=4
            ),
        ]
    )
    result = fw.save_layout(layout_update=body, session=SESSION, current_user=user)
    assert result.message
    assert saved["user_id"] == user.id
    assert saved["nodes"][0] == {
        "vmid": 101,
        "node_type": "vm",
        "position_x": 1.0,
        "position_y": 2.0,
    }
    assert "2 nodes" in audits[0]["details"]


# ─── 連線 ─────────────────────────────────────────────────────────────────────


def test_create_connection_success(
    monkeypatch: pytest.MonkeyPatch, audits: list, access: list
) -> None:
    created: dict[str, Any] = {}
    monkeypatch.setattr(
        fw.firewall_service, "create_connection", lambda **kw: created.update(kw)
    )
    conn = ConnectionCreate(
        source_vmid=None, target_vmid=200, ports=[PortSpec(port=22)]
    )
    result = fw.create_connection(conn=conn, session=SESSION, current_user=_user())
    assert result.message
    # None（網關）那端略過檢查
    assert access == [("fw", 200)]
    assert created["target_vmid"] == 200
    assert audits[0]["vmid"] == 200


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (BadRequestError("bad"), 400),
        (NotFoundError("missing"), 400),
        (ProxmoxError("down"), 500),
    ],
)
def test_create_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
    access: list,
    exc: Exception,
    status: int,
) -> None:
    monkeypatch.setattr(fw.firewall_service, "create_connection", _raiser(exc))
    conn = ConnectionCreate(source_vmid=100, target_vmid=200, ports=[])
    with pytest.raises(HTTPException) as info:
        fw.create_connection(conn=conn, session=SESSION, current_user=_user())
    assert info.value.status_code == status
    assert access == [("fw", 100), ("fw", 200)]


def test_delete_connection_success(
    monkeypatch: pytest.MonkeyPatch, audits: list, access: list
) -> None:
    deleted: dict[str, Any] = {}
    monkeypatch.setattr(
        fw.firewall_service, "delete_connection", lambda **kw: deleted.update(kw)
    )
    conn = ConnectionDelete(source_vmid=100, target_vmid=None)
    result = fw.delete_connection(conn=conn, session=SESSION, current_user=_user())
    assert result.message
    assert deleted["source_vmid"] == 100
    assert audits[0]["vmid"] == 100


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (BadRequestError("bad"), 400),
        (NotFoundError("missing"), 400),
        (ProxmoxError("down"), 500),
    ],
)
def test_delete_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
    access: list,
    exc: Exception,
    status: int,
) -> None:
    monkeypatch.setattr(fw.firewall_service, "delete_connection", _raiser(exc))
    conn = ConnectionDelete(source_vmid=100, target_vmid=200)
    with pytest.raises(HTTPException) as info:
        fw.delete_connection(conn=conn, session=SESSION, current_user=_user())
    assert info.value.status_code == status


# ─── 單一 VM 規則 ─────────────────────────────────────────────────────────────


def test_create_rule_success(
    monkeypatch: pytest.MonkeyPatch, audits: list, access: list
) -> None:
    captured: list[Any] = []
    monkeypatch.setattr(
        fw.firewall_service,
        "create_rule",
        lambda node, vmid, vtype, rule: captured.append(rule),
    )
    rule = FirewallRuleCreate(type="in", action="ACCEPT", dport="22")
    result = fw.create_rule(
        vmid=100,
        rule=rule,
        session=SESSION,
        current_user=_user(),
        resource_info=RESOURCE,
    )
    assert result.message
    assert "source" not in captured[0]
    assert captured[0]["dport"] == "22"
    assert access == [("mgmt", 100)]


def test_create_rule_proxmox_error(
    monkeypatch: pytest.MonkeyPatch, access: list
) -> None:
    monkeypatch.setattr(fw.firewall_service, "create_rule", _raiser(ProxmoxError("x")))
    with pytest.raises(HTTPException) as info:
        fw.create_rule(
            vmid=100,
            rule=FirewallRuleCreate(type="in", action="DROP"),
            session=SESSION,
            current_user=_user(),
            resource_info=RESOURCE,
        )
    assert info.value.status_code == 500


def _rules(monkeypatch: pytest.MonkeyPatch, rules: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(
        fw.firewall_service, "get_vm_firewall_rules", lambda node, vmid, vtype: rules
    )


def test_update_rule_proxmox_error(
    monkeypatch: pytest.MonkeyPatch, access: list
) -> None:
    _rules(monkeypatch, [{"pos": 1}])
    monkeypatch.setattr(fw.firewall_service, "update_rule", _raiser(ProxmoxError("x")))
    with pytest.raises(HTTPException) as info:
        fw.update_rule(
            vmid=100,
            pos=1,
            rule=FirewallRuleUpdate(),
            session=SESSION,
            current_user=_user(),
            resource_info=RESOURCE,
        )
    assert info.value.status_code == 500


def test_delete_rule_proxmox_error(
    monkeypatch: pytest.MonkeyPatch, access: list
) -> None:
    _rules(monkeypatch, [{"pos": 2}])
    monkeypatch.setattr(
        fw.firewall_service, "delete_rule_by_pos", _raiser(ProxmoxError("x"))
    )
    with pytest.raises(HTTPException) as info:
        fw.delete_rule(
            vmid=100,
            pos=2,
            session=SESSION,
            current_user=_user(),
            resource_info=RESOURCE,
        )
    assert info.value.status_code == 500


# ─── 迷你拓撲與對外服務 ────────────────────────────────────────────────────────


def test_get_vm_topology(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fw.firewall_service, "get_vm_topology", lambda vmid, session: {"vmid": vmid}
    )
    assert fw.get_vm_topology(vmid=7, session=SESSION, _resource_info=RESOURCE) == {
        "vmid": 7
    }
    monkeypatch.setattr(
        fw.firewall_service, "get_vm_topology", _raiser(ProxmoxError("x"))
    )
    with pytest.raises(HTTPException) as info:
        fw.get_vm_topology(vmid=7, session=SESSION, _resource_info=RESOURCE)
    assert info.value.status_code == 500


def test_list_published_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fw.firewall_service, "list_vm_published_services", lambda vmid, session: []
    )
    assert (
        fw.list_published_services(vmid=7, session=SESSION, _resource_info=RESOURCE)
        == []
    )
    monkeypatch.setattr(
        fw.firewall_service, "list_vm_published_services", _raiser(ProxmoxError("x"))
    )
    with pytest.raises(HTTPException) as info:
        fw.list_published_services(vmid=7, session=SESSION, _resource_info=RESOURCE)
    assert info.value.status_code == 500


def _publish_body() -> PublishedServiceCreate:
    return PublishedServiceCreate(port=80, mode="firewall_only")


def test_publish_service_success(
    monkeypatch: pytest.MonkeyPatch, audits: list, access: list
) -> None:
    monkeypatch.setattr(
        fw.firewall_service,
        "publish_vm_service",
        lambda vmid, body, session: {"port": body.port},
    )
    result = fw.publish_service(
        vmid=7,
        body=_publish_body(),
        session=SESSION,
        current_user=_user(),
        _resource_info=RESOURCE,
    )
    assert result == {"port": 80}
    assert "80/tcp" in audits[0]["details"]


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (BadRequestError("bad"), 400),
        (NotFoundError("missing"), 400),
        (ProxmoxError("down"), 502),
    ],
)
def test_publish_service_errors(
    monkeypatch: pytest.MonkeyPatch,
    audits: list,
    access: list,
    exc: Exception,
    status: int,
) -> None:
    monkeypatch.setattr(fw.firewall_service, "publish_vm_service", _raiser(exc))
    with pytest.raises(HTTPException) as info:
        fw.publish_service(
            vmid=7,
            body=_publish_body(),
            session=SESSION,
            current_user=_user(),
            _resource_info=RESOURCE,
        )
    assert info.value.status_code == status
    assert audits == []


def _replace_body() -> PublishedServiceUpdate:
    return PublishedServiceUpdate(
        current=PublishedServiceRef(port=80),
        replacement=PublishedServiceCreate(
            port=8080, mode="port_forward", external_port=30080
        ),
    )


def test_replace_published_service_success(
    monkeypatch: pytest.MonkeyPatch, audits: list, access: list
) -> None:
    monkeypatch.setattr(
        fw.firewall_service,
        "replace_vm_service",
        lambda vmid, current, replacement, session: {"port": replacement.port},
    )
    result = fw.replace_published_service(
        vmid=7,
        body=_replace_body(),
        session=SESSION,
        current_user=_user(),
        _resource_info=RESOURCE,
    )
    assert result == {"port": 8080}
    assert "external_port=30080" in audits[0]["details"]


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (NotFoundError("missing"), 404),
        (BadRequestError("bad"), 400),
        (ProxmoxError("down"), 502),
    ],
)
def test_replace_published_service_errors(
    monkeypatch: pytest.MonkeyPatch,
    access: list,
    exc: Exception,
    status: int,
) -> None:
    monkeypatch.setattr(fw.firewall_service, "replace_vm_service", _raiser(exc))
    with pytest.raises(HTTPException) as info:
        fw.replace_published_service(
            vmid=7,
            body=_replace_body(),
            session=SESSION,
            current_user=_user(),
            _resource_info=RESOURCE,
        )
    assert info.value.status_code == status


def test_unpublish_service_success(
    monkeypatch: pytest.MonkeyPatch, audits: list, access: list
) -> None:
    removed: list[Any] = []
    monkeypatch.setattr(
        fw.firewall_service,
        "unpublish_vm_service",
        lambda vmid, body, session: removed.append(body.port),
    )
    result = fw.unpublish_service(
        vmid=7,
        body=PublishedServiceRef(port=443),
        session=SESSION,
        current_user=_user(),
        _resource_info=RESOURCE,
    )
    assert result.message
    assert removed == [443]
    assert "443/tcp" in audits[0]["details"]


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (BadRequestError("bad"), 400),
        (NotFoundError("missing"), 400),
        (ProxmoxError("down"), 502),
    ],
)
def test_unpublish_service_errors(
    monkeypatch: pytest.MonkeyPatch,
    access: list,
    exc: Exception,
    status: int,
) -> None:
    monkeypatch.setattr(fw.firewall_service, "unpublish_vm_service", _raiser(exc))
    with pytest.raises(HTTPException) as info:
        fw.unpublish_service(
            vmid=7,
            body=PublishedServiceRef(port=443),
            session=SESSION,
            current_user=_user(),
            _resource_info=RESOURCE,
        )
    assert info.value.status_code == status
