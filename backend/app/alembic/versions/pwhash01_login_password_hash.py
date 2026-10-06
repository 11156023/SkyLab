"""User-chosen machine login passwords are kept as hashes only.

Revision ID: pwhash01_login_password_hash
Revises: mrg09_merge_aikeydel_pentryprx
Create Date: 2026-10-07

A login password the user typed (request form, template clone, password reset)
used to be stored reversibly in ``resources.login_password_encrypted`` and sent
back to the browser. It is now kept only as a SHA-512 crypt hash:

- ``resources.login_password_hash`` / ``login_password_pending_hash``: the
  hash that was (or is still to be) written into the guest. Never shown; used
  to re-apply the password after an LXC reset.
- ``vm_requests.password_hash``: the hash waiting for provisioning.
  ``vm_requests.password`` stays for platform-generated passwords and for
  Windows, whose cloudbase-init only accepts plaintext.

``vm_templates.allow_password_change`` (a teacher's choice) becomes
``password_settable`` (a fact detected when the machine is converted: no
cloud-init means the platform cannot set a password). Existing templates are
all treated as settable; the flag is re-detected on their next update cycle.

Existing machines created from a personal request have their stored password
converted to a hash, so the plaintext is gone after this migration and cannot
be recovered by a downgrade. Platform-generated passwords (class machines,
quick practice) are left alone and remain visible to their owners.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "pwhash01_login_password_hash"
down_revision = "mrg09_merge_aikeydel_pentryprx"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {c["name"] for c in inspector.get_columns(table)}


def _add_string_column(table: str, column: str) -> None:
    if not _has_column(table, column):
        op.add_column(table, sa.Column(column, sa.String(), nullable=True))


def _to_hash(encrypted: str | None, vmid: int) -> str | None:
    """Decrypt a stored password and return its SHA-512 crypt hash."""
    if not encrypted:
        return None
    from cryptography.fernet import InvalidToken

    from app.core.security import decrypt_value
    from app.utils.login_password import hash_login_password

    try:
        return hash_login_password(decrypt_value(encrypted))
    except InvalidToken:
        # 金鑰換過或資料毀損：這組密碼本來就解不開，轉不成雜湊只能清掉
        logger.error(
            "Stored login password of VMID %s cannot be decrypted; dropping it",
            vmid,
        )
        return None


def _hash_personal_request_passwords() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT r.vmid, r.login_password_encrypted,
                   r.login_password_pending_encrypted
            FROM resources r
            JOIN vm_requests q ON q.id = r.request_id
            WHERE q.request_kind NOT IN ('quick_template', 'course')
              AND NOT EXISTS (
                  SELECT 1 FROM quick_practice_session_machines m
                  WHERE m.vm_request_id = q.id
              )
              AND (r.login_password_encrypted IS NOT NULL
                   OR r.login_password_pending_encrypted IS NOT NULL)
            """
        )
    ).fetchall()
    for vmid, encrypted, pending_encrypted in rows:
        bind.execute(
            sa.text(
                """
                UPDATE resources
                SET login_password_hash = :applied,
                    login_password_pending_hash = :pending,
                    login_password_encrypted = NULL,
                    login_password_pending_encrypted = NULL
                WHERE vmid = :vmid
                """
            ),
            {
                "applied": _to_hash(encrypted, vmid),
                "pending": _to_hash(pending_encrypted, vmid),
                "vmid": vmid,
            },
        )


def upgrade() -> None:
    if _has_column("vm_templates", "allow_password_change"):
        op.alter_column(
            "vm_templates",
            "allow_password_change",
            new_column_name="password_settable",
        )
    op.execute("UPDATE vm_templates SET password_settable = true")

    _add_string_column("resources", "login_password_hash")
    _add_string_column("resources", "login_password_pending_hash")
    _add_string_column("vm_requests", "password_hash")

    _hash_personal_request_passwords()


def downgrade() -> None:
    # 已轉成雜湊的密碼還原不了；降版後那些機器顯示為「未記錄」
    for table, column in (
        ("vm_requests", "password_hash"),
        ("resources", "login_password_pending_hash"),
        ("resources", "login_password_hash"),
    ):
        if _has_column(table, column):
            op.drop_column(table, column)
    if _has_column("vm_templates", "password_settable"):
        op.alter_column(
            "vm_templates",
            "password_settable",
            new_column_name="allow_password_change",
        )
