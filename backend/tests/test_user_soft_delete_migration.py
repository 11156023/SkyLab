"""Adding soft deletion preserves legacy accounts and prevents reactivation."""

from io import StringIO

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.alembic.versions import usrsoft01_soft_delete_users as migration


def test_upgrade_preserves_legacy_active_and_inactive_accounts(monkeypatch) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            'CREATE TABLE "user" (id INTEGER PRIMARY KEY, email TEXT NOT NULL, '
            "is_active BOOLEAN NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO user VALUES (1, 'active@example.test', 1), "
            "(2, 'inactive@example.test', 0)"
        )
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        rows = connection.exec_driver_sql(
            "SELECT id, email, is_active, deleted_at FROM user ORDER BY id"
        ).all()
        assert rows == [
            (1, "active@example.test", 1, None),
            (2, "inactive@example.test", 0, None),
        ]
        connection.exec_driver_sql(
            "UPDATE user SET is_active = 0, deleted_at = '2026-10-06' WHERE id = 1"
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql("UPDATE user SET is_active = 1 WHERE id = 1")
        assert connection.exec_driver_sql("SELECT count(*) FROM user").scalar() == 2
    engine.dispose()


def test_postgresql_upgrade_sql_contains_only_additive_schema_changes(
    monkeypatch,
) -> None:
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    monkeypatch.setattr(migration, "op", Operations(context))
    migration.upgrade()
    sql = output.getvalue()
    assert 'ALTER TABLE "user" ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE' in sql
    assert "ck_user_deleted_inactive" in sql
    assert "deleted_at IS NULL OR is_active = false" in sql
    assert "DELETE " not in sql and "DROP " not in sql and "UPDATE " not in sql
