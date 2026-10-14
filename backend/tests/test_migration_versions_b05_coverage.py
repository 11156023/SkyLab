"""Coverage for alembic revisions alertconn01, aud01, aud02, aikeydel01, usrsoft01, aiapi01."""

from __future__ import annotations

from contextlib import contextmanager
from io import StringIO
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.alembic.versions import aiapi01_control_plane_guards as aiapi01
from app.alembic.versions import aikeydel01_hide_deleted_credentials as aikeydel01
from app.alembic.versions import alertconn01_alert_connection as alertconn01
from app.alembic.versions import aud01_sync_auditaction_labels as aud01
from app.alembic.versions import aud02_course_practice_audit_actions as aud02
from app.alembic.versions import usrsoft01_soft_delete_users as usrsoft01


class _RecordingOp:
    """Minimal stand-in for ``alembic.op`` that records every call."""

    def __init__(self, bind) -> None:
        self.bind = bind
        self.calls: list[tuple[str, tuple, dict]] = []
        self.autocommit_entered = 0

    def get_bind(self):
        return self.bind

    def get_context(self):
        owner = self

        @contextmanager
        def autocommit_block():
            owner.autocommit_entered += 1
            yield

        return SimpleNamespace(autocommit_block=autocommit_block)

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return _record


def _postgres_offline_op() -> tuple[Operations, StringIO]:
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    return Operations(context), output


# --- alertconn01 ------------------------------------------------------------


def test_alertconn01_upgrade_adds_column_fk_and_index(monkeypatch) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE alert_events (id INTEGER PRIMARY KEY, target TEXT)"
        )
        fake = _RecordingOp(connection)
        monkeypatch.setattr(alertconn01, "op", fake)
        alertconn01.upgrade()
    engine.dispose()

    names = [name for name, _args, _kwargs in fake.calls]
    assert names == [
        "add_column",
        "create_foreign_key",
        "create_index",
        "execute",
        "execute",
    ]
    _name, args, _kwargs = fake.calls[1]
    assert args[0] == "fk_alert_events_connection_id"
    _name, args, _kwargs = fake.calls[2]
    assert args == ("ix_alert_events_connection_id", "alert_events", ["connection_id"])


def test_alertconn01_downgrade_drops_named_fk_and_index(monkeypatch) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE proxmox_connections (id INTEGER PRIMARY KEY)"
        )
        connection.exec_driver_sql("CREATE TABLE other (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE alert_events ("
            "id INTEGER PRIMARY KEY, "
            "connection_id INTEGER, "
            "other_id INTEGER, "
            "CONSTRAINT fk_alert_events_connection_id FOREIGN KEY (connection_id) "
            "REFERENCES proxmox_connections (id), "
            "FOREIGN KEY (other_id) REFERENCES other (id))"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_alert_events_connection_id ON alert_events (connection_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_alert_events_other ON alert_events (other_id)"
        )
        fake = _RecordingOp(connection)
        monkeypatch.setattr(alertconn01, "op", fake)
        alertconn01.downgrade()
    engine.dispose()

    assert fake.calls == [
        (
            "drop_constraint",
            ("fk_alert_events_connection_id", "alert_events"),
            {"type_": "foreignkey"},
        ),
        (
            "drop_index",
            ("ix_alert_events_connection_id",),
            {"table_name": "alert_events"},
        ),
        ("drop_column", ("alert_events", "connection_id"), {}),
    ]


def test_alertconn01_downgrade_without_column_is_noop(monkeypatch) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("CREATE TABLE alert_events (id INTEGER PRIMARY KEY)")
        fake = _RecordingOp(connection)
        monkeypatch.setattr(alertconn01, "op", fake)
        alertconn01.downgrade()
    engine.dispose()
    assert fake.calls == []


# --- aud01 / aud02 ----------------------------------------------------------


@pytest.mark.parametrize("migration", [aud01, aud02])
def test_audit_enum_upgrade_adds_every_value_on_postgresql(
    monkeypatch, migration
) -> None:
    fake = _RecordingOp(SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    monkeypatch.setattr(migration, "op", fake)
    migration.upgrade()

    assert fake.autocommit_entered == 1
    statements = [args[0] for _name, args, _kwargs in fake.calls]
    assert statements == [
        f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'"
        for value in migration.VALUES
    ]


@pytest.mark.parametrize("migration", [aud01, aud02])
def test_audit_enum_upgrade_skips_other_dialects(monkeypatch, migration) -> None:
    fake = _RecordingOp(SimpleNamespace(dialect=SimpleNamespace(name="sqlite")))
    monkeypatch.setattr(migration, "op", fake)
    migration.upgrade()
    assert fake.calls == []
    assert fake.autocommit_entered == 0


@pytest.mark.parametrize("migration", [aud01, aud02])
def test_audit_enum_downgrade_is_noop(monkeypatch, migration) -> None:
    fake = _RecordingOp(None)
    monkeypatch.setattr(migration, "op", fake)
    assert migration.downgrade() is None
    assert fake.calls == []


# --- aikeydel01 / usrsoft01 downgrades -----------------------------------------


def test_aikeydel01_downgrade_drops_constraint_and_column(monkeypatch) -> None:
    operations, output = _postgres_offline_op()
    monkeypatch.setattr(aikeydel01, "op", operations)
    aikeydel01.downgrade()
    sql = output.getvalue()
    assert "DROP CONSTRAINT ck_ai_api_credentials_deleted_revoked" in sql
    assert "DROP COLUMN deleted_at" in sql


def test_usrsoft01_downgrade_drops_constraint_and_column(monkeypatch) -> None:
    operations, output = _postgres_offline_op()
    monkeypatch.setattr(usrsoft01, "op", operations)
    usrsoft01.downgrade()
    sql = output.getvalue()
    assert "DROP CONSTRAINT ck_user_deleted_inactive" in sql
    assert "DROP COLUMN deleted_at" in sql


# --- aiapi01 downgrade ------------------------------------------------------


def test_aiapi01_downgrade_drops_existing_index(monkeypatch) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE ai_api_credentials ("
            "id TEXT PRIMARY KEY, request_id TEXT NOT NULL, revoked_at DATETIME NULL)"
        )
        connection.exec_driver_sql(
            f"CREATE UNIQUE INDEX {aiapi01._INDEX} ON ai_api_credentials (request_id)"
        )
        monkeypatch.setattr(
            aiapi01, "op", Operations(MigrationContext.configure(connection))
        )
        aiapi01.downgrade()
        names = {
            index["name"]
            for index in sa.inspect(connection).get_indexes("ai_api_credentials")
        }
        assert aiapi01._INDEX not in names
        # A second downgrade finds nothing to drop.
        aiapi01.downgrade()
    engine.dispose()
