"""app/services/template/tasks.py 與 template_files.py 的單元測試（handler 委派、附件檔案清理）。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from app.services.template import clone_service, tasks, template_files, template_service


async def test_ping_echoes_payload() -> None:
    assert await tasks.ping(uuid.uuid4(), {"a": 1}) == {"pong": True, "echo": {"a": 1}}


@pytest.mark.parametrize(
    ("handler", "module", "target"),
    [
        (tasks.convert_template, template_service, "run_convert_task"),
        (tasks.delete_template, template_service, "run_delete_task"),
        (tasks.update_template_clone, template_service, "run_update_clone_task"),
        (tasks.update_template_convert, template_service, "run_update_convert_task"),
        (tasks.update_template_cancel, template_service, "run_update_cancel_task"),
        (tasks.clone_from_template, clone_service, "run_clone_task"),
    ],
)
async def test_handlers_delegate_to_service(
    monkeypatch: pytest.MonkeyPatch, handler: Any, module: Any, target: str
) -> None:
    calls: list[tuple[uuid.UUID, dict[str, Any]]] = []

    def fake(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((task_id, payload))
        return {"done": target}

    monkeypatch.setattr(module, target, fake)
    task_id = uuid.uuid4()
    assert await handler(task_id, {"x": 1}) == {"done": target}
    assert calls == [(task_id, {"x": 1})]


@pytest.fixture()
def files_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "template_files"
    monkeypatch.setattr(template_files, "ATTACHMENT_DIR", directory)
    return directory


def test_delete_all_for_template_logs_failure(
    files_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    template_id = uuid.uuid4()
    template_files.save_attachment(template_id, uuid.uuid4(), b"x")

    def broken_rmdir(self: Path) -> None:
        raise OSError("directory busy")

    monkeypatch.setattr(Path, "rmdir", broken_rmdir)
    template_files.delete_all_for_template(template_id)
    assert "Failed to remove files for template" in caplog.text
