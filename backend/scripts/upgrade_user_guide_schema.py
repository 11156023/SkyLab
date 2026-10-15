"""Safely apply the two reviewed account-schema migrations.

Read-only unless --apply is supplied. Run from backend with
python -m scripts.upgrade_user_guide_schema --apply.
Existing account fields are fingerprinted before/after; DDL and revision updates
share a transaction, with short lock/statement timeouts. No application restart.
"""

import argparse
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import settings

TARGET = "guide01_account_seen_guides"
ALLOWED_REVISIONS = {"pwhash01_login_password_hash", "regpolicy01", TARGET}
# New columns are the only account fields these migrations may change.
ACCOUNT_FINGERPRINT = text(
    "SELECT id, md5((to_jsonb(u) - 'seen_user_guides' - 'registration_status')::text) "
    'FROM "user" u ORDER BY id'
)


def apply(*, write: bool = False) -> None:
    engine = create_engine(
        str(settings.SQLALCHEMY_DATABASE_URI),
        connect_args={"client_encoding": "utf8", "connect_timeout": 5},
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '2s'"))
            connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(186451, 1)"))
            revisions = (
                connection.execute(text("SELECT version_num FROM alembic_version"))
                .scalars()
                .all()
            )
            if len(revisions) != 1 or revisions[0] not in ALLOWED_REVISIONS:
                raise RuntimeError("Unexpected database revision; no changes applied")
            before = connection.execute(ACCOUNT_FINGERPRINT).all()
            logging.info("revisions=%s accounts=%s apply=%s", revisions, len(before), write)
            if not write:
                return
            backend = Path(__file__).resolve().parents[1]
            config = Config(str(backend / "alembic.ini"))
            config.set_main_option("script_location", str(backend / "app" / "alembic"))
            config.attributes["connection"] = connection
            command.upgrade(config, TARGET)
            after = connection.execute(ACCOUNT_FINGERPRINT).all()
            final_revisions = (
                connection.execute(text("SELECT version_num FROM alembic_version"))
                .scalars()
                .all()
            )
            columns = {c["name"]: c for c in inspect(connection).get_columns("user")}
            invalid = connection.execute(
                text(
                    'SELECT count(*) FROM "user" WHERE seen_user_guides IS NULL '
                    "OR json_typeof(seen_user_guides) <> 'array' "
                    "OR registration_status IS NULL"
                )
            ).scalar_one()
            if (
                before != after
                or final_revisions != [TARGET]
                or invalid
                or columns["seen_user_guides"]["default"] != "'[]'::json"
                or not columns["registration_status"]["default"]
            ):
                raise RuntimeError(
                    "Verification failed; rolling back the whole upgrade"
                )
        logging.info(
            "Committed: both schema changes verified; all existing account fields unchanged."
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    apply(write=parser.parse_args().apply)
