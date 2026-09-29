"""同一組 TOTP 驗證碼在併發（或拿著舊資料）時也只能用一次。"""

from sqlmodel import Session

from app.core import security
from app.core.db import engine
from app.models import User
from app.repositories import user as user_repo
from app.schemas import UserCreate
from app.services.user import totp_service
from app.utils import totp as totp_util
from tests.utils.utils import random_email, random_lower_string


def _user_with_secret(db: Session) -> tuple[User, str]:
    user = user_repo.create_user(
        session=db,
        user_create=UserCreate(email=random_email(), password=random_lower_string()),
    )
    secret = totp_util.generate_secret()
    user.totp_secret_encrypted = security.encrypt_value(secret)
    user.totp_enabled = True
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, secret


def test_stale_user_row_cannot_replay_a_consumed_code(db: Session) -> None:
    user, secret = _user_with_secret(db)
    code = totp_util.totp_code(secret)

    with Session(engine) as first, Session(engine) as second:
        # 兩個請求都在任何一方 commit 之前載入使用者（totp_last_used_step 為 NULL）
        first_user = first.get(User, user.id)
        second_user = second.get(User, user.id)
        assert first_user is not None and second_user is not None
        assert second_user.totp_last_used_step is None

        assert totp_service._consume_code(session=first, user=first_user, code=code)
        assert not totp_service._consume_code(
            session=second, user=second_user, code=code
        )

    db.expire_all()
    stored = db.get(User, user.id)
    assert stored is not None
    assert stored.totp_last_used_step is not None


def test_consumed_step_is_visible_on_the_same_object(db: Session) -> None:
    user, secret = _user_with_secret(db)
    step = totp_util.current_step()
    code = totp_util.hotp_code(secret, step)

    assert totp_service._consume_code(session=db, user=user, code=code)
    assert user.totp_last_used_step == step
    # 同一個 session 再用一次也要被擋
    assert not totp_service._consume_code(session=db, user=user, code=code)
