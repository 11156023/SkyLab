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
    return RegistrationPolicy(signup_enabled=settings.ENABLE_SIGNUP and _signup_ready())


def update_policy(
    session: Session, *, signup_enabled: bool, approval_required: bool
) -> RegistrationPolicy:
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
    session.add(policy)
    session.commit()
    session.refresh(policy)
    return policy
