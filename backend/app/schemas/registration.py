from pydantic import BaseModel


class RegistrationPolicyPublic(BaseModel):
    signup_enabled: bool
    approval_required: bool
    google_signup_enabled: bool
    student_email_domain: str | None = None


class RegistrationPolicyUpdate(RegistrationPolicyPublic):
    pass
