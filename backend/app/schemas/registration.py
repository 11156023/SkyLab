from pydantic import BaseModel


class RegistrationPolicyPublic(BaseModel):
    signup_enabled: bool
    approval_required: bool
    google_signup_enabled: bool


class RegistrationPolicyUpdate(RegistrationPolicyPublic):
    pass
