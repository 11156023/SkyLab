from pydantic import BaseModel


class RegistrationPolicyPublic(BaseModel):
    signup_enabled: bool
    approval_required: bool


class RegistrationPolicyUpdate(RegistrationPolicyPublic):
    pass
