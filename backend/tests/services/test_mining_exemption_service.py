"""mining_service.set_exemption：資源挖礦偵測豁免的設定／解除（mock DB）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import NotFoundError
from app.models import AuditAction
from app.services.security import mining_service


class _FakeSession:
    def __init__(self) -> None:
        self.added: list = []
        self.commits = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1


def _patch(
    monkeypatch: pytest.MonkeyPatch, resource: SimpleNamespace | None
) -> list[dict]:
    audits: list[dict] = []
    monkeypatch.setattr(
        mining_service.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: resource,
    )
    monkeypatch.setattr(
        mining_service.audit_service,
        "log_action",
        lambda **kwargs: audits.append(kwargs),
    )
    return audits


@pytest.mark.parametrize(
    ("initial", "exempt", "word"),
    [(False, True, "granted"), (True, False, "revoked")],
)
def test_set_exemption_updates_resource_and_audits(
    monkeypatch: pytest.MonkeyPatch, initial: bool, exempt: bool, word: str
) -> None:
    resource = SimpleNamespace(vmid=205, mining_exempt=initial)
    audits = _patch(monkeypatch, resource)
    session = _FakeSession()
    admin = SimpleNamespace(id=uuid.uuid4())

    result = mining_service.set_exemption(
        session=session, vmid=205, exempt=exempt, admin=admin  # type: ignore[arg-type]
    )

    assert result is resource
    assert resource.mining_exempt is exempt
    assert session.added == [resource]
    assert session.commits == 1
    assert len(audits) == 1
    audit = audits[0]
    assert audit["action"] is AuditAction.mining_exempt_change
    assert audit["user_id"] == admin.id
    assert audit["vmid"] == 205
    assert audit["commit"] is False
    assert word in audit["details"]


def test_set_exemption_missing_resource_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audits = _patch(monkeypatch, None)
    session = _FakeSession()

    with pytest.raises(NotFoundError):
        mining_service.set_exemption(
            session=session,  # type: ignore[arg-type]
            vmid=999,
            exempt=True,
            admin=SimpleNamespace(id=uuid.uuid4()),  # type: ignore[arg-type]
        )

    assert audits == []
    assert session.commits == 0
