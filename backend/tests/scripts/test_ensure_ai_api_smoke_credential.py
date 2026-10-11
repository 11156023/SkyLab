from __future__ import annotations

import os
import stat
import sys
import uuid
from datetime import timedelta, timezone
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import ai_api_key
from app.api.deps.ai_api_key import get_current_user_by_ai_api_key
from app.exceptions import NotFoundError
from app.models import (
    AIAPICredential,
    AIAPIRequest,
    AIAPIRequestStatus,
    AIAPIUsage,
    User,
    UserRole,
    get_datetime_utc,
)
from app.schemas import AIAPIRequestCreate, AIAPIRequestReview
from app.services.llm_gateway import ai_gateway_service
from scripts import ensure_ai_api_smoke_credential as smoke_script
from scripts.ensure_ai_api_smoke_credential import (
    SMOKE_KEY_NAME,
    SMOKE_KEY_PURPOSE,
    SMOKE_KEY_RATE_LIMIT,
    cleanup_ai_api_smoke_credentials,
    ensure_ai_api_smoke_credential,
)


@pytest.fixture
def isolated_session(monkeypatch: pytest.MonkeyPatch) -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            AIAPIRequest.__table__,
            AIAPICredential.__table__,
            AIAPIUsage.__table__,
        ],
    )
    monkeypatch.setattr(
        ai_gateway_service.audit_service,
        "log_action",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        ai_gateway_service.ai_api_settings,
        "ai_api_public_base_url",
        "https://campus.example.test/api/v1",
    )
    # SQLite strips timezone information; PostgreSQL returns aware timestamps.
    monkeypatch.setattr(
        ai_api_key,
        "get_datetime_utc",
        lambda: get_datetime_utc().replace(tzinfo=None),
    )
    with Session(engine) as session:
        yield session


@pytest.fixture
def owner(isolated_session: Session) -> User:
    owner = User(
        email=f"ai-api-smoke-{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="not-used-by-this-test",
        role=UserRole.admin,
    )
    isolated_session.add(owner)
    isolated_session.commit()
    isolated_session.refresh(owner)
    return owner


def _assert_invalid_key(session: Session, key: str) -> None:
    with pytest.raises(HTTPException) as error:
        get_current_user_by_ai_api_key(session=session, authorization=f"Bearer {key}")
    assert error.value.status_code == 401


def test_smoke_credential_is_temporary_and_replaced_for_each_deployment(
    isolated_session: Session, owner: User
) -> None:

    first_key = ensure_ai_api_smoke_credential(isolated_session)
    second_key = ensure_ai_api_smoke_credential(isolated_session)

    requests = list(
        isolated_session.exec(
            select(AIAPIRequest)
            .where(AIAPIRequest.user_id == owner.id)
            .where(AIAPIRequest.purpose == SMOKE_KEY_PURPOSE)
        ).all()
    )
    credentials = list(
        isolated_session.exec(
            select(AIAPICredential)
            .where(AIAPICredential.user_id == owner.id)
            .where(AIAPICredential.api_key_name == SMOKE_KEY_NAME)
        ).all()
    )

    assert first_key.startswith("ccai_")
    assert second_key != first_key
    assert len(requests) == 2
    assert len(credentials) == 2
    assert sum(item.revoked_at is None for item in credentials) == 1
    assert requests[0].rate_limit == SMOKE_KEY_RATE_LIMIT
    assert credentials[0].rate_limit == SMOKE_KEY_RATE_LIMIT
    assert all(request.duration == "1d" for request in requests)
    expires_at = credentials[0].expires_at
    assert expires_at is not None
    assert (
        get_datetime_utc()
        < expires_at.replace(tzinfo=timezone.utc)
        <= (get_datetime_utc() + timedelta(days=1))
    )
    _assert_invalid_key(isolated_session, first_key)
    authenticated_owner, _ = get_current_user_by_ai_api_key(
        session=isolated_session, authorization=f"Bearer {second_key}"
    )
    assert authenticated_owner.id == owner.id


