"""app.repositories.push 的 VAPID subject 與舊版明文私鑰遷移分支覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from cryptography.fernet import InvalidToken
from sqlmodel import Session, SQLModel, create_engine

from app.core.security import decrypt_value
from app.repositories import push as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("https://skylab.example.com", "https://skylab.example.com"),
        ("http://localhost:5173", "mailto:admin@skylab.local"),
    ],
)
def test_default_subject_falls_back_to_frontend_host(
    monkeypatch: pytest.MonkeyPatch, host: str, expected: str
) -> None:
    monkeypatch.setattr(repo.settings, "EMAILS_FROM_EMAIL", None)
    monkeypatch.setattr(repo.settings, "FRONTEND_HOST", host)
    assert repo.default_vapid_subject() == expected


def test_plaintext_private_key_is_reencrypted_in_place(session: Session) -> None:
    config = repo.get_web_push_config(session=session)
    pem, _ = repo.generate_vapid_keys()
    config.vapid_private_key_pem = pem
    session.add(config)
    session.commit()

    assert repo.get_vapid_private_key(session=session, config=config) == pem
    session.refresh(config)
    assert not config.vapid_private_key_pem.startswith("-----BEGIN")
    assert decrypt_value(config.vapid_private_key_pem) == pem


def test_undecryptable_non_pem_key_is_reraised(session: Session) -> None:
    config = repo.get_web_push_config(session=session)
    config.vapid_private_key_pem = "garbage-not-pem"
    with pytest.raises(InvalidToken):
        repo.get_vapid_private_key(session=session, config=config)
