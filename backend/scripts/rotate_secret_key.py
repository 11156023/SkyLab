"""Rotate SECRET_KEY without losing the secrets encrypted under the old one.

SECRET_KEY does double duty in this project.  It signs JWTs -- rotating it just
forces everyone to log in again -- but ``app.core.security._get_fernet`` also
derives a Fernet key from it via PBKDF2, and that key encrypts credentials at
rest: Proxmox and LDAP passwords, the gateway SSH private key, the Cloudflare
API token, AI API credentials, per-resource SSH keys and login passwords, TOTP
secrets, the Web Push VAPID private key, pending VM request passwords and the
login passwords queued in clone task payloads.

Changing SECRET_KEY on its own therefore makes every one of those values
permanently undecryptable.  Losing the Proxmox passwords alone stops the
platform from talking to Proxmox at all.

This script re-encrypts each value: decrypt with the old key, encrypt with the
new one, all inside a single transaction.  Rows that fail to decrypt are
reported and left untouched rather than silently corrupted.

Usage
-----
Preview what would change (no writes, no .env edit)::

    python -m scripts.rotate_secret_key

Rotate for real, generating a new key and updating .env::

    python -m scripts.rotate_secret_key --apply

Rotate to a key you supply yourself::

    python -m scripts.rotate_secret_key --apply --new-key "<value>"

Inside the backend container the project .env is not mounted; run it there
with ``--skip-env-update`` (the new key is printed for you to put in .env
yourself), or point ``--env-file`` at the right file.  The .env is checked
before the database is touched.

A value that decrypts under neither the old nor the new key was encrypted under
some earlier key and is already unrecoverable.  The run aborts on those by
default; pass ``--skip-undecryptable`` to rotate everything else and leave them
as they are.

After a successful run, restart the backend and the worker.  Every issued access and refresh
token stops validating, so all users must log in again.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import inspect, text

from app.core.config import settings
from app.core.db import engine
from app.core.security import derive_fernet

logger = logging.getLogger("rotate_secret_key")


@dataclass(frozen=True)
class EncryptedColumn:
    table: str
    pk: str
    column: str
    # The column may still hold a legacy unencrypted PEM (web_push_config
    # stored the VAPID key in plaintext before it was encrypted at rest).
    legacy_plaintext_pem: bool = False


# Every column written through app.core.security.encrypt_value.
# tests/scripts/test_rotate_secret_key.py guards that new encrypted model
# columns get added here.
ENCRYPTED_COLUMNS: tuple[EncryptedColumn, ...] = (
    # dbw02 起已刪除；保留給尚未升級的環境（腳本會跳過不存在的欄位）
    EncryptedColumn("proxmox_config", "id", "encrypted_password"),
    EncryptedColumn("proxmox_connections", "id", "encrypted_password"),
    EncryptedColumn("ldap_config", "id", "encrypted_bind_password"),
    EncryptedColumn("gateway_config", "id", "encrypted_private_key"),
    EncryptedColumn("cloudflare_config", "id", "encrypted_api_token"),
    EncryptedColumn("ai_api_credentials", "id", "api_key_encrypted"),
    EncryptedColumn("resources", "vmid", "ssh_private_key_encrypted"),
    EncryptedColumn("resources", "vmid", "login_password_encrypted"),
    EncryptedColumn("resources", "vmid", "login_password_pending_encrypted"),
    EncryptedColumn("user", "id", "totp_secret_encrypted"),
    EncryptedColumn(
        "web_push_config", "id", "vapid_private_key_pem", legacy_plaintext_pem=True
    ),
    EncryptedColumn("vm_requests", "id", "password"),
)

# Fernet ciphertext embedded in a JSON column (json since dbm06, text before):
# TaskRecord.payload["login_password_enc"] (services/template/clone_service).
ENCRYPTED_JSON_FIELDS: tuple[EncryptedColumn, ...] = (
    EncryptedColumn("task_records", "id", "payload"),
)
_JSON_FIELD_KEY = "login_password_enc"

# In a column flagged legacy_plaintext_pem, a value neither key can decrypt but
# that looks like a PEM is legacy plaintext; encrypt it under the new key
# instead of treating it as lost.
_PLAINTEXT_PEM_MARKER = "-----BEGIN"


def _quote(identifier: str) -> str:
    # ``user`` is a reserved word in PostgreSQL, so every identifier is quoted.
    return '"' + identifier.replace('"', '""') + '"'


def rotate_value(
    value: str,
    *,
    old_fernet: Fernet,
    new_fernet: Fernet,
    legacy_plaintext_pem: bool = False,
) -> tuple[str, str | None]:
    """Re-encrypt one stored value.

    Returns ``(outcome, new_value)`` where outcome is ``"rotated"`` (new_value
    holds the new ciphertext), ``"skipped"`` (already under the new key) or
    ``"failed"`` (neither key decrypts it; leave it untouched).
    """
    try:
        plain = old_fernet.decrypt(value.encode())
    except (InvalidToken, ValueError):
        # Already rotated, or written under a different key. Leave it alone --
        # overwriting would destroy the only copy.
        try:
            new_fernet.decrypt(value.encode())
        except (InvalidToken, ValueError):
            if legacy_plaintext_pem and value.startswith(_PLAINTEXT_PEM_MARKER):
                return "rotated", new_fernet.encrypt(value.encode()).decode()
            return "failed", None
        return "skipped", None
    return "rotated", new_fernet.encrypt(plain).decode()


def _load_json_payload(value: object) -> dict[str, Any] | None:
    """Return the payload as a dict, whether the column is json or legacy text.

    A json column comes back already parsed by the driver; a text column (an
    environment not yet upgraded past dbm06) comes back as the encoded string.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _json_assignment(column_type: str) -> str:
    """SQL expression that assigns the ``:value`` JSON string to the column."""
    upper = column_type.upper()
    if upper == "JSONB":
        return "CAST(:value AS jsonb)"
    if upper == "JSON":
        return "CAST(:value AS json)"
    return ":value"


