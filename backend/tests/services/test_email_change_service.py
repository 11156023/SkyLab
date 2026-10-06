from html import unescape
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.config import settings
from app.exceptions import BadRequestError
from app.models import User
from app.schemas.user import UserUpdateMe
from app.services.user import user_service


def test_email_change_updates_only_after_verification_and_rejects_replay() -> None:
    user = User(email="old@example.com", hashed_password="unused")
    session = Mock()
    session.get.return_value = user
    sent = []

    with (
        patch("app.services.user.user_service.send_email", side_effect=lambda **kw: sent.append(kw)),
        patch("app.services.user.user_service.user_repo.get_user_by_email", return_value=None),
        patch("app.services.user.user_service.audit_service.log_action") as log_action,
        patch.object(settings, "SMTP_HOST", "smtp.example.com"),
        patch.object(settings, "EMAILS_FROM_EMAIL", "sender@example.com"),
    ):
        with pytest.raises(BadRequestError):
            user_service.update_me(
                session=session,
                current_user=user,
                user_in=UserUpdateMe(email="new@example.com"),
            )
        user_service.request_email_change(
            session=session, current_user=user, email="new@example.com"
        )
        assert user.email == "old@example.com"
        assert sent[0]["email_to"] == "new@example.com"

        link = unescape(sent[0]["html_content"].split('href="')[1].split('"')[0])
        token = parse_qs(urlparse(link).query)["token"][0]
        user_service.confirm_email_change_from_link(session=session, token=token)
        assert user.email == "new@example.com"
        session.commit.assert_called_once()
        assert log_action.call_args.kwargs["action"] == "user_update"

        with pytest.raises(BadRequestError):
            user_service.confirm_email_change_from_link(session=session, token=token)
