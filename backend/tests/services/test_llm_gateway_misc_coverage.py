"""llm_gateway 套件入口、usage_writer 與 ai_usage_export 的補強測試。"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import AIAPIUsage
from app.services import llm_gateway
from app.services.llm_gateway import ai_usage_export, usage_writer


def test_package_lazy_attribute_lookup() -> None:
    assert llm_gateway.ai_usage_export is ai_usage_export
    with pytest.raises(AttributeError):
        _ = llm_gateway.not_a_module


# ---- usage_writer ------------------------------------------------------


@pytest.fixture
def isolated_writer(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(usage_writer, "_writer", None)
    yield
    writer = usage_writer._writer
    if writer is not None:
        writer.executor.shutdown(wait=True)


async def test_write_usage_returns_false_when_writer_closed(
    isolated_writer: None,
) -> None:
    writer = usage_writer.get_usage_writer()
    writer.closed = True

    assert await usage_writer.write_usage(lambda: True, source="api_key") is False


# ---- ai_usage_export ---------------------------------------------------


def test_bool_text_handles_none() -> None:
    assert ai_usage_export._bool_text(None) == ""
    assert ai_usage_export._bool_text(True) == "true"
    assert ai_usage_export._bool_text(False) == "false"


def test_count_rows_applies_every_filter() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    try:
        with Session(engine) as session:
            for model_name in ("m1", "m2"):
                session.add(
                    AIAPIUsage(
                        user_id=user_id,
                        source="platform",
                        model_name=model_name,
                        call_type="chat",
                        status="success",
                        created_at=now - timedelta(minutes=1),
                    )
                )
            session.commit()

            common = {
                "session": session,
                "start_date": now - timedelta(hours=1),
                "end_date": now,
                "source": "platform",
                "status": "success",
                "call_type": "chat",
                "user_id": user_id,
            }
            assert ai_usage_export.count_rows(model_name="m1", **common) == 1
            assert ai_usage_export.count_rows(model_name=None, **common) == 2
    finally:
        engine.dispose()