_SECRET_KEY_LINE = re.compile(r"^SECRET_KEY=.*$", re.MULTILINE)
# Same floor app.core.config warns about outside ENVIRONMENT=local.
_MIN_KEY_LENGTH = 32


def find_env_file(explicit: str | None = None) -> Path:
    """Locate the .env to update and make sure it has a SECRET_KEY line.

    Called before touching the database: rotating the stored values and then
    failing to update .env would leave every credential unreadable after the
    next restart.
    """
    env_path = (
        Path(explicit) if explicit else Path(__file__).resolve().parents[2] / ".env"
    )
    if not env_path.exists():
        raise SystemExit(
            f"找不到 .env：{env_path}。用 --env-file 指定路徑；在容器內執行"
            "（.env 沒有掛進容器）時改用 --skip-env-update，完成後自行更新 .env。"
        )
    if not _SECRET_KEY_LINE.search(env_path.read_text(encoding="utf-8")):
        raise SystemExit(f"在 {env_path} 中找不到 SECRET_KEY= 這一行")
    return env_path


def write_secret_key(env_path: Path, new_key: str) -> None:
    """Replace the SECRET_KEY line in .env, preserving everything else."""
    original = env_path.read_text(encoding="utf-8")
    if not _SECRET_KEY_LINE.search(original):
        raise SystemExit(f"在 {env_path} 中找不到 SECRET_KEY= 這一行")

    backup = env_path.with_suffix(env_path.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")
    # A callable replacement: a user-supplied key may contain backslashes.
    updated = _SECRET_KEY_LINE.sub(lambda _: f"SECRET_KEY={new_key}", original, count=1)
    env_path.write_text(updated, encoding="utf-8")
    logger.info("已更新 %s（原檔備份於 %s）", env_path.name, backup.name)


def rotate(*, new_key: str, apply: bool, skip_undecryptable: bool) -> int:
    old_fernet = derive_fernet(settings.SECRET_KEY)
    new_fernet = derive_fernet(new_key)
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    rotated = skipped = failed = 0

    def column_exists(spec: EncryptedColumn) -> bool:
        if spec.table not in existing_tables:
            logger.info("略過 %s.%s（資料表不存在）", spec.table, spec.column)
            return False
        columns = {c["name"] for c in inspector.get_columns(spec.table)}
        if spec.column not in columns:
            logger.info("略過 %s.%s（欄位不存在）", spec.table, spec.column)
            return False
        return True

    def json_column_type(spec: EncryptedColumn) -> str:
        for c in inspector.get_columns(spec.table):
            if c["name"] == spec.column:
                return str(c["type"])
        return ""

    def tally(outcome: str, spec: EncryptedColumn, pk: object) -> None:
        nonlocal rotated, skipped, failed
        if outcome == "rotated":
            rotated += 1
        elif outcome == "skipped":
            skipped += 1
            logger.info(
                "已是新金鑰加密，略過：%s.%s pk=%s", spec.table, spec.column, pk
            )
        else:
            failed += 1
            logger.error(
                "無法用舊金鑰解密：%s.%s pk=%s（已略過，未修改）",
                spec.table, spec.column, pk,
            )

    # An explicit transaction so a preview writes nothing and a real run is
    # all-or-nothing: a partially rotated table would be unrecoverable.
    with engine.connect() as conn:
        transaction = conn.begin()
        for spec in ENCRYPTED_COLUMNS:
            if not column_exists(spec):
                continue
            table, pk_col, col = (
                _quote(spec.table), _quote(spec.pk), _quote(spec.column)
            )
            rows = conn.execute(
                text(
                    f"SELECT {pk_col} AS pk, {col} AS value FROM {table} "
                    f"WHERE {col} IS NOT NULL AND {col} <> ''"
                )
            ).all()

            for row in rows:
                outcome, new_value = rotate_value(
                    row.value,
                    old_fernet=old_fernet,
                    new_fernet=new_fernet,
                    legacy_plaintext_pem=spec.legacy_plaintext_pem,
                )
                tally(outcome, spec, row.pk)
                if apply and new_value is not None:
                    conn.execute(
                        text(f"UPDATE {table} SET {col} = :value WHERE {pk_col} = :pk"),
                        {"value": new_value, "pk": row.pk},
                    )

            logger.info("%s.%s：%d 筆", spec.table, spec.column, len(rows))

        for spec in ENCRYPTED_JSON_FIELDS:
            if not column_exists(spec):
                continue
            table, pk_col, col = (
                _quote(spec.table), _quote(spec.pk), _quote(spec.column)
            )
            # dbm06 起是 json 型別（之前是 text）：json 沒有 LIKE 運算子，
            # 先轉成 text 再比對；寫回時也要轉回欄位原本的型別。
            new_value_sql = _json_assignment(json_column_type(spec))
            rows = conn.execute(
                text(
                    f"SELECT {pk_col} AS pk, {col} AS value FROM {table} "
                    f"WHERE CAST({col} AS text) LIKE :needle"
                ),
                {"needle": f'%"{_JSON_FIELD_KEY}"%'},
            ).all()

            count = 0
            for row in rows:
                payload = _load_json_payload(row.value)
                if payload is None:
                    continue
                enc = payload.get(_JSON_FIELD_KEY)
                if not isinstance(enc, str) or not enc:
                    continue
                count += 1
                outcome, new_value = rotate_value(
                    enc, old_fernet=old_fernet, new_fernet=new_fernet
                )
                tally(outcome, spec, row.pk)
                if apply and new_value is not None:
                    payload[_JSON_FIELD_KEY] = new_value
                    conn.execute(
                        text(
                            f"UPDATE {table} SET {col} = {new_value_sql} "
                            f"WHERE {pk_col} = :pk"
                        ),
                        {"value": json.dumps(payload), "pk": row.pk},
                    )

            logger.info(
                "%s.%s[%s]：%d 筆", spec.table, spec.column, _JSON_FIELD_KEY, count
            )

        if apply and (not failed or skip_undecryptable):
            transaction.commit()
        else:
            transaction.rollback()

    verb = "已重新加密" if apply else "可重新加密"
    logger.info("─" * 56)
    logger.info("%s %d 筆；略過 %d 筆；失敗 %d 筆", verb, rotated, skipped, failed)

    if failed and not skip_undecryptable:
        logger.error(
            "有 %d 筆無法用舊金鑰解密；資料庫已回滾，.env 未變更。"
            "請先確認目前的 SECRET_KEY 正確；若確認這些值本來就已失效，"
            "加上 --skip-undecryptable 可略過它們繼續輪替。",
            failed,
        )
        return 1
    if failed:
        logger.warning(
            "已略過 %d 筆無法解密的值（維持原樣，仍然無法使用）。", failed
        )
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="實際寫入資料庫並更新 .env；未指定時只做預覽",
    )
    parser.add_argument(
        "--new-key",
        default=None,
        help="指定新的 SECRET_KEY；未指定時自動產生",
    )
    parser.add_argument(
        "--skip-undecryptable",
        action="store_true",
        help="遇到舊金鑰也解不開的值時，略過它們而非中止（那些值本來就已失效）",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="要更新的 .env 路徑；預設為專案根目錄的 .env",
    )
    parser.add_argument(
        "--skip-env-update",
        action="store_true",
        help="不改 .env（例如在容器內執行、.env 不在容器裡），改為印出新金鑰由你自行更新",
    )
    args = parser.parse_args()

    new_key = args.new_key or secrets.token_urlsafe(48)
    if new_key == settings.SECRET_KEY:
        raise SystemExit("新舊 SECRET_KEY 相同，無需輪替")
    if len(new_key) < _MIN_KEY_LENGTH:
        raise SystemExit(f"新的 SECRET_KEY 至少要 {_MIN_KEY_LENGTH} 個字元")

    env_path: Path | None = None
    if args.apply and not args.skip_env_update:
        env_path = find_env_file(args.env_file)

    if not args.apply:
        logger.info("── 預覽模式（不寫入任何東西）──")

    exit_code = rotate(
        new_key=new_key,
        apply=args.apply,
        skip_undecryptable=args.skip_undecryptable,
    )
    if exit_code != 0:
        return exit_code

    if args.apply:
        if env_path is not None:
            write_secret_key(env_path, new_key)
        else:
            logger.warning(
                "資料庫已改用新金鑰加密，但 .env 未更新。請立刻把 .env 的 "
                "SECRET_KEY 改成下面這個值（backend 與 worker 共用），再重啟："
            )
            logger.warning("SECRET_KEY=%s", new_key)
        logger.info("完成。請重啟 backend 與 worker；所有使用者需要重新登入。")
    else:
        logger.info("預覽結束。加上 --apply 才會實際輪替。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
