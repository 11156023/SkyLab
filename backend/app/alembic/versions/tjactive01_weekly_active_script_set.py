"""Explicit weekly active script set; preserve all historical scripts and runs.

Revision ID: tjactive01
Revises: tjreply01
"""

from __future__ import annotations

import logging
from collections import defaultdict

import sqlalchemy as sa
from alembic import op

revision = "tjactive01"
down_revision = "tjreply01"
branch_labels = None
depends_on = None


def _items(document: object) -> list[dict]:
    items = document.get("items") if isinstance(document, dict) else None
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        return []
    return items


def _approved(document: object) -> bool:
    return isinstance(document, dict) and document.get("approved") is True


def upgrade() -> None:
    op.add_column(
        "teacher_judge_sessions",
        sa.Column("active_artifact_set_id", sa.Uuid(), nullable=True),
    )
    bind = op.get_bind()
    metadata = sa.MetaData()
    workspaces = sa.Table("teacher_judge_sessions", metadata, autoload_with=bind)
    files = sa.Table("teacher_judge_files", metadata, autoload_with=bind)
    artifacts = sa.Table("teacher_judge_script_artifacts", metadata, autoload_with=bind)
    unresolved = 0
    for workspace in bind.execute(sa.select(workspaces)).mappings():
        if workspace["teaching_class_week_id"] is None:
            continue
        source = (
            bind.execute(
                sa.select(files).where(
                    files.c.id == workspace["selected_file_id"],
                    files.c.teaching_class_id == workspace["teaching_class_id"],
                )
            )
            .mappings()
            .first()
        )
        items = _items(source["analysis_json"]) if source else []
        valid_items = all(
            isinstance(item.get("id"), str)
            and isinstance(item.get("target_node_key"), str)
            for item in items
        )
        expected = (
            {item["id"]: item["target_node_key"] for item in items}
            if valid_items
            else {}
        )
        if (
            not expected
            or len(expected) != len(items)
            or not all(expected)
            or not all(expected.values())
        ):
            unresolved += 1
            continue
        groups = defaultdict(list)
        for row in bind.execute(
            sa.select(artifacts).where(
                artifacts.c.session_id == workspace["id"],
                artifacts.c.teaching_class_id == workspace["teaching_class_id"],
                artifacts.c.artifact_set_id.is_not(None),
            )
        ).mappings():
            groups[row["artifact_set_id"]].append(row)
        candidates = []
        for set_id, rows in groups.items():
            latest = {}
            for row in rows:
                key = row["target_node_key"]
                previous = latest.get(key)
                if previous is None or (row["version"], row["created_at"]) > (
                    previous["version"],
                    previous["created_at"],
                ):
                    latest[key] = row
            children = list(latest.values())
            actual = {}
            valid = set(latest) == set(expected.values()) and all(
                sum(
                    row["target_node_key"] == child["target_node_key"]
                    and row["version"] == child["version"]
                    for row in rows
                )
                == 1
                for child in children
            )
            for child in children:
                valid = valid and (
                    str(child["status"]) == "approved"
                    and child["source_file_id"] == source["id"]
                    and child["source_analysis_revision"] == source["analysis_revision"]
                    and _approved(child["policy_check_result_json"])
                    and _approved(child["ai_review_result_json"])
                )
                for item in _items(child["rubric_snapshot_json"]):
                    if not isinstance(item.get("id"), str):
                        valid = False
                        continue
                    item_id = item.get("id")
                    if item_id in actual:
                        valid = False
                    actual[item_id] = child["target_node_key"]
            if valid and actual == expected:
                candidates.append(
                    (
                        max(child["created_at"] for child in children),
                        str(set_id),
                        set_id,
                    )
                )
        if candidates:
            chosen = max(candidates)[2]
            bind.execute(
                workspaces.update()
                .where(workspaces.c.id == workspace["id"])
                .values(active_artifact_set_id=chosen)
            )
        elif groups:
            unresolved += 1
    if unresolved:
        logging.getLogger("alembic.runtime.migration").warning(
            "%s Teacher Judge workspaces have no provably complete current-source set; regenerate before executing. Historical data was preserved.",
            unresolved,
        )


def downgrade() -> None:
    op.drop_column("teacher_judge_sessions", "active_artifact_set_id")
