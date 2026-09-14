"""Install the course-environment schema without changing Alembic history.

This is intentionally separate from Alembic.  It supports deployments whose
database points at a migration revision that is not present in this checkout.
All operations are additive/idempotent and the alembic_version row is untouched.
"""

import importlib
import logging

from sqlalchemy import inspect, text
from sqlmodel import SQLModel

from app.core.db import engine

# 副作用匯入：載入 app.models 以註冊所有 SQLModel table（不直接使用模組物件）
importlib.import_module("app.models")

NEW_TABLE_NAMES = [
    "course_environments",
    "course_environment_audiences",
    "course_environment_versions",
    "course_environment_nodes",
    "course_environment_edges",
    "class_capacity_reservations",
    "quick_practice_sessions",
    "quick_practice_session_machines",
]
logger = logging.getLogger(__name__)


def _column_names(table: str) -> set[str]:
    return {row["name"] for row in inspect(engine).get_columns(table)}


def _add_column(table: str, name: str, definition: str) -> None:
    if name in _column_names(table):
        return
    with engine.begin() as connection:
        connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {definition}'))


def _drop_not_null(table: str, name: str) -> None:
    if name not in _column_names(table):
        return
    with engine.begin() as connection:
        connection.execute(
            text(f'ALTER TABLE "{table}" ALTER COLUMN {name} DROP NOT NULL')
        )


def apply() -> None:
    new_tables = [SQLModel.metadata.tables[name] for name in NEW_TABLE_NAMES]
    SQLModel.metadata.create_all(engine, tables=new_tables, checkfirst=True)

    _add_column(
        "course_environments",
        "usage_scope",
        "usage_scope VARCHAR(24) NOT NULL DEFAULT 'course'",
    )
    # Existing rows keep today's behaviour: visible to every signed-in user.
    _add_column(
        "course_environments",
        "audience",
        "audience VARCHAR(24) NOT NULL DEFAULT 'campus'",
    )
    # The environment code was removed; legacy databases keep the column but
    # must not block inserts that no longer provide a value.
    _drop_not_null("course_environments", "code")
    # 範本旗標：requires_gpu（克隆時強制選 GPU）
    _add_column(
        "vm_templates",
        "requires_gpu",
        "requires_gpu BOOLEAN NOT NULL DEFAULT FALSE",
    )
    _add_column(
        "course_environments",
        "max_concurrent_sessions",
        "max_concurrent_sessions INTEGER NULL",
    )
    _add_column(
        "course_environment_nodes",
        "position_x",
        "position_x DOUBLE PRECISION NOT NULL DEFAULT 80",
    )
    _add_column(
        "course_environment_nodes",
        "position_y",
        "position_y DOUBLE PRECISION NOT NULL DEFAULT 120",
    )
    _add_column(
        "quick_practice_sessions",
        "status",
        "status VARCHAR(24) NOT NULL DEFAULT 'creating'",
    )
    _add_column(
        "quick_practice_sessions",
        "topology_applied_at",
        "topology_applied_at TIMESTAMP WITH TIME ZONE NULL",
    )
    _add_column(
        "quick_practice_sessions",
        "reclaim_started_at",
        "reclaim_started_at TIMESTAMP WITH TIME ZONE NULL",
    )
    _add_column(
        "quick_practice_sessions",
        "reclaimed_at",
        "reclaimed_at TIMESTAMP WITH TIME ZONE NULL",
    )
    _add_column(
        "quick_practice_sessions",
        "last_error",
        "last_error VARCHAR(2000) NULL",
    )
    _add_column(
        "teaching_classes",
        "course_version_id",
        "course_version_id UUID NULL REFERENCES "
        '"course_environment_versions"(id) ON DELETE RESTRICT',
    )
    _add_column(
        "teaching_classes",
        "locked_at",
        "locked_at TIMESTAMP WITH TIME ZONE NULL",
    )
    _add_column(
        "teaching_class_machine_nodes",
        "source_type",
        "source_type VARCHAR(16) NOT NULL DEFAULT 'template'",
    )
    _add_column(
        "teaching_class_machine_nodes",
        "custom_image_ref",
        "custom_image_ref VARCHAR(500) NULL",
    )
    _add_column(
        "teaching_class_machine_nodes",
        "custom_storage",
        "custom_storage VARCHAR(120) NULL",
    )
    _add_column(
        "teaching_class_machine_nodes",
        "custom_username",
        "custom_username VARCHAR(32) NULL",
    )
    _add_column(
        "teaching_class_machine_nodes",
        "custom_unprivileged",
        "custom_unprivileged BOOLEAN NOT NULL DEFAULT TRUE",
    )
    _add_column(
        "ip_allocation",
        "reservation_key",
        "reservation_key VARCHAR(200) NULL",
    )
    _add_column(
        "ip_allocation",
        "teaching_class_id",
        'teaching_class_id UUID NULL REFERENCES "teaching_classes"(id) '
        "ON DELETE CASCADE",
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                'ALTER TABLE "teaching_class_machine_nodes" '
                "ALTER COLUMN source_template_id DROP NOT NULL"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_teaching_classes_course_version_id "
                'ON "teaching_classes" (course_version_id)'
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "ix_ip_allocation_reservation_key "
                'ON "ip_allocation" (reservation_key) '
                "WHERE reservation_key IS NOT NULL"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_ip_allocation_teaching_class_id "
                'ON "ip_allocation" (teaching_class_id)'
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_quick_practice_sessions_status "
                'ON "quick_practice_sessions" (status)'
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_quick_practice_sessions_reclaimed_at "
                'ON "quick_practice_sessions" (reclaimed_at)'
            )
        )

    logger.info("Course environment compatibility schema is ready.")
    logger.info("Alembic history was not changed.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    apply()
