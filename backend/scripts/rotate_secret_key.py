"""Rotate SECRET_KEY without losing the secrets encrypted under the old one.

SECRET_KEY does double duty in this project.  It signs JWTs -- rotating it just
forces everyone to log in again -- but ``app.core.security._get_fernet`` also
derives a Fernet key from it via PBKDF2, and that key encrypts credentials at
rest: Proxmox and LDAP passwords, the gateway SSH private key, the Cloudflare
API token, AI API credentials, and per-resource SSH keys and login passwords.

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

A value that decrypts under neither the old nor the new key was encrypted under
some earlier key and is already unrecoverable.  The run aborts on those by
default; pass ``--skip-undecryptable`` to rotate everything else and leave them
as they are.

After a successful run, restart the backend.  Every issued access and refresh
token stops validating, so all users must log in again.
"""

from __future__ import annotations

import argparse
import base64
import logging
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import inspect, text

from app.core.config import settings
from app.core.db import engine

logger = logging.getLogger("rotate_secret_key")

# Must stay in step with app.core.security._get_fernet.
_FERNET_SALT = b"SkyLab-fernet-v1"
_FERNET_ITERATIONS = 480_000


@dataclass(frozen=True)
class EncryptedColumn:
    table: str
    pk: str
    column: str


# Every column written through app.core.security.encrypt_value.
ENCRYPTED_COLUMNS: tuple[EncryptedColumn, ...] = (
    EncryptedColumn("proxmox_connections", "id", "encrypted_password"),
    EncryptedColumn("ldap_config", "id", "encrypted_bind_password"),
    EncryptedColumn("gateway_config", "id", "encrypted_private_key"),
    EncryptedColumn("cloudflare_config", "id", "encrypted_api_token"),
    EncryptedColumn("ai_api_credentials", "id", "api_key_encrypted"),
    EncryptedColumn("resources", "vmid", "ssh_private_key_encrypted"),
    EncryptedColumn("resources", "vmid", "login_password_encrypted"),
)


def build_fernet(secret_key: str) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_FERNET_SALT,
        iterations=_FERNET_ITERATIONS,
    )
    return Fernet(base64.urlsafe_b64encode(kdf.derive(secret_key.encode())))


def find_env_file() -> Path:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        raise SystemExit(f"找不到 .env：{env_path}")
    return env_path


def write_secret_key(env_path: Path, new_key: str) -> None:
    """Replace the SECRET_KEY line in .env, preserving everything else."""
    original = env_path.read_text(encoding="utf-8")
    pattern = re.compile(r"^SECRET_KEY=.*$", re.MULTILINE)
    if not pattern.search(original):
        raise SystemExit("在 .env 中找不到 SECRET_KEY= 這一行")

    backup = env_path.with_suffix(env_path.suffix + ".bak")
    backup.write_text(original, encoding="utf-8")
    env_path.write_text(pattern.sub(f"SECRET_KEY={new_key}", original, count=1),
                        encoding="utf-8")
    logger.info("已更新 %s（原檔備份於 %s）", env_path.name, backup.name)


def rotate(*, new_key: str, apply: bool, skip_undecryptable: bool) -> int:
    old_fernet = build_fernet(settings.SECRET_KEY)
    new_fernet = build_fernet(new_key)
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    rotated = skipped = failed = 0

    # An explicit transaction so a preview writes nothing and a real run is
    # all-or-nothing: a partially rotated table would be unrecoverable.
    with engine.connect() as conn:
        transaction = conn.begin()
        for spec in ENCRYPTED_COLUMNS:
            if spec.table not in existing_tables:
                logger.info("略過 %s.%s（資料表不存在）", spec.table, spec.column)
                continue
            columns = {c["name"] for c in inspector.get_columns(spec.table)}
            if spec.column not in columns:
                logger.info("略過 %s.%s（欄位不存在）", spec.table, spec.column)
                continue

            rows = conn.execute(
                text(
                    f"SELECT {spec.pk} AS pk, {spec.column} AS value "
                    f"FROM {spec.table} "
                    f"WHERE {spec.column} IS NOT NULL AND {spec.column} <> ''"
                )
            ).all()

            for row in rows:
                try:
                    plain = old_fernet.decrypt(row.value.encode())
                except (InvalidToken, ValueError):
                    # Already rotated, or written under a different key. Leave
                    # it alone -- overwriting would destroy the only copy.
                    try:
                        new_fernet.decrypt(row.value.encode())
                    except (InvalidToken, ValueError):
                        failed += 1
                        logger.error(
                            "無法用舊金鑰解密：%s.%s pk=%s（已略過，未修改）",
                            spec.table, spec.column, row.pk,
                        )
                    else:
                        skipped += 1
                        logger.info(
                            "已是新金鑰加密，略過：%s.%s pk=%s",
                            spec.table, spec.column, row.pk,
                        )
                    continue

                rotated += 1
                if apply:
                    conn.execute(
                        text(
                            f"UPDATE {spec.table} SET {spec.column} = :value "
                            f"WHERE {spec.pk} = :pk"
                        ),
                        {"value": new_fernet.encrypt(plain).decode(), "pk": row.pk},
                    )

            logger.info("%s.%s：%d 筆", spec.table, spec.column, len(rows))

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
    args = parser.parse_args()

    new_key = args.new_key or secrets.token_urlsafe(48)
    if new_key == settings.SECRET_KEY:
        raise SystemExit("新舊 SECRET_KEY 相同，無需輪替")

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
        write_secret_key(find_env_file(), new_key)
        logger.info("完成。請重啟後端；所有使用者需要重新登入。")
    else:
        logger.info("預覽結束。加上 --apply 才會實際輪替。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
