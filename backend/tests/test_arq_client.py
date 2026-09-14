import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.infrastructure import worker
from app.infrastructure.queue import arq_client, dispatch, registry


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """This unit-test module does not require the external test database."""


@pytest.fixture(autouse=True)
def reset_arq_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arq_client, "_pool", None)


async def test_init_arq_pool_skips_connection_when_redis_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_pool = AsyncMock()
    monkeypatch.setattr(arq_client.settings, "redis_enabled", False)
    monkeypatch.setattr(arq_client, "create_pool", create_pool)

    await arq_client.init_arq_pool()

    create_pool.assert_not_awaited()
    assert arq_client._pool is None


async def test_get_arq_pool_does_not_connect_when_redis_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_pool = AsyncMock()
    monkeypatch.setattr(arq_client.settings, "redis_enabled", False)
    monkeypatch.setattr(arq_client, "create_pool", create_pool)

    with pytest.raises(RuntimeError, match="REDIS_ENABLED=false"):
        await arq_client.get_arq_pool()

    create_pool.assert_not_awaited()


async def test_init_arq_pool_connects_when_redis_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = AsyncMock()
    create_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(arq_client.settings, "redis_enabled", True)
    monkeypatch.setattr(arq_client, "create_pool", create_pool)

    await arq_client.init_arq_pool()

    create_pool.assert_awaited_once()
    assert arq_client._pool is pool


async def test_enqueue_uses_local_runner_when_redis_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = SimpleNamespace(id=uuid.uuid4())
    scheduled: list[object] = []
    executed: list[tuple[str, str, dict[str, object]]] = []

    monkeypatch.setattr(dispatch.settings, "redis_enabled", False)
    monkeypatch.setattr(
        dispatch.task_record_repo,
        "create_task_record",
        lambda **_: record,
    )

    async def fake_run(
        name: str,
        record_id: str,
        payload: dict[str, object],
    ) -> None:
        executed.append((name, record_id, payload))

    def fake_submit(coro: object, **kwargs: object) -> str:
        scheduled.append(coro)
        return str(kwargs["task_id"])

    monkeypatch.setattr(registry, "run_registered_task_locally", fake_run)
    monkeypatch.setattr(worker, "submit", fake_submit)

    result = await dispatch.enqueue_task(
        session=object(),  # type: ignore[arg-type]
        task_type="template.convert",
        user_id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        payload={"vmid": 101},
    )

    assert result is record
    assert len(scheduled) == 1
    await asyncio.wait_for(scheduled[0], timeout=5)  # type: ignore[misc]
    assert executed == [
        ("template.convert", str(record.id), {"vmid": 101})
    ]
