"""Web Push repository：VAPID 金鑰 singleton 與訂閱 upsert／刪除（記憶體 SQLite，不碰共用庫）。"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator

import pytest
from cryptography.hazmat.primitives import serialization
from sqlmodel import Session, SQLModel, create_engine

from app.models import User, UserRole
from app.repositories import push as push_repo
from app.repositories import user as user_repo
from app.schemas.user import UserCreate


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _create_user(session: Session) -> User:
    user = user_repo.create_user(
        session=session,
        user_create=UserCreate(
            email=f"user-{uuid.uuid4().hex[:8]}@example.com",
            password="strongpass123",
            role=UserRole.student,
        ),
    )
    session.commit()
    session.refresh(user)
    return user


def _subscribe(session: Session, user: User, endpoint: str, **kw: object) -> None:
    push_repo.upsert_subscription(
        session=session,
        user_id=user.id,
        endpoint=endpoint,
        p256dh=str(kw.get("p256dh", "P")),
        auth=str(kw.get("auth", "A")),
        user_agent=None,
        language=str(kw.get("language", "zh-TW")),
    )


def test_generate_vapid_keys_are_a_valid_p256_pair() -> None:
    pem, public_key = push_repo.generate_vapid_keys()
    private = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    padded = public_key + "=" * (-len(public_key) % 4)
    raw = base64.urlsafe_b64decode(padded)
    # 未壓縮點：0x04 + 32 bytes X + 32 bytes Y
    assert len(raw) == 65 and raw[0] == 0x04
    expected = private.public_key().public_bytes(  # type: ignore[union-attr]
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    assert raw == expected


def test_web_push_config_is_generated_once_and_reused(db: Session) -> None:
    first = push_repo.get_web_push_config(session=db)
    second = push_repo.get_web_push_config(session=db)
    assert first.id == 1
    assert first.vapid_public_key == second.vapid_public_key
    assert first.vapid_private_key_pem.startswith("-----BEGIN PRIVATE KEY-----")
    assert first.subject.startswith(("mailto:", "https://"))


def test_upsert_moves_endpoint_to_new_user_and_resets_failures(db: Session) -> None:
    alice = _create_user(db)
    bob = _create_user(db)
    _subscribe(db, alice, "https://push.example/e1", language="en")
    sub = push_repo.get_subscription_by_endpoint(
        session=db, endpoint="https://push.example/e1"
    )
    assert sub is not None and sub.user_id == alice.id and sub.language == "en"

    sub.failure_count = 3
    db.add(sub)
    db.commit()

    # 同一台瀏覽器換帳號：endpoint 歸屬改到 bob，失敗計數歸零，不會多一筆
    _subscribe(db, bob, "https://push.example/e1", language="ja", p256dh="P2")
    moved = push_repo.get_subscription_by_endpoint(
        session=db, endpoint="https://push.example/e1"
    )
    assert moved is not None
    assert moved.id == sub.id
    assert moved.user_id == bob.id
    assert moved.language == "ja"
    assert moved.p256dh == "P2"
    assert moved.failure_count == 0
    assert push_repo.list_subscriptions_for_user(session=db, user_id=alice.id) == []
    assert push_repo.list_subscribed_user_ids(session=db) == [bob.id]


def test_delete_by_ids_and_subscribed_user_ids(db: Session) -> None:
    alice = _create_user(db)
    _subscribe(db, alice, "https://push.example/a")
    _subscribe(db, alice, "https://push.example/b")
    subs = push_repo.list_subscriptions_for_user(session=db, user_id=alice.id)
    assert len(subs) == 2
    assert push_repo.list_subscribed_user_ids(session=db) == [alice.id]

    removed = push_repo.delete_subscriptions_by_ids(session=db, ids=[subs[0].id])
    assert removed == 1
    remaining = push_repo.list_subscriptions_for_user(session=db, user_id=alice.id)
    assert [s.endpoint for s in remaining] == ["https://push.example/b"]
    assert push_repo.delete_subscriptions_by_ids(session=db, ids=[]) == 0

    push_repo.delete_subscription(session=db, subscription=remaining[0])
    assert push_repo.list_subscribed_user_ids(session=db) == []
