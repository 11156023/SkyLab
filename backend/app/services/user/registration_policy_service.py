"""Read and update the site-wide self-registration policy."""

from sqlmodel import Session

from app.core.config import settings
from app.core.i18n import t
from app.exceptions import BadRequestError
from app.models.registration_policy import RegistrationPolicy


def _signup_ready() -> bool:
    return bool(
        settings.emails_enabled
        and (
            settings.ENVIRONMENT == "local"
            or (settings.turnstile_enabled and settings.SMTP_HOST != "mailcatcher")
        )
    )


def get_policy(session: Session) -> RegistrationPolicy:
    policy = session.get(RegistrationPolicy, 1)
    if policy is not None:
        return policy
    # 後台還沒存過設定：Google 自動建帳沿用原本行為，跟著信箱註冊的預設值走。
    signup_enabled = bool(settings.ENABLE_SIGNUP and _signup_ready())
    return RegistrationPolicy(
        signup_enabled=signup_enabled, google_signup_enabled=signup_enabled
    )


def update_policy(
    session: Session,
    *,
    signup_enabled: bool,
    approval_required: bool,
    google_signup_enabled: bool,
) -> RegistrationPolicy:
    # 只有信箱註冊要寄驗證信與過 Turnstile；Google 自動建帳的信箱由 Google 驗證，不受此限。
    if signup_enabled and not _signup_ready():
        raise BadRequestError(
            t("user.emailVerificationUnavailable")
            if not settings.emails_enabled
            else t("user.registrationRequirementsUnavailable")
        )
    policy = session.get(RegistrationPolicy, 1)
    if policy is None:
        policy = RegistrationPolicy(id=1)
    policy.signup_enabled = signup_enabled
    policy.approval_required = approval_required
    policy.google_signup_enabled = google_signup_enabled
    session.add(policy)
    session.commit()
    session.refresh(policy)
    return policy
