"""worker 與本機 fallback 必須載入同一份任務模組清單。"""

from __future__ import annotations

import pytest

from app.infrastructure.queue import modules, registry


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


EXPECTED_TASK_TYPES = {
    "queue.ping",
    "template.convert",
    "template.delete",
    "template.update_clone",
    "template.update_convert",
    "template.update_cancel",
    "template.clone",
    "resource.reset",
    "batch_provision.run",
    "vm_request.provision",
}



def test_import_task_modules_registers_every_queue_task() -> None:
    modules.import_task_modules()

    assert EXPECTED_TASK_TYPES <= set(registry._registry)


def test_import_task_modules_is_idempotent() -> None:
    modules.import_task_modules()
    before = dict(registry._registry)

    modules.import_task_modules()  # 第二次不得因重複註冊而拋錯

    assert registry._registry == before


def test_worker_settings_expose_all_registered_functions() -> None:
    from app.infrastructure.queue.worker import WorkerSettings

    names = {fn.name for fn in WorkerSettings.functions}
    assert EXPECTED_TASK_TYPES <= names