@pytest.mark.parametrize("with_usage", [False, True])
def test_cleanup_invalidates_key_and_preserves_usage(
    isolated_session: Session, owner: User, with_usage: bool
) -> None:
    key = ensure_ai_api_smoke_credential(isolated_session, owner=owner)
    credential = isolated_session.exec(select(AIAPICredential)).one()
    credential_id = credential.id
    if with_usage:
        usage = AIAPIUsage(
            user_id=owner.id,
            credential_id=credential_id,
            model_name="smoke-model",
            call_type="chat_completion",
            status="success",
        )
        isolated_session.add(usage)
        isolated_session.commit()

    cleanup_ai_api_smoke_credentials(isolated_session)
    cleanup_ai_api_smoke_credentials(isolated_session)

    stored = isolated_session.get(AIAPICredential, credential_id)
    assert stored is not None and stored.revoked_at is not None
    if with_usage:
        assert isolated_session.get(AIAPIUsage, usage.id) is not None
    _assert_invalid_key(isolated_session, key)

    next_key = ensure_ai_api_smoke_credential(isolated_session, owner=owner)
    assert next_key != key
    get_current_user_by_ai_api_key(
        session=isolated_session, authorization=f"Bearer {next_key}"
    )


def test_cleanup_finds_legacy_renamed_rotated_keys_of_inactive_owner(
    isolated_session: Session, owner: User
) -> None:
    key = ensure_ai_api_smoke_credential(isolated_session, owner=owner)
    credential = isolated_session.exec(select(AIAPICredential)).one()
    credential.expires_at = None
    isolated_session.add(credential)
    isolated_session.commit()
    rotated = ai_gateway_service.rotate_credential(
        session=isolated_session, credential_id=credential.id, current_user=owner
    )
    assert rotated.api_key is not None
    ai_gateway_service.update_credential_name(
        session=isolated_session,
        credential_id=rotated.id,
        name="renamed-smoke",
        current_user=owner,
    )
    owner.is_active = False
    owner.role = UserRole.student
    isolated_session.add(owner)
    isolated_session.commit()

    cleanup_ai_api_smoke_credentials(isolated_session)

    assert isolated_session.get(AIAPICredential, rotated.id).revoked_at is not None
    _assert_invalid_key(isolated_session, key)
    _assert_invalid_key(isolated_session, rotated.api_key)


def test_deleted_credential_is_hidden_but_first_call_can_finish_accounting(
    isolated_session: Session, owner: User
) -> None:
    key = ensure_ai_api_smoke_credential(isolated_session, owner=owner)
    _, credential = get_current_user_by_ai_api_key(
        session=isolated_session, authorization=f"Bearer {key}"
    )
    credential_id = credential.id
    ai_gateway_service.delete_credential(
        session=isolated_session, credential_id=credential_id, current_user=owner
    )
    stored = isolated_session.get(AIAPICredential, credential_id)
    assert stored is not None
    revoked_at = stored.revoked_at
    assert revoked_at is not None
    assert stored.deleted_at == revoked_at
    assert stored.api_key_encrypted == ""
    assert ai_gateway_service.list_credentials_by_user(
        session=isolated_session, user_id=owner.id
    ).data == []
    with pytest.raises(NotFoundError):
        ai_gateway_service.get_credential(
            session=isolated_session,
            credential_id=credential_id,
            current_user=owner,
        )
    admin_record = next(
        item
        for item in ai_gateway_service.list_all_credentials(
            session=isolated_session
        ).data
        if item.id == credential_id
    )
    assert admin_record.inactive_reason == "deleted"
    assert admin_record.deleted_at == revoked_at
    _assert_invalid_key(isolated_session, key)
    ai_gateway_service.record_usage(
        session=isolated_session,
        user_id=owner.id,
        credential_id=credential_id,
        model_name="smoke-model",
        request_type="chat_completion",
        input_tokens=3,
    )
    usage = isolated_session.exec(select(AIAPIUsage)).one()
    assert usage.credential_id == credential_id and usage.input_tokens == 3
    ai_gateway_service.delete_credential(
        session=isolated_session, credential_id=credential_id, current_user=owner
    )
    stored = isolated_session.get(AIAPICredential, credential_id)
    assert stored.revoked_at == revoked_at
    assert stored.deleted_at == revoked_at


def test_auth_only_loads_legacy_ids_that_can_still_have_rate_limit_buckets(
    isolated_session: Session, owner: User,
) -> None:
    key = ensure_ai_api_smoke_credential(isolated_session, owner=owner)
    current = isolated_session.exec(select(AIAPICredential)).one()
    current_id = current.id
    recent = AIAPICredential(
        user_id=owner.id, request_id=current.request_id, base_url=current.base_url,
        api_key_encrypted="unused", api_key_prefix="recent-legacy", revoked_at=get_datetime_utc(),
    )
    recent_id = recent.id
    isolated_session.add(recent)
    for index in range(50):
        isolated_session.add(AIAPICredential(
            user_id=owner.id, request_id=current.request_id, base_url=current.base_url,
            api_key_encrypted="unused", api_key_prefix=f"old-legacy-{index}",
            revoked_at=get_datetime_utc() - timedelta(days=1),
        ))
    isolated_session.commit()
    _, authenticated = get_current_user_by_ai_api_key(
        session=isolated_session, authorization=f"Bearer {key}",
    )
    assert set(authenticated._rate_limit_legacy_ids) == {str(current_id), str(recent_id)}
    assert "_rate_limit_legacy_ids" not in authenticated.model_dump()


