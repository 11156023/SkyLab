"""Migration safety checks for the active AI API credential invariant."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.alembic.versions import aiapi01_control_plane_guards as migration


def _credentials_table(connection: sa.Connection) -> None:
    connection.exec_driver_sql(
        "CREATE TABLE ai_api_credentials ("
        "id TEXT PRIMARY KEY, request_id TEXT NOT NULL, revoked_at DATETIME NULL)"
    )


def test_upgrade_aborts_without_rewriting_duplicate_active_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _credentials_table(connection)
        connection.exec_driver_sql(
            "INSERT INTO ai_api_credentials VALUES "
            "('one', 'request-1', NULL), ('two', 'request-1', NULL)"
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        monkeypatch.setattr(
            migration.op,
            "create_index",
            lambda *_args, **_kwargs: pytest.fail(
                "migration must not create the index over duplicate data"
            ),
        )

        with pytest.raises(RuntimeError, match="request-1"):
            migration.upgrade()

        rows = connection.exec_driver_sql(
            "SELECT id, revoked_at FROM ai_api_credentials ORDER BY id"
        ).all()
        assert rows == [("one", None), ("two", None)]
    engine.dispose()


def test_upgrade_creates_postgresql_partial_unique_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _credentials_table(connection)
        recorded: dict[str, object] = {}
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

        def record_create_index(
            name: str,
            table: str,
            columns: list[str],
            **kwargs: object,
        ) -> None:
            recorded.update(
                name=name, table=table, columns=columns, kwargs=kwargs
            )

        monkeypatch.setattr(migration.op, "create_index", record_create_index)
        migration.upgrade()

    engine.dispose()
    assert recorded["name"] == "uq_ai_api_credentials_active_request"
    assert recorded["table"] == "ai_api_credentials"
    assert recorded["columns"] == ["request_id"]
    kwargs = recorded["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["unique"] is True
    assert str(kwargs["postgresql_where"]) == "revoked_at IS NULL"
