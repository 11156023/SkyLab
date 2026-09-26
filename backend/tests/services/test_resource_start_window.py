"""個人申請機器的核准使用時段：start_window_state 與開機檢查共用同一份判斷。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.i18n import t
from app.exceptions import BadRequestError
from app.services.resource import resource_service

WINDOW_START = datetime(2026, 9, 2, 9, 10, tzinfo=UTC)
WINDOW_END = datetime(2026, 9, 9, 15, 59, 59, tzinfo=UTC)


def _patch(monkeypatch, *, now, request, resource=None):
    monkeypatch.setattr(resource_service, "_utc_now", lambda: now)
    monkeypatch.setattr(
        resource_service.vm_request_repo,
        "get_latest_approved_vm_request_by_vmid",
        lambda **_kwargs: request,
    )
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda **_kwargs: resource or SimpleNamespace(teaching_class_id=None),
    )


def _request():
    return SimpleNamespace(start_at=WINDOW_START, end_at=WINDOW_END)


@pytest.mark.parametrize(
    ("now", "reason"),
    [
        (datetime(2026, 9, 1, 0, 0, tzinfo=UTC), "window_not_started"),
        (datetime(2026, 9, 5, 0, 0, tzinfo=UTC), None),
        (datetime(2026, 9, 26, 4, 38, tzinfo=UTC), "window_ended"),
    ],
)
def test_start_window_state_reports_reason_and_window(monkeypatch, now, reason) -> None:
    _patch(monkeypatch, now=now, request=_request())

    got_reason, start_at, end_at = resource_service.start_window_state(
        session=object(), vmid=484,
    )

    assert got_reason == reason
    assert start_at == WINDOW_START
    assert end_at == WINDOW_END


def test_start_window_state_ignores_class_machines(monkeypatch) -> None:
    _patch(
        monkeypatch,
        now=datetime(2026, 9, 26, tzinfo=UTC),
        request=_request(),
        resource=SimpleNamespace(teaching_class_id="class-1"),
    )

    assert resource_service.start_window_state(session=object(), vmid=478) == (None, None, None)


def test_start_window_state_without_window(monkeypatch) -> None:
    _patch(monkeypatch, now=datetime(2026, 9, 26, tzinfo=UTC), request=None)

    assert resource_service.start_window_state(session=object(), vmid=484) == (None, None, None)


def test_enforce_start_window_raises_translated_message_after_window(monkeypatch) -> None:
    _patch(monkeypatch, now=datetime(2026, 9, 26, 4, 38, tzinfo=UTC), request=_request())

    with pytest.raises(BadRequestError) as exc:
        resource_service._enforce_start_window(session=object(), vmid=484)

    assert exc.value.message == t("resource.start_window_ended")
