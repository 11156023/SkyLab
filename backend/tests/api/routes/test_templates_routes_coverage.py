"""app/api/routes/templates.py 路由層的單元測試（直接呼叫路由函式，service 全部替換）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.responses import FileResponse

from app.api.routes import templates as tr
from app.models import TaskRecordStatus
from app.schemas.template import VMTemplatePublic

SESSION = object()
USER = SimpleNamespace(id=uuid.uuid4())
TEMPLATE_ID = uuid.uuid4()
NOW = datetime(2030, 1, 1, tzinfo=UTC)


def _record(result: Any = None) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        task_type="template_convert",
        status=TaskRecordStatus.queued,
        progress=0,
        result=result,
        error=None,
        template_id=TEMPLATE_ID,
        resource_vmid=101,
        created_at=NOW,
        started_at=None,
        finished_at=None,
    )


def _template() -> VMTemplatePublic:
    # 路由只把它原樣塞進回應模型；略過驗證以免牽動完整欄位
    return VMTemplatePublic.model_construct(id=TEMPLATE_ID)


def _attachment(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "template_id": TEMPLATE_ID,
        "filename": "manual.pdf",
        "content_type": "application/pdf",
        "size_bytes": 3,
        "created_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_list_template_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tr.template_service, "list_student_catalog", lambda session: [])
    result = tr.list_template_catalog(session=SESSION, _current_user=USER)
    assert result.count == 0


def test_list_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tr.template_service, "list_templates", lambda session, user: [])
    result = tr.list_templates(session=SESSION, current_user=USER)
    assert (result.data, result.count) == ([], 0)


async def test_create_template(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _record(result={"vmid": 1})

    async def _create(*, session: Any, user: Any, data: Any) -> Any:
        return _template(), record

    monkeypatch.setattr(tr.template_service, "create_template", _create)
    result = await tr.create_template(session=SESSION, current_user=USER, body=object())
    assert result.task.id == record.id
    assert result.task.result == {"vmid": 1}


def test_get_and_update_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tr.template_service,
        "get_template_for_user",
        lambda **kw: ("get", kw["template_id"]),
    )
    monkeypatch.setattr(
        tr.template_service, "update_template", lambda **kw: ("update", kw["data"])
    )
    assert tr.get_template(
        session=SESSION, current_user=USER, template_id=TEMPLATE_ID
    ) == ("get", TEMPLATE_ID)
    body = object()
    assert tr.update_template(
        session=SESSION, current_user=USER, template_id=TEMPLATE_ID, body=body
    ) == ("update", body)


async def test_retry_template_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    record = _record(result="not-a-dict")

    async def _retry(*, session: Any, user: Any, template_id: uuid.UUID) -> Any:
        return _template(), record

    monkeypatch.setattr(tr.template_service, "retry_template_conversion", _retry)
    result = await tr.retry_template_conversion(
        session=SESSION, current_user=USER, template_id=TEMPLATE_ID
    )
    assert result.task.id == record.id
    assert result.task.result is None


@pytest.mark.parametrize(
    ("route", "service_name"),
    [
        ("delete_template", "delete_template"),
        ("start_update_cycle", "start_update_cycle"),
        ("finish_update_cycle", "finish_update_cycle"),
        ("cancel_update_cycle", "cancel_update_cycle"),
    ],
)
async def test_single_task_routes(
    monkeypatch: pytest.MonkeyPatch, route: str, service_name: str
) -> None:
    record = _record()
    seen: dict[str, Any] = {}

    async def _service(*, session: Any, user: Any, template_id: uuid.UUID) -> Any:
        seen["template_id"] = template_id
        return record

    monkeypatch.setattr(tr.template_service, service_name, _service)
    result = await getattr(tr, route)(
        session=SESSION, current_user=USER, template_id=TEMPLATE_ID
    )
    assert result.id == record.id
    assert seen["template_id"] == TEMPLATE_ID


async def test_clone_template(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record(), _record()]

    async def _clone(**_kwargs: Any) -> list[Any]:
        return records

    monkeypatch.setattr(tr.clone_service, "request_clone", _clone)
    result = await tr.clone_template(
        session=SESSION, current_user=USER, template_id=TEMPLATE_ID, body=object()
    )
    assert [task.id for task in result.tasks] == [r.id for r in records]


def test_list_template_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    attachments = [_attachment(), _attachment(content_type=None)]
    monkeypatch.setattr(
        tr.template_service, "list_attachments", lambda **kw: attachments
    )
    result = tr.list_template_attachments(
        session=SESSION, current_user=USER, template_id=TEMPLATE_ID
    )
    assert result.count == 2
    assert result.data[1].content_type is None


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [("application/pdf", "application/pdf"), (None, "application/octet-stream")],
)
def test_download_template_attachment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content_type: str | None,
    expected: str,
) -> None:
    path = tmp_path / "stored.bin"
    path.write_bytes(b"x")
    monkeypatch.setattr(
        tr.template_service,
        "get_attachment_for_download",
        lambda **kw: (path, _attachment(content_type=content_type)),
    )
    response = tr.download_template_attachment(
        session=SESSION,
        current_user=USER,
        template_id=TEMPLATE_ID,
        attachment_id=uuid.uuid4(),
    )
    assert isinstance(response, FileResponse)
    assert response.filename == "manual.pdf"
    assert response.media_type == expected


def test_delete_template_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    removed: list[Any] = []
    monkeypatch.setattr(
        tr.template_service,
        "remove_attachment",
        lambda **kw: removed.append(kw["attachment_id"]),
    )
    attachment_id = uuid.uuid4()
    assert tr.delete_template_attachment(
        session=SESSION,
        current_user=USER,
        template_id=TEMPLATE_ID,
        attachment_id=attachment_id,
    ) == {"message": "Attachment deleted"}
    assert removed == [attachment_id]
