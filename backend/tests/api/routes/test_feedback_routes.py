"""使用者回饋 API 測試。

以 dependency override 隔離：資料庫換成 SQLite 記憶體（真的建表、走 repository 與
service），登入者直接注入，不需要 PostgreSQL 或 Redis。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.deps.auth import get_current_user
from app.api.deps.database import get_db
from app.api.routes import feedback as feedback_routes
from app.core.config import settings
from app.main import app
from app.models import User, UserFeedbackStatus, UserRole

BASE = f"{settings.API_V1_STR}/feedback"


@pytest.fixture()
def session() -> Iterator[Session]:
    # TestClient 在別的執行緒處理請求：記憶體 SQLite 要鎖同一條連線，否則每條連線各一個空 DB
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


@pytest.fixture()
def student(session: Session) -> User:
    user = User(
        id=uuid.uuid4(),
        email="student-feedback@example.com",
        full_name="小明",
        hashed_password="x",
        role=UserRole.student,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def admin(session: Session) -> User:
    user = User(
        id=uuid.uuid4(),
        email="admin-feedback@example.com",
        hashed_password="x",
        role=UserRole.admin,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def api_client(session: Session) -> Iterator[TestClient]:
    """不進入 lifespan 的 client；DB 換成記憶體 SQLite，送出的限流依賴關掉。"""
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[feedback_routes.submit_rate_limit] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(feedback_routes.submit_rate_limit, None)
    app.dependency_overrides.pop(get_current_user, None)


def _login_as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def _submit(client: TestClient, **overrides: object) -> dict:
    body: dict[str, object] = {
        "category": "bug",
        "content": "按了建立機器之後畫面沒反應",
        "app_version": "1.2.3",
    }
    body.update(overrides)
    response = client.post(f"{BASE}/", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_student_can_submit_and_response_carries_sender(
    api_client: TestClient, student: User
) -> None:
    _login_as(student)

    created = _submit(api_client, content="  按了建立機器之後畫面沒反應  ")

    assert created["category"] == "bug"
    # 前後空白去掉再存
    assert created["content"] == "按了建立機器之後畫面沒反應"
    assert created["app_version"] == "1.2.3"
    assert created["status"] == UserFeedbackStatus.open.value
    assert created["user_id"] == str(student.id)
    assert created["user_email"] == student.email
    assert created["user_full_name"] == "小明"
    assert created["resolved_at"] is None


@pytest.mark.parametrize(
    "content",
    ["短", "     ", "x" * 2001],
    ids=["too-short", "blank", "too-long"],
)
def test_submit_rejects_invalid_content(
    api_client: TestClient, student: User, content: str
) -> None:
    _login_as(student)

    response = api_client.post(
        f"{BASE}/", json={"category": "suggestion", "content": content}
    )

    assert response.status_code == 422


def test_submit_rejects_unknown_category(api_client: TestClient, student: User) -> None:
    _login_as(student)

    response = api_client.post(
        f"{BASE}/", json={"category": "praise", "content": "做得很好，繼續加油"}
    )

    assert response.status_code == 422


def test_student_cannot_list_update_or_delete(
    api_client: TestClient, student: User
) -> None:
    _login_as(student)
    created = _submit(api_client)

    assert api_client.get(f"{BASE}/").status_code == 403
    assert (
        api_client.patch(
            f"{BASE}/{created['id']}", json={"status": "resolved"}
        ).status_code
        == 403
    )
    assert api_client.delete(f"{BASE}/{created['id']}").status_code == 403


def test_admin_lists_newest_first_with_filters(
    api_client: TestClient, student: User, admin: User
) -> None:
    _login_as(student)
    first = _submit(api_client, category="bug", content="第一則：按鈕沒反應")
    second = _submit(api_client, category="suggestion", content="第二則：希望能匯出")

    _login_as(admin)
    listed = api_client.get(f"{BASE}/").json()
    assert listed["count"] == 2
    assert [row["id"] for row in listed["data"]] == [second["id"], first["id"]]

    only_bugs = api_client.get(f"{BASE}/", params={"category": "bug"}).json()
    assert only_bugs["count"] == 1
    assert only_bugs["data"][0]["id"] == first["id"]

    paged = api_client.get(f"{BASE}/", params={"limit": 1, "skip": 1}).json()
    assert paged["count"] == 2
    assert [row["id"] for row in paged["data"]] == [first["id"]]

    assert api_client.get(f"{BASE}/", params={"limit": 0}).status_code == 422
    assert api_client.get(f"{BASE}/", params={"status": "bogus"}).status_code == 422


def test_admin_marks_resolved_and_reopens(
    api_client: TestClient, student: User, admin: User
) -> None:
    _login_as(student)
    created = _submit(api_client)

    _login_as(admin)
    resolved = api_client.patch(f"{BASE}/{created['id']}", json={"status": "resolved"})
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["status"] == "resolved"
    assert body["resolved_by_id"] == str(admin.id)
    assert body["resolved_by_email"] == admin.email
    assert body["resolved_at"] is not None

    # 狀態篩選看得到變化
    assert api_client.get(f"{BASE}/", params={"status": "open"}).json()["count"] == 0
    assert (
        api_client.get(f"{BASE}/", params={"status": "resolved"}).json()["count"] == 1
    )

    # 改回 open 時處理紀錄清掉
    reopened = api_client.patch(
        f"{BASE}/{created['id']}", json={"status": "open"}
    ).json()
    assert reopened["status"] == "open"
    assert reopened["resolved_by_id"] is None
    assert reopened["resolved_at"] is None


def test_admin_deletes_feedback(
    api_client: TestClient, student: User, admin: User
) -> None:
    _login_as(student)
    created = _submit(api_client)

    _login_as(admin)
    assert api_client.delete(f"{BASE}/{created['id']}").status_code == 204
    assert api_client.get(f"{BASE}/").json()["count"] == 0
    # 再刪一次：找不到
    assert api_client.delete(f"{BASE}/{created['id']}").status_code == 404
    assert (
        api_client.patch(
            f"{BASE}/{uuid.uuid4()}", json={"status": "resolved"}
        ).status_code
        == 404
    )
