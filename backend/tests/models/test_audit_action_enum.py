"""AuditAction 與資料庫既有紀錄的相容性檢查。

``audit_logs.action`` 是 PostgreSQL enum，標籤加了就拿不掉；只要 Python 的
``AuditAction`` 少了任何一個資料表裡出現過的值，SQLAlchemy 讀到那筆時就會丟
``LookupError``，稽核清單與 CSV 匯出會整批失敗（2026-09-07 的
``group_member_remove`` 事故）。
"""

import ast
import importlib.util
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditAction, AuditLog
from app.repositories import audit_log as audit_repo
from app.services.user import audit_service

# 已下線功能留下的 action；共用資料庫的 audit_logs 仍有這些紀錄
RETIRED_ACTIONS = (
    "group_create",
    "group_delete",
    "group_member_add",
    "group_member_remove",
    "cloudflare_zone_activation_check",
)

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "alembic"
    / "versions"
    / "aud01_sync_auditaction_labels.py"
)
_COURSE_PRACTICE_MIGRATION = _MIGRATION.with_name(
    "aud02_course_practice_audit_actions.py"
)
_APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _insert_raw(session: Session, action: str) -> uuid.UUID:
    """繞過 ORM 直接寫入字串，模擬舊版程式留下的紀錄。"""
    log_id = uuid.uuid4()
    session.execute(
        sa.insert(AuditLog.__table__).values(
            id=log_id,
            action=action,
            details=f"legacy {action}",
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    return log_id


@pytest.mark.parametrize("action", RETIRED_ACTIONS)
def test_retired_actions_are_still_enum_members(action: str) -> None:
    assert AuditAction(action).value == action


def _literal_audit_actions() -> dict[str, list[str]]:
    """掃描 app/ 內所有 ``log_action`` / ``create_audit_log`` 呼叫的字串 action。"""
    found: dict[str, list[str]] = {}
    for path in _APP_DIR.rglob("*.py"):
        if "alembic" in path.parts:
            continue
        # utf-8-sig：少數檔案帶 BOM，ast.parse 不吃
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in {"log_action", "create_audit_log"}:
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "action"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    found.setdefault(kw.value.value, []).append(
                        f"{path.relative_to(_APP_DIR)}:{node.lineno}"
                    )
    return found


def test_every_literal_action_is_an_enum_member() -> None:
    """``log_action(action="...")`` 接受字串，拼錯或忘了加 enum 只會在執行期爆

    （2026-09-21 快速練習啟動 500：``quick_practice_machine_create``）。
    """
    known = {action.value for action in AuditAction}
    literals = _literal_audit_actions()
    assert literals, "scanner found no log_action calls; the scan itself is broken"

    unknown = {a: where for a, where in literals.items() if a not in known}
    assert not unknown, f"audit actions missing from AuditAction: {unknown}"


def test_course_practice_migration_matches_the_model() -> None:
    spec = importlib.util.spec_from_file_location("aud02", _COURSE_PRACTICE_MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert set(module.VALUES) <= {action.value for action in AuditAction}
    assert "quick_practice_machine_create" in module.VALUES


def test_migration_only_adds_labels_known_to_the_model() -> None:
    spec = importlib.util.spec_from_file_location("aud01", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    known = {action.value for action in AuditAction}
    assert set(module.VALUES) <= known
    assert set(RETIRED_ACTIONS) <= set(module.VALUES)
