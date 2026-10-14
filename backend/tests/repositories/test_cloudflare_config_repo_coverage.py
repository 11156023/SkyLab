"""app.repositories.cloudflare_config 的建立／更新／驗證／解密覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import AppError
from app.repositories import cloudflare_config as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_upsert_creates_then_updates_and_verifies(session: Session) -> None:
    assert repo.get_cloudflare_config(session) is None

    created = repo.upsert_cloudflare_config(
        session,
        account_id="  acct  ",
        api_token="token-1",
        default_dns_target_type=" A ",
        default_dns_target_value=" 1.2.3.4 ",
    )
    assert created.account_id == "acct"
    assert created.default_dns_target_type == "A"
    assert created.default_dns_target_value == "1.2.3.4"
    assert repo.get_decrypted_api_token(created) == "token-1"

    updated = repo.upsert_cloudflare_config(
        session, account_id=None, api_token="token-2"
    )
    assert updated.id == created.id
    assert updated.account_id == ""
    assert updated.default_dns_target_type == ""
    assert repo.get_decrypted_api_token(updated) == "token-2"

    assert updated.last_verified_at is None
    verified = repo.mark_cloudflare_config_verified(session, updated)
    assert verified.last_verified_at is not None


def test_decrypt_failure_raises_app_error(session: Session) -> None:
    config = repo.upsert_cloudflare_config(session, account_id="a", api_token="t")
    config.encrypted_api_token = "not-a-fernet-token"
    with pytest.raises(AppError) as exc:
        repo.get_decrypted_api_token(config)
    assert exc.value.status_code == 400
