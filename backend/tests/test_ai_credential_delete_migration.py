"""Deleted AI credentials stay as backend-only accounting records."""

from io import StringIO

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.alembic.versions import aikeydel01_hide_deleted_credentials as migration


def test_upgrade_preserves_credentials_and_requires_revocation(monkeypatch) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ai_api_credentials "
            "(id INTEGER PRIMARY KEY, revoked_at DATETIME NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO ai_api_credentials (id, revoked_at) VALUES (1, NULL)"
        )
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        assert connection.exec_driver_sql(
            "SELECT id, revoked_at, deleted_at FROM ai_api_credentials"
        ).one() == (1, None, None)
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql(
                "UPDATE ai_api_credentials SET deleted_at = '2026-10-06' WHERE id = 1"
            )
        connection.exec_driver_sql(
            "UPDATE ai_api_credentials SET revoked_at = '2026-10-06', "
            "deleted_at = '2026-10-06' WHERE id = 1"
        )
    engine.dispose()


def test_postgresql_upgrade_is_additive(monkeypatch) -> None:
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    monkeypatch.setattr(migration, "op", Operations(context))
    migration.upgrade()
    sql = output.getvalue()
    assert "ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE" in sql
    assert "ck_ai_api_credentials_deleted_revoked" in sql
    assert "deleted_at IS NULL OR revoked_at IS NOT NULL" in sql
    assert "DELETE " not in sql and "DROP " not in sql and "UPDATE " not in sql