@pytest.mark.parametrize(
    ("key_name", "purpose"),
    [
        (SMOKE_KEY_NAME, "Unrelated application credential."),
        ("other-key", SMOKE_KEY_PURPOSE),
    ],
)
def test_cleanup_preserves_unrelated_keys(
    isolated_session: Session, owner: User, key_name: str, purpose: str
) -> None:
    request = ai_gateway_service.create_request(
        session=isolated_session,
        request_in=AIAPIRequestCreate(
            api_key_name=key_name, purpose=purpose, duration="never"
        ),
        user=owner,
    )
    ai_gateway_service.review_request(
        session=isolated_session,
        request_id=request.id,
        review_data=AIAPIRequestReview(status=AIAPIRequestStatus.approved),
        reviewer=owner,
    )
    credential = isolated_session.exec(select(AIAPICredential)).one()
    key = ai_gateway_service.get_credential(
        session=isolated_session, credential_id=credential.id, current_user=owner
    ).api_key
    assert key is not None

    cleanup_ai_api_smoke_credentials(isolated_session)

    assert credential.expires_at is None
    get_current_user_by_ai_api_key(
        session=isolated_session, authorization=f"Bearer {key}"
    )


def test_cleanup_failure_prevents_new_key(
    isolated_session: Session, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_ai_api_smoke_credential(isolated_session, owner=owner)

    def fail_cleanup(**_kwargs) -> None:
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(ai_gateway_service, "delete_credential", fail_cleanup)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        ensure_ai_api_smoke_credential(isolated_session, owner=owner)
    assert len(isolated_session.exec(select(AIAPICredential)).all()) == 1


def test_cleanup_empty_database_needs_no_admin(isolated_session: Session) -> None:
    cleanup_ai_api_smoke_credentials(isolated_session)


def test_key_expires_even_if_cleanup_cannot_run(
    isolated_session: Session, owner: User
) -> None:
    key = ensure_ai_api_smoke_credential(isolated_session, owner=owner)
    credential = isolated_session.exec(select(AIAPICredential)).one()
    credential.expires_at = get_datetime_utc() - timedelta(seconds=1)
    isolated_session.add(credential)
    isolated_session.commit()

    _assert_invalid_key(isolated_session, key)


def test_legacy_pending_request_cannot_create_a_permanent_smoke_key(
    isolated_session: Session, owner: User
) -> None:
    ai_gateway_service.create_request(
        session=isolated_session,
        request_in=AIAPIRequestCreate(
            api_key_name=SMOKE_KEY_NAME, purpose=SMOKE_KEY_PURPOSE, duration="never"
        ),
        user=owner,
    )

    ensure_ai_api_smoke_credential(isolated_session, owner=owner)

    credential = isolated_session.exec(select(AIAPICredential)).one()
    request = isolated_session.get(AIAPIRequest, credential.request_id)
    assert request is not None and request.duration == "1d"
    assert credential.expires_at is not None


def test_cleanup_cli_outputs_no_secret(
    isolated_session: Session,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key = ensure_ai_api_smoke_credential(isolated_session, owner=owner)
    monkeypatch.setattr(smoke_script, "engine", isolated_session.get_bind())
    monkeypatch.setattr(sys, "argv", ["smoke-credential", "--cleanup"])

    assert smoke_script.main() == 0
    assert capsys.readouterr().out == ""
    isolated_session.expire_all()
    _assert_invalid_key(isolated_session, key)


def test_cleanup_cli_propagates_failure(
    isolated_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_cleanup(_session: Session) -> None:
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(smoke_script, "engine", isolated_session.get_bind())
    monkeypatch.setattr(smoke_script, "cleanup_ai_api_smoke_credentials", fail_cleanup)
    monkeypatch.setattr(sys, "argv", ["smoke-credential", "--cleanup"])

    with pytest.raises(RuntimeError, match="cleanup failed"):
        smoke_script.main()


def test_cleanup_after_key_recovery_failure(
    isolated_session: Session, owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_recovery(**_kwargs) -> None:
        raise RuntimeError("key recovery failed")

    monkeypatch.setattr(ai_gateway_service, "get_credential", fail_recovery)
    with pytest.raises(RuntimeError, match="key recovery failed"):
        ensure_ai_api_smoke_credential(isolated_session, owner=owner)
    assert len(isolated_session.exec(select(AIAPICredential)).all()) == 1

    cleanup_ai_api_smoke_credentials(isolated_session)

    stored = isolated_session.exec(select(AIAPICredential)).one()
    assert stored.revoked_at is not None


def test_creation_cli_writes_private_file_without_logging_secret(
    isolated_session: Session,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    output = tmp_path / "key"
    monkeypatch.setattr(smoke_script, "engine", isolated_session.get_bind())
    monkeypatch.setattr(sys, "argv", ["smoke-credential", "--output", str(output)])

    assert smoke_script.main() == 0
    key = output.read_text(encoding="utf-8").strip()
    assert key.startswith("ccai_")
    captured = capsys.readouterr()
    assert key not in captured.out + captured.err + caplog.text
    assert captured.out == ""
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
    authenticated_owner, _ = get_current_user_by_ai_api_key(
        session=isolated_session, authorization=f"Bearer {key}"
    )
    assert authenticated_owner.id == owner.id


@pytest.mark.parametrize("existing", [False, True])
def test_creation_cli_requires_new_output_before_creating_key(
    isolated_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    existing: bool,
) -> None:
    monkeypatch.setattr(smoke_script, "engine", isolated_session.get_bind())
    output = tmp_path / "key"
    argv = ["smoke-credential"]
    if existing:
        output.write_text("preserve", encoding="utf-8")
        argv += ["--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(FileExistsError if existing else SystemExit):
        smoke_script.main()
    assert isolated_session.exec(select(AIAPIRequest)).all() == []
    if existing:
        assert output.read_text(encoding="utf-8") == "preserve"


def test_creation_cli_removes_file_on_recovery_failure(
    isolated_session: Session,
    owner: User,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_recovery(**_kwargs) -> None:
        raise RuntimeError("key recovery failed")

    output = tmp_path / "key"
    monkeypatch.setattr(smoke_script, "engine", isolated_session.get_bind())
    monkeypatch.setattr(ai_gateway_service, "get_credential", fail_recovery)
    monkeypatch.setattr(sys, "argv", ["smoke-credential", "--output", str(output)])
    with pytest.raises(RuntimeError, match="key recovery failed"):
        smoke_script.main()
    assert not output.exists()
    captured = capsys.readouterr()
    assert "ccai_" not in captured.out + captured.err
    cleanup_ai_api_smoke_credentials(isolated_session)


def test_workflow_cleans_up_even_after_failed_or_cancelled_smoke() -> None:
    workflow_path = (
        Path(__file__).resolve().parents[3] / ".github/workflows/deploy-pve-test.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["deploy"]["steps"]
    smoke_index = next(
        index for index, step in enumerate(steps) if step.get("id") == "ai_api_smoke"
    )
    smoke_step = steps[smoke_index]
    smoke_run = steps[smoke_index]["run"]
    assert smoke_step["continue-on-error"] is True
    assert '--output "$container_smoke_dir/key"' in smoke_run
    assert (
        'docker compose cp "backend:$container_smoke_dir/key" "$smoke_dir/key"'
        in smoke_run
    )
    assert "trap cleanup_smoke_files EXIT" in smoke_run
    assert smoke_run.index("set +x") < smoke_run.index('AI_API_SMOKE_KEY="')
    assert smoke_run.index("umask 077") < smoke_run.index("mktemp -d")
    assert smoke_run.index("::add-mask::") < smoke_run.index("export AI_API_SMOKE_KEY")
    assert "::warning::Unable to create or recover" in smoke_run
    assert "::error::" not in smoke_run
    warning_step = steps[smoke_index + 1]
    assert warning_step["if"] == "${{ steps.ai_api_smoke.outcome == 'failure' }}"
    assert "::warning" in warning_step["run"]
    cleanup_step = steps[smoke_index + 2]
    assert cleanup_step["if"] == (
        "${{ always() && steps.ai_api_smoke.outcome != 'skipped' }}"
    )
    assert cleanup_step["run"] == (
        "docker compose exec -T backend python -m "
        "scripts.ensure_ai_api_smoke_credential --cleanup"
    )
    assert not cleanup_step.get("continue-on-error", False)
