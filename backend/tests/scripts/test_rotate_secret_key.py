"""rotate_secret_key must cover every Fernet-encrypted column."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel

import app.models  # noqa: F401  -- registers every table on SQLModel.metadata
from app.core import security
from app.core.config import settings
from app.models.task_record import TaskRecord
from scripts import rotate_secret_key as rsk
from tests.utils.user import create_random_user

# Encrypted columns whose names do not say so.
_UNNAMED_ENCRYPTED = {
    ("web_push_config", "vapid_private_key_pem"),
    ("vm_requests", "password"),
}


def test_encrypted_columns_cover_every_encrypted_model_field() -> None:
    expected = set(_UNNAMED_ENCRYPTED)
    for table in SQLModel.metadata.tables.values():
        for column in table.columns:
            if "encrypted" in column.name:
                expected.add((table.name, column.name))

    covered = {(c.table, c.column) for c in rsk.ENCRYPTED_COLUMNS}
    assert expected - covered == set()


def test_rotate_value_outcomes() -> None:
    old = security.derive_fernet("old-key")
    new = security.derive_fernet("new-key")

    outcome, value = rsk.rotate_value(
        old.encrypt(b"secret").decode(), old_fernet=old, new_fernet=new
    )
    assert outcome == "rotated"
    assert value is not None and new.decrypt(value.encode()) == b"secret"

    already = new.encrypt(b"secret").decode()
    assert rsk.rotate_value(already, old_fernet=old, new_fernet=new) == (
        "skipped",
        None,
    )

    lost = security.derive_fernet("other-key").encrypt(b"secret").decode()
    assert rsk.rotate_value(lost, old_fernet=old, new_fernet=new) == (
        "failed",
        None,
    )


def test_rotate_value_encrypts_legacy_plaintext_pem() -> None:
    old = security.derive_fernet("old-key")
    new = security.derive_fernet("new-key")
    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"

    outcome, value = rsk.rotate_value(
        pem, old_fernet=old, new_fernet=new, legacy_plaintext_pem=True
    )

    assert outcome == "rotated"
    assert value is not None and new.decrypt(value.encode()).decode() == pem

    # Only the flagged column (web_push_config) may hold legacy plaintext.
    assert rsk.rotate_value(pem, old_fernet=old, new_fernet=new) == (
        "failed",
        None,
    )
    vapid = next(
        c for c in rsk.ENCRYPTED_COLUMNS if c.table == "web_push_config"
    )
    assert vapid.legacy_plaintext_pem


def test_load_json_payload_accepts_json_and_legacy_text_columns() -> None:
    payload = {"login_password_enc": "x"}
    assert rsk._load_json_payload(payload) == payload
    assert rsk._load_json_payload(json.dumps(payload)) == payload
    assert rsk._load_json_payload("not json") is None
    assert rsk._load_json_payload("[1]") is None
    assert rsk._load_json_payload(None) is None


def test_json_assignment_casts_to_the_column_type() -> None:
    assert rsk._json_assignment("JSON") == "CAST(:value AS json)"
    assert rsk._json_assignment("JSONB") == "CAST(:value AS jsonb)"
    assert rsk._json_assignment("TEXT") == ":value"


def test_find_env_file_rejects_missing_file_or_key_line(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        rsk.find_env_file(str(tmp_path / "missing.env"))

    no_key = tmp_path / "no_key.env"
    no_key.write_text("DOMAIN=example.com\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        rsk.find_env_file(str(no_key))

    ok = tmp_path / "ok.env"
    ok.write_text("SECRET_KEY=old\n", encoding="utf-8")
    assert rsk.find_env_file(str(ok)) == ok


def test_write_secret_key_keeps_other_lines_and_literal_backslashes(
    tmp_path: Path,
) -> None:
    env = tmp_path / ".env"
    env.write_text("DOMAIN=x\nSECRET_KEY=old\nOTHER=y\n", encoding="utf-8")

    rsk.write_secret_key(env, r"new\1key\g<0>")

    assert env.read_text(encoding="utf-8") == (
        "DOMAIN=x\nSECRET_KEY=new\\1key\\g<0>\nOTHER=y\n"
    )
    assert (tmp_path / ".env.bak").read_text(encoding="utf-8").count("old") == 1


def test_rotate_round_trip_on_user_table_and_task_payload(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``user`` is a reserved word: the generated SQL must quote it."""
    user = create_random_user(db)
    user.totp_secret_encrypted = security.encrypt_value("JBSWY3DPEHPK3PXP")
    db.add(user)
    record = TaskRecord(
        id=uuid.uuid4(),
        task_type="b16_rotate_test",
        user_id=user.id,
        payload={"hostname": "h", "login_password_enc": security.encrypt_value("pw")},
    )
    db.add(record)
    db.commit()

    monkeypatch.setattr(
        rsk,
        "ENCRYPTED_COLUMNS",
        (rsk.EncryptedColumn("user", "id", "totp_secret_encrypted"),),
    )
    original_key = settings.SECRET_KEY
    new_key = "test-rotated-secret-key"
    new_fernet = security.derive_fernet(new_key)

    try:
        assert rsk.rotate(new_key=new_key, apply=True, skip_undecryptable=True) == 0

        db.expire_all()
        user_row = db.get(type(user), user.id)
        assert user_row is not None and user_row.totp_secret_encrypted
        assert (
            new_fernet.decrypt(user_row.totp_secret_encrypted.encode()).decode()
            == "JBSWY3DPEHPK3PXP"
        )
        task_row = db.get(TaskRecord, record.id)
        assert task_row is not None
        # task_records.payload is a json column (dbm06): it must stay an object.
        payload = task_row.payload
        assert isinstance(payload, dict)
        assert payload["hostname"] == "h"
        assert new_fernet.decrypt(payload["login_password_enc"].encode()) == b"pw"
    finally:
        # Rotate back so the rest of the suite still decrypts with the test key.
        monkeypatch.setattr(settings, "SECRET_KEY", new_key)
        rsk.rotate(new_key=original_key, apply=True, skip_undecryptable=True)
        monkeypatch.setattr(settings, "SECRET_KEY", original_key)
        db.expire_all()
        task_row = db.get(TaskRecord, record.id)
        if task_row is not None:
            db.delete(task_row)
        user_row = db.get(type(user), user.id)
        if user_row is not None:
            db.delete(user_row)
        db.commit()

    assert security.decrypt_value(
        security.encrypt_value("x")
    ) == "x"
