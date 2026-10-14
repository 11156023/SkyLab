"""Administrator-controlled self-registration policy."""

from sqlmodel import Field, SQLModel


class RegistrationPolicy(SQLModel, table=True):
    __tablename__ = "registration_policy"

    id: int = Field(default=1, primary_key=True)
    signup_enabled: bool = Field(default=True)
    approval_required: bool = Field(default=False)
