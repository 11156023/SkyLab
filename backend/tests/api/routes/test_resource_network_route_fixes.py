"""稽核修正的路由回歸測試（基礎設施／資源／網路路由）。

以 dependency override + monkeypatch 隔離 DB 與 PVE：這些案例只驗證路由層
的判斷順序與參數約束，不需要真實資料庫或 Proxmox。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.infrastructure.proxmox as proxmox_infra
from app.api.deps.auth import get_current_active_superuser, get_current_user
from app.api.deps.database import get_db
from app.api.routes import batch_provision as batch_provision_route
from app.api.routes import proxmox_config as proxmox_config_route
from app.api.routes import reverse_proxy as reverse_proxy_route
from app.api.routes import templates as templates_route
from app.core.config import settings
from app.exceptions import (
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
)
from app.infrastructure.proxmox import operations as proxmox_operations
from app.main import app
from app.models import ResourceQuota, TemplateAttachment, User, UserRole
from app.schemas import SpecChangeRequestsPublic
from app.services.resource import deletion_service
from app.services.user import audit_service

API = settings.API_V1_STR


class _FakeSession:
    """只提供路由會碰到的最小介面。"""

    def __init__(self, objects: dict[type, Any] | None = None) -> None:
        self.objects = objects or {}
        self.exec_rows: list[Any] = []
        self.committed = False

    def get(self, model: type, _key: Any) -> Any:
        return self.objects.get(model)

    def exec(self, _stmt: Any) -> Any:
        return SimpleNamespace(all=lambda: list(self.exec_rows))

    def add(self, _obj: Any) -> None:
        return None

    def commit(self) -> None:
        self.committed = True

    def refresh(self, _obj: Any) -> None:
        return None

    def rollback(self) -> None:
        return None


def _user(*, superuser: bool = False) -> User:
    return User(
        id=uuid.uuid4(),
        email=f"b2-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        role=UserRole.admin if superuser else UserRole.student,
    )


@pytest.fixture()
def api_client() -> Iterator[TestClient]:
    """不進入 lifespan 的 client（避免連 Redis / 啟動排程器）。"""
    yield TestClient(app)


@pytest.fixture()
def fake_session() -> Iterator[_FakeSession]:
    session = _FakeSession()
    app.dependency_overrides[get_db] = lambda: session
    yield session
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def as_user(fake_session: _FakeSession) -> Iterator[User]:
    user = _user()
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def as_admin(fake_session: _FakeSession) -> Iterator[User]:
    admin = _user(superuser=True)
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_current_active_superuser] = lambda: admin
    yield admin
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_active_superuser, None)


@pytest.fixture(autouse=True)
def _silence_audit(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        audit_service, "log_action", lambda **kwargs: calls.append(kwargs)
    )
    return calls


# ---------------------------------------------------------------------------
# DELETE /resources/{vmid} 不可在連線停用／斷線時清掉活機器的 DB 記錄
# ---------------------------------------------------------------------------


class _FakePve:
    def __init__(self, vms: list[dict] | Exception) -> None:
        self._vms = vms
        self.cluster = SimpleNamespace(
            resources=SimpleNamespace(get=self._get)
        )

    def _get(self, **_kwargs: Any) -> list[dict]:
        if isinstance(self._vms, Exception):
            raise self._vms
        return self._vms


@pytest.fixture()
def orphan_delete_setup(
    monkeypatch: pytest.MonkeyPatch, as_user: User
) -> dict[str, Any]:
    state: dict[str, Any] = {"orphan_calls": [], "clients": {}}
    monkeypatch.setattr(
        deletion_service.resource_repo,
        "get_resource_by_vmid",
        lambda **_kw: SimpleNamespace(vmid=205, user_id=as_user.id),
    )
    monkeypatch.setattr(
        deletion_service, "can_bypass_resource_ownership", lambda _u: False
    )
    monkeypatch.setattr(
        deletion_service, "require_resource_management", lambda **_kw: None
    )

    def _not_found(vmid: int) -> dict:
        raise NotFoundError(f"Resource {vmid} not found")

    monkeypatch.setattr(deletion_service.proxmox_service, "find_resource", _not_found)
    monkeypatch.setattr(
        deletion_service.proxmox_connection_repo,
        "get_all_connections",
        lambda _session: [
            SimpleNamespace(id=1, enabled=True),
            SimpleNamespace(id=2, enabled=False),
        ],
    )
    # 逐連線查詢走 operations.find_vmid_on_connections → list_connection_vms
    # → operations.get_proxmox_api，換成假 client。
    def _api(cid: int | None) -> _FakePve:
        return _FakePve(state["clients"][cid])

    monkeypatch.setattr(deletion_service.proxmox_service, "get_proxmox_api", _api)
    monkeypatch.setattr(
        deletion_service.resource_service,
        "delete_orphan_db_record",
        lambda **kw: state["orphan_calls"].append(kw),
    )
    return state


def test_delete_keeps_db_record_when_a_connection_is_unreachable(
    api_client: TestClient, orphan_delete_setup: dict[str, Any]
) -> None:
    orphan_delete_setup["clients"] = {1: [], 2: RuntimeError("connection refused")}

    response = api_client.delete(f"{API}/resources/205")

    assert response.status_code == 502
    assert orphan_delete_setup["orphan_calls"] == []
    # 訊息必須是翻譯過的文字，不能退回成 i18n key 本身
    assert not response.json()["detail"].startswith("resource.")


def test_delete_keeps_db_record_when_vm_is_on_disabled_connection(
    api_client: TestClient, orphan_delete_setup: dict[str, Any]
) -> None:
    # 連線 2 已停用（find_resource 看不到），但機器其實還在上面
    orphan_delete_setup["clients"] = {
        1: [],
        2: [{"vmid": 205, "node": "n2", "type": "qemu"}],
    }

    response = api_client.delete(f"{API}/resources/205")

    assert response.status_code == 409
    assert orphan_delete_setup["orphan_calls"] == []
    # 訊息必須是翻譯過的文字，不能退回成 i18n key 本身
    assert not response.json()["detail"].startswith("resource.")


def test_delete_cleans_orphan_only_when_every_connection_confirms_absence(
    api_client: TestClient, orphan_delete_setup: dict[str, Any]
) -> None:
    orphan_delete_setup["clients"] = {1: [{"vmid": 101}], 2: []}

    response = api_client.delete(f"{API}/resources/205")

    assert response.status_code == 202
    assert response.json()["status"] == "completed"
    assert len(orphan_delete_setup["orphan_calls"]) == 1


# ---------------------------------------------------------------------------
# 刪除 PVE 連線前要直接詢問該連線本身（含停用中、連不上）
# ---------------------------------------------------------------------------


@pytest.fixture()
def connection_delete_setup(
    monkeypatch: pytest.MonkeyPatch, as_admin: User, fake_session: _FakeSession
) -> dict[str, Any]:
    state: dict[str, Any] = {"deleted": [], "client": [], "queried": []}
    conn = SimpleNamespace(
        id=7, name="lab-b", host="10.0.0.2", is_default=False, enabled=False
    )
    other = SimpleNamespace(id=1, name="lab-a", host="10.0.0.1", is_default=True)
    monkeypatch.setattr(
        proxmox_config_route.proxmox_connection_repo,
        "get_connection",
        lambda _s, cid: conn if cid == 7 else None,
    )
    monkeypatch.setattr(
        proxmox_config_route.proxmox_connection_repo,
        "get_all_connections",
        lambda _s, **_kw: [other, conn],
    )
    monkeypatch.setattr(
        proxmox_config_route.proxmox_connection_repo,
        "delete_connection",
        lambda _s, cid: state["deleted"].append(cid),
    )
    monkeypatch.setattr(
        proxmox_config_route.proxmox_node_repo,
        "get_all_nodes",
        lambda _s, **_kw: [SimpleNamespace(name="n1")],
    )
    monkeypatch.setattr(
        proxmox_config_route.proxmox_storage_repo,
        "upsert_storages",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        proxmox_config_route, "invalidate_proxmox_client", lambda *_a, **_kw: None
    )

    def _api(cid: int) -> _FakePve:
        state["queried"].append(cid)
        return _FakePve(state["client"])

    # 路由經 operations.list_connection_vms 查詢，operations 自己匯入的
    # get_proxmox_api 才是實際呼叫點；套件層級的名稱一併換掉，涵蓋路由內
    # 直接 import 的寫法。
    monkeypatch.setattr(proxmox_operations, "get_proxmox_api", _api)
    monkeypatch.setattr(proxmox_infra, "get_proxmox_api", _api)
    return state


def test_delete_disabled_connection_with_resources_is_blocked(
    api_client: TestClient,
    connection_delete_setup: dict[str, Any],
    fake_session: _FakeSession,
) -> None:
    connection_delete_setup["client"] = [{"vmid": 301, "node": "n1"}]
    fake_session.exec_rows = [301]

    response = api_client.delete(f"{API}/proxmox-config/connections/7")

    assert response.status_code == 400
    assert "301" in response.json()["detail"]
    assert connection_delete_setup["queried"] == [7]
    assert connection_delete_setup["deleted"] == []


def test_delete_unreachable_connection_is_blocked(
    api_client: TestClient, connection_delete_setup: dict[str, Any]
) -> None:
    connection_delete_setup["client"] = RuntimeError("timed out")

    response = api_client.delete(f"{API}/proxmox-config/connections/7")

    # 503（不是「確認仍有機器」的 400）：前端據此才提供強制刪除
    assert response.status_code == 503
    assert not response.json()["detail"].startswith("proxmoxConfig.")
    assert connection_delete_setup["deleted"] == []


def test_force_delete_unreachable_connection_is_audited(
    api_client: TestClient,
    connection_delete_setup: dict[str, Any],
    _silence_audit: list[dict[str, Any]],
) -> None:
    connection_delete_setup["client"] = RuntimeError("timed out")

    response = api_client.delete(
        f"{API}/proxmox-config/connections/7", params={"force": "true"}
    )

    assert response.status_code == 200
    assert connection_delete_setup["deleted"] == [7]
    assert "forced" in _silence_audit[-1]["details"]


def test_force_does_not_bypass_confirmed_resources(
    api_client: TestClient,
    connection_delete_setup: dict[str, Any],
    fake_session: _FakeSession,
) -> None:
    connection_delete_setup["client"] = [{"vmid": 301, "node": "n1"}]
    fake_session.exec_rows = [301]

    response = api_client.delete(
        f"{API}/proxmox-config/connections/7", params={"force": "true"}
    )

    assert response.status_code == 400
    assert connection_delete_setup["deleted"] == []


# ---------------------------------------------------------------------------
# 更新反向代理規則要先驗證，驗證失敗不可撤下原本正常的規則
# ---------------------------------------------------------------------------


@pytest.fixture()
def rp_update_setup(
    monkeypatch: pytest.MonkeyPatch, as_user: User
) -> dict[str, Any]:
    state: dict[str, Any] = {"unpublished": [], "replaced": [], "published": []}
    rule = SimpleNamespace(
        id=uuid.uuid4(), vmid=120, internal_port=8080, domain="a.example.edu"
    )
    state["rule"] = rule
    monkeypatch.setattr(reverse_proxy_route.rp_repo, "get_rule", lambda _s, _id: rule)
    monkeypatch.setattr(
        reverse_proxy_route, "check_firewall_access", lambda **_kw: None
    )
    monkeypatch.setattr(
        reverse_proxy_route.cloudflare_service,
        "get_zone",
        lambda **_kw: SimpleNamespace(name="example.edu"),
    )
    monkeypatch.setattr(
        reverse_proxy_route.reverse_proxy_service,
        "assert_domain_available",
        lambda *_a, **_kw: None,
    )
    fw = reverse_proxy_route.firewall_service
    monkeypatch.setattr(
        fw, "unpublish_vm_service", lambda *a: state["unpublished"].append(a)
    )
    monkeypatch.setattr(
        fw, "replace_vm_service", lambda *a: state["replaced"].append(a)
    )
    monkeypatch.setattr(
        fw, "publish_vm_service", lambda *a: state["published"].append(a)
    )
    monkeypatch.setattr(fw, "list_vm_published_services", lambda *_a: [])
    monkeypatch.setattr(fw, "_get_publishable_vm_ip", lambda *_a: "10.0.0.9")
    return state


def _rp_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "vmid": 120,
        "zone_id": "zone-1",
        "hostname_prefix": "b",
        "internal_port": 8080,
        "enable_https": True,
    }
    body.update(overrides)
    return body


def test_update_rule_to_taken_domain_keeps_existing_rule(
    api_client: TestClient,
    rp_update_setup: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _taken(*_a: Any, **_kw: Any) -> None:
        raise BadRequestError("b.example.edu is taken")

    monkeypatch.setattr(
        reverse_proxy_route.reverse_proxy_service, "assert_domain_available", _taken
    )

    response = api_client.put(
        f"{API}/reverse-proxy/rules/{rp_update_setup['rule'].id}", json=_rp_body()
    )

    assert response.status_code == 400
    assert rp_update_setup["unpublished"] == []
    assert rp_update_setup["replaced"] == []


def test_update_rule_with_unknown_zone_keeps_existing_rule(
    api_client: TestClient,
    rp_update_setup: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _no_zone(**_kw: Any) -> None:
        raise NotFoundError("zone not found")

    monkeypatch.setattr(reverse_proxy_route.cloudflare_service, "get_zone", _no_zone)

    response = api_client.put(
        f"{API}/reverse-proxy/rules/{rp_update_setup['rule'].id}", json=_rp_body()
    )

    assert response.status_code == 404
    assert rp_update_setup["unpublished"] == []
    assert rp_update_setup["replaced"] == []


def test_update_rule_to_vm_without_ip_keeps_existing_rule(
    api_client: TestClient,
    rp_update_setup: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reverse_proxy_route.firewall_service, "_get_publishable_vm_ip", lambda *_a: None
    )

    response = api_client.put(
        f"{API}/reverse-proxy/rules/{rp_update_setup['rule'].id}",
        json=_rp_body(vmid=121),
    )

    assert response.status_code == 400
    assert rp_update_setup["unpublished"] == []


def test_update_rule_same_vm_uses_validated_replace(
    api_client: TestClient, rp_update_setup: dict[str, Any]
) -> None:
    response = api_client.put(
        f"{API}/reverse-proxy/rules/{rp_update_setup['rule'].id}",
        json=_rp_body(internal_port=9090),
    )

    assert response.status_code == 200
    assert rp_update_setup["unpublished"] == []
    (vmid, current, replacement, _session), = rp_update_setup["replaced"]
    assert vmid == 120
    assert (current.port, current.protocol) == (8080, "tcp")
    assert replacement.port == 9090
    assert replacement.mode == "domain"
    assert replacement.domain == "b.example.edu"


# ---------------------------------------------------------------------------
# 附件上傳先驗權限、只讀上限 +1 bytes
# ---------------------------------------------------------------------------


@pytest.fixture()
def attachment_setup(
    monkeypatch: pytest.MonkeyPatch, as_user: User
) -> dict[str, Any]:
    state: dict[str, Any] = {"added": []}
    template = SimpleNamespace(id=uuid.uuid4(), owner_id=as_user.id)
    state["template"] = template
    monkeypatch.setattr(
        templates_route.template_service, "get_or_404", lambda _s, _id: template
    )
    monkeypatch.setattr(
        templates_route.template_service, "_require_owner", lambda _u, _t: None
    )
    monkeypatch.setattr(templates_route.template_files, "ATTACHMENT_MAX_BYTES", 10)

    def _add(**kw: Any) -> TemplateAttachment:
        state["added"].append(kw)
        return TemplateAttachment(
            template_id=template.id,
            filename=kw["filename"],
            content_type=kw["content_type"],
            size_bytes=len(kw["data"]),
        )

    monkeypatch.setattr(templates_route.template_service, "add_attachment", _add)
    return state


def test_attachment_upload_by_non_owner_is_rejected_before_storing(
    api_client: TestClient,
    attachment_setup: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _deny(_u: Any, _t: Any) -> None:
        raise PermissionDeniedError("not owner")

    monkeypatch.setattr(templates_route.template_service, "_require_owner", _deny)

    response = api_client.post(
        f"{API}/templates/{attachment_setup['template'].id}/attachments",
        files={"file": ("manual.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 403
    assert attachment_setup["added"] == []


def test_attachment_upload_over_limit_is_rejected(
    api_client: TestClient, attachment_setup: dict[str, Any]
) -> None:
    response = api_client.post(
        f"{API}/templates/{attachment_setup['template'].id}/attachments",
        files={"file": ("manual.txt", b"x" * 25, "text/plain")},
    )

    assert response.status_code == 400
    assert attachment_setup["added"] == []


def test_attachment_upload_at_limit_succeeds(
    api_client: TestClient, attachment_setup: dict[str, Any]
) -> None:
    response = api_client.post(
        f"{API}/templates/{attachment_setup['template'].id}/attachments",
        files={"file": ("manual.txt", b"x" * 10, "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["size_bytes"] == 10
    assert len(attachment_setup["added"][0]["data"]) == 10


# ---------------------------------------------------------------------------
# 規格變更清單的 skip/limit 要有界
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query", ["skip=-1", "limit=-1", "limit=0", "limit=1000"]
)
def test_spec_change_list_rejects_out_of_range_paging(
    api_client: TestClient, as_admin: User, query: str
) -> None:
    assert api_client.get(f"{API}/spec-change-requests/my?{query}").status_code == 422
    assert api_client.get(f"{API}/spec-change-requests/?{query}").status_code == 422


def test_spec_change_list_default_paging_still_works(
    api_client: TestClient, as_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.routes import spec_change_requests as scr_route

    seen: dict[str, Any] = {}

    def _list(**kw: Any) -> SpecChangeRequestsPublic:
        seen.update(kw)
        return SpecChangeRequestsPublic(data=[], count=0)

    monkeypatch.setattr(scr_route.spec_change_service, "list_by_user", _list)

    response = api_client.get(f"{API}/spec-change-requests/my?limit=100")

    assert response.status_code == 200
    assert (seen["skip"], seen["limit"]) == (0, 100)


# ---------------------------------------------------------------------------
# PUT /quotas/{id} 明確送 null 不可寫進 NOT NULL 欄位
# ---------------------------------------------------------------------------


def test_update_quota_ignores_explicit_null(
    api_client: TestClient, as_admin: User, fake_session: _FakeSession
) -> None:
    quota = ResourceQuota(
        user_id=uuid.uuid4(),
        max_cpu_cores=8,
        max_memory_mb=16384,
        max_disk_gb=100,
        max_instances=5,
    )
    fake_session.objects[ResourceQuota] = quota

    response = api_client.put(
        f"{API}/quotas/{quota.id}",
        json={"max_cpu_cores": None, "max_instances": 3},
    )

    assert response.status_code == 200
    assert quota.max_cpu_cores == 8
    assert quota.max_instances == 3
    assert response.json()["max_cpu_cores"] == 8


# ---------------------------------------------------------------------------
# 非正規的時區字串要回 400 而不是 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["../etc", "/etc/localtime", "Not/AZone"])
def test_invalid_timezone_maps_to_bad_request(value: str) -> None:
    from app.services.vm import vm_request_availability_service as svc

    with pytest.raises(BadRequestError):
        svc._resolve_timezone(value)


# ---------------------------------------------------------------------------
# recurrence-preview 的 count 要有上限
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 51, 100000000])
def test_recurrence_preview_rejects_out_of_range_count(
    api_client: TestClient, as_admin: User, count: int
) -> None:
    response = api_client.get(
        f"{API}/batch-provision/{uuid.uuid4()}/recurrence-preview?count={count}"
    )
    assert response.status_code == 422


def test_recurrence_preview_default_count_still_works(
    api_client: TestClient, as_admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        batch_provision_route.bp_repo,
        "get_job",
        lambda **_kw: SimpleNamespace(
            recurrence_rule=None, recurrence_duration_minutes=None
        ),
    )

    response = api_client.get(
        f"{API}/batch-provision/{uuid.uuid4()}/recurrence-preview?count=5"
    )

    assert response.status_code == 200
    assert response.json() == {"windows": []}
