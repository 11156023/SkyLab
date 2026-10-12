"""開機補設 LXC 密碼後，記錄到 DB 失敗：只撤銷自己這一步，呼叫端 session 照常可用。

用真的 PostgreSQL：SQLite（pysqlite）的 SAVEPOINT 行為和 PostgreSQL 不同，
測不出 savepoint 回滾後外層交易是否還在。
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, event
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.core.security import decrypt_value, encrypt_value
from app.models import Resource, User
from app.services.resource import resource_service
from app.services.template import clone_service


def _db_fixture_available() -> bool:
    from tests.conftest import _assert_safe_pytest_database_target

    try:
        _assert_safe_pytest_database_target()
    except RuntimeError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _db_fixture_available(),
    reason="需要測試用資料庫（見 tests/conftest.py 的 _assert_safe_pytest_database_target）",
)


@pytest.fixture()
def rows(db: Session) -> Iterator[dict[str, int]]:
    """兩台機器：一台有待套用密碼，一台給「呼叫端自己的變更」用。結束後清掉。"""
    owner_id = db.exec(
        select(User.id).where(User.email == settings.FIRST_SUPERUSER)
    ).one()
    pending_vmid = random.randint(910_000, 919_999)
    caller_vmid = pending_vmid + 10_000
    with Session(engine) as session:
        session.add(
            Resource(
                vmid=pending_vmid,
                user_id=owner_id,
                environment_type="class",
                login_password_pending_encrypted=encrypt_value("Rand0mPass12"),
                created_at=datetime.now(UTC),
            )
        )
        session.add(
            Resource(
                vmid=caller_vmid,
                user_id=owner_id,
                environment_type="class",
                created_at=datetime.now(UTC),
            )
        )
        session.commit()
    yield {"pending": pending_vmid, "caller": caller_vmid}
    with Session(engine) as session:
        session.exec(
            delete(Resource).where(
                Resource.vmid.in_([pending_vmid, caller_vmid])  # type: ignore[attr-defined]
            )
        )
        session.commit()


@pytest.fixture(autouse=True)
def _guest_accepts_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        clone_service, "set_lxc_root_password", lambda *a, **k: True
    )


def _fresh(vmid: int) -> Resource:
    with Session(engine) as session:
        row = session.get(Resource, vmid)
        assert row is not None
        session.expunge(row)
        return row


def test_failed_update_keeps_pending_and_caller_changes(rows: dict[str, int]) -> None:
    with Session(engine) as session:
        # 呼叫端（開機流程）在同一個 session 裡已有尚未 commit 的變更
        caller = session.get(Resource, rows["caller"])
        caller.environment_type = "changed-by-caller"
        session.add(caller)

        def fail_password_update(sess, _ctx, _instances) -> None:
            for obj in sess.dirty:
                if (
                    getattr(obj, "vmid", None) == rows["pending"]
                    and obj.login_password_pending_encrypted is None
                ):
                    raise RuntimeError("database hiccup")

        event.listen(session, "before_flush", fail_password_update)
        try:
            assert not resource_service.ensure_lxc_login_password(
                session=session, node="pve", vmid=rows["pending"]
            )
        finally:
            event.remove(session, "before_flush", fail_password_update)

        # session 仍可用：呼叫端自己的變更照樣 commit 得進去
        assert session.is_active
        pending_row = session.get(Resource, rows["pending"])
        assert pending_row.login_password_pending_encrypted is not None
        session.commit()

    assert _fresh(rows["caller"]).environment_type == "changed-by-caller"
    stored = _fresh(rows["pending"])
    assert stored.login_password_encrypted is None
    assert decrypt_value(stored.login_password_pending_encrypted) == "Rand0mPass12"


def test_failed_commit_rolls_back_and_keeps_pending(
    rows: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(engine) as session:
        real_commit = session.commit
        attempts = {"n": 0}

        def flaky_commit() -> None:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("connection lost at COMMIT")
            real_commit()

        monkeypatch.setattr(session, "commit", flaky_commit)

        assert not resource_service.ensure_lxc_login_password(
            session=session, node="pve", vmid=rows["pending"]
        )

        # 已回滾，session 可以繼續用
        assert session.get(Resource, rows["caller"]) is not None
        session.commit()

    stored = _fresh(rows["pending"])
    assert stored.login_password_encrypted is None
    assert decrypt_value(stored.login_password_pending_encrypted) == "Rand0mPass12"


def test_successful_promotion_still_commits(rows: dict[str, int]) -> None:
    with Session(engine) as session:
        assert resource_service.ensure_lxc_login_password(
            session=session, node="pve", vmid=rows["pending"]
        )

    stored = _fresh(rows["pending"])
    assert decrypt_value(stored.login_password_encrypted) == "Rand0mPass12"
    assert stored.login_password_pending_encrypted is None
