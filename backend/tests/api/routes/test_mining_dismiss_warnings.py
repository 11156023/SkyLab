"""誤判解除 API：非致命失敗要以 ``warnings`` 回給前端，其餘欄位維持不變。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import mining_incidents as route
from app.core.config import settings
from app.models import MiningIncidentStatus
from app.schemas.mining import (
    MiningDismissRequest,
    MiningDismissResult,
    MiningIncidentPublic,
)

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


def _incident() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        vmid=101,
        user_id=uuid.uuid4(),
        node="pve1",
        resource_type="qemu",
        avg_cpu=97.5,
        window_hours=6,
        snapshot_name=None,
        status=MiningIncidentStatus.dismissed,
        detected_at=NOW,
        suspended_at=NOW,
        reviewed_by=uuid.uuid4(),
        reviewed_at=NOW,
        review_note="誤判；恢復失敗，請手動開機：PVE down",
    )


def _call(monkeypatch: pytest.MonkeyPatch, failures: list[str]) -> tuple:
    incident = _incident()
    captured: dict = {}

    def fake_dismiss(**kwargs: object) -> tuple:
        captured.update(kwargs)
        return incident, failures

    monkeypatch.setattr(route.mining_service, "dismiss_incident", fake_dismiss)
    admin = SimpleNamespace(id=uuid.uuid4())
    result = route.dismiss_incident(
        incident_id=incident.id,
        body=MiningDismissRequest(exempt=True, note="誤判"),
        session=object(),  # type: ignore[arg-type]
        current_user=admin,  # type: ignore[arg-type]
    )
    return incident, captured, admin, result


def test_dismiss_returns_warnings_from_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident, captured, admin, result = _call(
        monkeypatch, ["恢復失敗，請手動開機：PVE down"]
    )

    assert isinstance(result, MiningDismissResult)
    assert result.warnings == ["恢復失敗，請手動開機：PVE down"]
    assert captured["incident_id"] == incident.id
    assert captured["admin"] is admin
    assert captured["exempt"] is True
    assert captured["note"] == "誤判"


def test_dismiss_response_is_superset_of_incident_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """既有欄位一個都不能少，只多一個 warnings（向後相容）。"""
    incident, _, _, result = _call(monkeypatch, [])

    public = MiningIncidentPublic.model_validate(incident, from_attributes=True)
    dumped = result.model_dump()
    assert dumped.pop("warnings") == []
    assert dumped == public.model_dump()


def test_dismiss_route_declares_result_schema() -> None:
    dismiss_routes = [
        r
        for r in route.router.routes
        if getattr(r, "path", "").endswith("/{incident_id}/dismiss")
    ]
    assert len(dismiss_routes) == 1
    assert dismiss_routes[0].response_model is MiningDismissResult  # type: ignore[attr-defined]
    assert dismiss_routes[0].methods == {"POST"}  # type: ignore[attr-defined]


def _stub_dismiss(
    monkeypatch: pytest.MonkeyPatch, failures: list[str]
) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_dismiss(**kwargs: object) -> tuple:
        captured.update(kwargs)
        incident = SimpleNamespace(
            id=uuid.uuid4(),
            vmid=101,
            user_id=uuid.uuid4(),
            node="pve1",
            resource_type="qemu",
            avg_cpu=97.5,
            window_hours=6,
            snapshot_name=None,
            status=MiningIncidentStatus.dismissed,
            detected_at=datetime.now(timezone.utc),
            suspended_at=None,
            reviewed_by=None,
            reviewed_at=None,
            review_note="誤判；恢復失敗，請手動開機：PVE down",
        )
        return incident, failures

    monkeypatch.setattr(
        "app.api.routes.mining_incidents.mining_service.dismiss_incident",
        fake_dismiss,
    )
    return captured


def test_dismiss_endpoint_returns_warnings(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _stub_dismiss(monkeypatch, ["恢復失敗，請手動開機：PVE down"])
    incident_id = uuid.uuid4()

    resp = client.post(
        f"{settings.API_V1_STR}/mining-incidents/{incident_id}/dismiss",
        headers=superuser_token_headers,
        json={"exempt": False, "note": "誤判"},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["warnings"] == ["恢復失敗，請手動開機：PVE down"]
    assert data["status"] == "dismissed"
    assert captured["incident_id"] == incident_id
    assert captured["exempt"] is False
    assert captured["note"] == "誤判"


def test_dismiss_endpoint_returns_empty_warnings_on_clean_dismiss(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dismiss(monkeypatch, [])

    resp = client.post(
        f"{settings.API_V1_STR}/mining-incidents/{uuid.uuid4()}/dismiss",
        headers=superuser_token_headers,
        json={"exempt": False, "note": "誤判"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == []
    assert resp.json()["status"] == "dismissed"
