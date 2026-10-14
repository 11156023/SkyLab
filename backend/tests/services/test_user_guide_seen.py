"""Guide history survives sessions and stays per account."""

from sqlmodel import Session, create_engine

from app.models import User
from app.schemas import UserPublic
from app.services.user.user_service import mark_user_guide_seen


def test_guide_viewing_is_persistent_idempotent_and_account_scoped():
    engine = create_engine("sqlite://")
    User.__table__.create(engine)
    with Session(engine) as session:
        first = User(email="guide@example.com", hashed_password="unused")
        other = User(email="other@example.com", hashed_password="unused")
        session.add_all([first, other])
        session.commit()
        first_id, other_id = first.id, other.id
        for guide_id in ["student-home", "student-home", "my-requests"]:
            mark_user_guide_seen(session=session, current_user=first, guide_id=guide_id)
    with Session(engine) as session:
        first = session.get(User, first_id)
        assert UserPublic.model_validate(first).seen_user_guides == [
            "student-home",
            "my-requests",
        ]
        assert session.get(User, other_id).seen_user_guides == []
    engine.dispose()


def test_migration_suppresses_existing_accounts_but_keeps_new_accounts_unseen(
    monkeypatch,
):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    migration = importlib.import_module(
        "app.alembic.versions.guide01_account_seen_guides"
    )
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE "user" (id INTEGER PRIMARY KEY)'))
        connection.execute(text('INSERT INTO "user" (id) VALUES (1)'))
        monkeypatch.setattr(
            migration, "op", Operations(MigrationContext.configure(connection))
        )
        migration.upgrade()
        connection.execute(text('INSERT INTO "user" (id) VALUES (2)'))
        rows = (
            connection.execute(text('SELECT seen_user_guides FROM "user" ORDER BY id'))
            .scalars()
            .all()
        )
        import json

        assert json.loads(rows[0]) == ["student-home", "my-requests"]
        assert json.loads(rows[1]) == []
    engine.dispose()
