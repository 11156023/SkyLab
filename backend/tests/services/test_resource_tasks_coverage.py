"""Coverage tests for app.services.resource.tasks (arq handler delegation)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.services.resource import backup_service, deletion_service, reset_service, tasks


@pytest.mark.parametrize(
    ("handler", "module", "name"),
    [
        (tasks.reset_to_init_snapshot, reset_service, "run_reset_task"),
        (tasks.delete_resource, deletion_service, "run_delete_task"),
        (tasks.backup_resource, backup_service, "run_backup_task"),
        (tasks.restore_resource, backup_service, "run_restore_task"),
    ],
)
async def test_handlers_delegate_to_service_in_thread(
    monkeypatch: pytest.MonkeyPatch, handler: Any, module: Any, name: str
) -> None:
    seen: list[tuple[uuid.UUID, dict[str, Any]]] = []

    def _fake(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
        seen.append((task_id, payload))
        return {"handled_by": name}

    monkeypatch.setattr(module, name, _fake)
    task_id = uuid.uuid4()
    out = await handler(task_id, {"vmid": 150})
    assert out == {"handled_by": name}
    assert seen == [(task_id, {"vmid": 150})]
