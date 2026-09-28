"""SECRET_KEY must be a fixed value, and init_db must not restore the .env superuser after setup."""

from __future__ import annotations

import uuid
import warnings

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from app.core import db as core_db
from app.core.config import Settings, settings
from app.models import SystemSetup, User
from app.repositories.system_setup import SYSTEM_SETUP_ID

_REQUIRED = {
    "PROJECT_NAME": "SkyLab",
    "POSTGRES_SERVER": "db",
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "a-real-db-password",
    "FIRST_SUPERUSER": "admin@example.com",
    "FIRST_SUPERUSER_PASSWORD": "a-real-admin-password",
}


@pytest.fixture
def no_secret_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)


def test_missing_secret_key_rejected_outside_local(no_secret_key_env: None) -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(_env_file=None, ENVIRONMENT="production", **_REQUIRED)  # type: ignore[call-arg]


def test_empty_secret_key_rejected_outside_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # env_ignore_empty turns `SECRET_KEY=` into "unset".
    monkeypatch.setenv("SECRET_KEY", "")
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(_env_file=None, ENVIRONMENT="staging", **_REQUIRED)  # type: ignore[call-arg]


def test_fixed_secret_key_accepted_outside_local(no_secret_key_env: None) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        s = Settings(  # type: ignore[call-arg]
            _env_file=None,
            ENVIRONMENT="production",
            SECRET_KEY="z" * 43,
            **_REQUIRED,
        )
    assert s.SECRET_KEY == "z" * 43


def test_short_secret_key_only_warns(no_secret_key_env: None) -> None:
    with pytest.warns(UserWarning, match="shorter than 32"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            ENVIRONMENT="production",
            SECRET_KEY="short-but-stable",
            **_REQUIRED,
        )


def test_missing_secret_key_only_warns_in_local(no_secret_key_env: None) -> None:
    with pytest.warns(UserWarning, match="SECRET_KEY is not set"):
        Settings(_env_file=None, ENVIRONMENT="local", **_REQUIRED)  # type: ignore[call-arg]


def _set_setup_completed(db: Session, completed: bool | None) -> None:
    state = db.get(SystemSetup, SYSTEM_SETUP_ID)
    if completed is None:
        if state is not None:
            db.delete(state)
    else:
        if state is None:
            state = SystemSetup(id=SYSTEM_SETUP_ID)
        state.completed = completed
        db.add(state)
    db.commit()


@pytest.mark.parametrize(
    ("completed", "expect_created"),
    [(True, False), (False, True), (None, True)],
)
def test_init_db_respects_finished_setup(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    completed: bool | None,
    expect_created: bool,
) -> None:
    email = f"setup-first-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(settings, "FIRST_SUPERUSER", email)
    original = db.get(SystemSetup, SYSTEM_SETUP_ID)
    snapshot = None if original is None else original.model_dump()
    try:
        _set_setup_completed(db, completed)

        core_db.init_db(db)

        user = db.exec(select(User).where(User.email == email)).first()
        assert (user is not None) is expect_created
    finally:
        db.expire_all()
        user = db.exec(select(User).where(User.email == email)).first()
        if user is not None:
            db.delete(user)
            db.commit()
        state = db.get(SystemSetup, SYSTEM_SETUP_ID)
        if snapshot is None:
            if state is not None:
                db.delete(state)
        else:
            if state is None:
                state = SystemSetup(**snapshot)
            else:
                for key, value in snapshot.items():
                    setattr(state, key, value)
            db.add(state)
        db.commit()


def test_ensure_first_superuser_ignores_finished_setup(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Test fixtures rely on this to seed the superuser even on a DB whose
    # setup wizard is already marked completed.
    email = f"setup-ensure-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(settings, "FIRST_SUPERUSER", email)
    original = db.get(SystemSetup, SYSTEM_SETUP_ID)
    snapshot = None if original is None else original.model_dump()
    try:
        _set_setup_completed(db, True)

        core_db.ensure_first_superuser(db)

        user = db.exec(select(User).where(User.email == email)).first()
        assert user is not None
        assert user.is_superuser
    finally:
        db.expire_all()
        user = db.exec(select(User).where(User.email == email)).first()
        if user is not None:
            db.delete(user)
            db.commit()
        state = db.get(SystemSetup, SYSTEM_SETUP_ID)
        if snapshot is None:
            if state is not None:
                db.delete(state)
        else:
            if state is None:
                state = SystemSetup(**snapshot)
            else:
                for key, value in snapshot.items():
                    setattr(state, key, value)
            db.add(state)
        db.commit()
