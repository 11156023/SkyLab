"""請求層 session 在慢速 await 前要能讓出 DB 連線。

回歸：整班同時登入時 ``get_current_user`` 的 ``session.get`` 開出的交易一路佔著
連線到回應送出，端點再 await PVE／LLM 或排隊等 threadpool，連線池
（10 + 40）就被吃光，其餘請求等 30 秒後 ``QueuePool limit ... reached``。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from sqlalchemy import String, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import QueuePool
from sqlmodel import Session

from app.api.deps import auth as auth_module
from app.core import security
from app.core.config import settings
from app.core.db import end_read_transaction


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "end_read_tx_row"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


@pytest.fixture
def pooled_engine(tmp_path: Any) -> Any:
    # 檔案型 SQLite + QueuePool：才看得到「連線有沒有還回池子」
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pool.db'}", poolclass=QueuePool, pool_size=2
    )
    _Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(_Row(id=1, name="alice"))
        session.commit()
    yield engine
    engine.dispose()


def _count_statements(engine: Any) -> list[str]:
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    return statements


def test_releases_connection_and_keeps_loaded_objects(pooled_engine: Any) -> None:
    with Session(pooled_engine) as session:
        row = session.get(_Row, 1)
        assert row is not None
        assert pooled_engine.pool.checkedout() == 1

        end_read_transaction(session)

        assert pooled_engine.pool.checkedout() == 0
        assert not session.in_transaction()
        # 物件沒有被標成過期：讀屬性不會再開交易、不會再取連線
        statements = _count_statements(pooled_engine)
        assert row.name == "alice"
        assert statements == []
        assert pooled_engine.pool.checkedout() == 0
        # 之後再查 DB 會自動重新取連線
        session.exec(text("SELECT 1"))  # type: ignore[call-overload]
        assert pooled_engine.pool.checkedout() == 1
        # expire_on_commit 設定有還原
        assert session.expire_on_commit is True


def test_keeps_transaction_with_pending_writes(pooled_engine: Any) -> None:
    """未送出的寫入由呼叫端決定去留，不能被順手 commit 掉。"""
    with Session(pooled_engine) as session:
        row = session.get(_Row, 1)
        assert row is not None
        row.name = "bob"
        end_read_transaction(session)
        assert session.in_transaction()
        session.rollback()
    with Session(pooled_engine) as session:
        assert session.get(_Row, 1).name == "alice"  # type: ignore[union-attr]


def test_noop_without_transaction(pooled_engine: Any) -> None:
    with Session(pooled_engine) as session:
        statements = _count_statements(pooled_engine)
        end_read_transaction(session)
        assert statements == []
        assert not session.in_transaction()


def _access_token(sub: uuid.UUID) -> str:
    payload = {
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "sub": str(sub),
        "ver": 0,
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)


class _FakeUser:
    is_active = True
    token_version = 0
    totp_required = False
    totp_enabled = False


class _RecordingSession:
    """記錄 get_current_user 對 session 的呼叫順序。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.expire_on_commit = True
        self._in_tx = False
        self.new: list[Any] = []
        self.dirty: list[Any] = []
        self.deleted: list[Any] = []

    def get(self, _model: Any, _ident: Any) -> _FakeUser:
        self.calls.append("get")
        self._in_tx = True
        return _FakeUser()

    def in_transaction(self) -> bool:
        return self._in_tx

    def commit(self) -> None:
        self.calls.append(f"commit(expire_on_commit={self.expire_on_commit})")
        self._in_tx = False


async def test_get_current_user_releases_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _not_revoked(*_args: Any, **_kwargs: Any) -> bool:
        return False

    async def _fake_redis() -> None:
        return None

    monkeypatch.setattr(auth_module, "get_redis", _fake_redis)
    monkeypatch.setattr(auth_module, "is_jti_revoked", _not_revoked)

    class _Url:
        path = f"{settings.API_V1_STR}/resources/my"

    class _Request:
        url = _Url()

    session = _RecordingSession()
    user = await auth_module.get_current_user(
        session=session,  # type: ignore[arg-type]
        token=_access_token(uuid.uuid4()),
        request=_Request(),  # type: ignore[arg-type]
    )

    assert isinstance(user, _FakeUser)
    assert session.calls == ["get", "commit(expire_on_commit=False)"]
    assert not session.in_transaction()
    assert session.expire_on_commit is True
