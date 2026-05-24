"""security upgrade: token_version, ip_cache_ts, re-encrypt fernet data

Revision ID: u1v2w3x4y5z6
Revises: t1u2v3w4x5y6
Create Date: 2026-04-06

"""

import base64
import hashlib
import logging

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

revision = "u1v2w3x4y5z6"
down_revision = "t1u2v3w4x5y6"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _get_secret_key() -> str:
    """Read SECRET_KEY the same way the application does."""
    import os

    # Try env var first, then fall back to .env file
    key = os.environ.get("SECRET_KEY")
    if key:
        return key

    # Read from .env file (same path as Settings)
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".env")
    env_path = os.path.normpath(env_path)
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("SECRET_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")

    raise RuntimeError(
        "SECRET_KEY not found. Set it as an environment variable or in .env"
    )


def _old_fernet(secret_key: str) -> Fernet:
    """Legacy: SHA256-based Fernet key."""
    key = hashlib.sha256(secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _new_fernet(secret_key: str) -> Fernet:
    """New: PBKDF2-based Fernet key."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"SkyLab-fernet-v1",
        iterations=480_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret_key.encode()))
    return Fernet(key)


def _re_encrypt_column(
    connection,
    table_name: str,
    pk_column: str,
    encrypted_column: str,
    old_f: Fernet,
    new_f: Fernet,
) -> int:
    """Re-encrypt all non-empty values in a column. Returns count of migrated rows."""
    result = connection.execute(
        sa.text(
            f"SELECT {pk_column}, {encrypted_column} FROM {table_name} "
            f"WHERE {encrypted_column} IS NOT NULL AND {encrypted_column} != ''"
        )
    )
    rows = result.fetchall()
    migrated = 0

    for row in rows:
        pk_val = row[0]
        encrypted_val = row[1]
        if not encrypted_val:
            continue

        try:
            # Decrypt with old key
            plain = old_f.decrypt(encrypted_val.encode()).decode()
        except InvalidToken:
            # Already using new key, or corrupted — skip
            logger.warning(
                f"  Skipping {table_name}.{pk_column}={pk_val}: "
                f"cannot decrypt with old key (may already be migrated)"
            )
            continue

        # Re-encrypt with new key
        new_encrypted = new_f.encrypt(plain.encode()).decode()
        connection.execute(
            sa.text(
                f"UPDATE {table_name} SET {encrypted_column} = :val "
                f"WHERE {pk_column} = :pk"
            ),
            {"val": new_encrypted, "pk": pk_val},
        )
        migrated += 1

    return migrated


def upgrade() -> None:
    # --- Schema changes ---

    # 1. Add token_version to user table
    op.add_column("user", sa.Column("token_version", sa.Integer(), nullable=True))
    op.execute("UPDATE \"user\" SET token_version = 0 WHERE token_version IS NULL")
    op.alter_column("user", "token_version", nullable=False, server_default="0")

    # 2. Add ip_address_cached_at to resources table
    op.add_column(
        "resources",
        sa.Column("ip_address_cached_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- Data migration: re-encrypt Fernet data ---

    secret_key = _get_secret_key()
    old_f = _old_fernet(secret_key)
    new_f = _new_fernet(secret_key)

    connection = op.get_bind()

    # Table: encrypted column : pk column
    targets = [
        ("vm_requests", "id", "password"),
        ("ai_api_credentials", "id", "api_key_encrypted"),
        ("gateway_config", "id", "encrypted_private_key"),
        ("proxmox_config", "id", "encrypted_password"),
    ]

    for table_name, pk_col, enc_col in targets:
        # Check table exists (some might not have been created yet)
        inspector = sa.inspect(connection)
        if not inspector.has_table(table_name):
            logger.info(f"  Table {table_name} does not exist, skipping")
            continue

        logger.info(f"  Re-encrypting {table_name}.{enc_col} ...")
        count = _re_encrypt_column(connection, table_name, pk_col, enc_col, old_f, new_f)
        logger.info(f"  Migrated {count} rows in {table_name}")


def downgrade() -> None:
    # --- Reverse data migration: re-encrypt back to old key ---

    secret_key = _get_secret_key()
    old_f = _old_fernet(secret_key)  # This becomes the TARGET for downgrade
    new_f = _new_fernet(secret_key)  # This is the CURRENT key

    connection = op.get_bind()

    targets = [
        ("vm_requests", "id", "password"),
        ("ai_api_credentials", "id", "api_key_encrypted"),
        ("gateway_config", "id", "encrypted_private_key"),
        ("proxmox_config", "id", "encrypted_password"),
    ]

    for table_name, pk_col, enc_col in targets:
        inspector = sa.inspect(connection)
        if not inspector.has_table(table_name):
            continue

        logger.info(f"  Reverting {table_name}.{enc_col} to legacy encryption ...")
        # Swap: decrypt with new, encrypt with old
        count = _re_encrypt_column(connection, table_name, pk_col, enc_col, new_f, old_f)
        logger.info(f"  Reverted {count} rows in {table_name}")

    # --- Schema rollback ---
    op.drop_column("resources", "ip_address_cached_at")
    op.drop_column("user", "token_version")
