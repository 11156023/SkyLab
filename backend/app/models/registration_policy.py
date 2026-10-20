"""Administrator-controlled self-registration policy."""

from sqlmodel import Field, SQLModel


class RegistrationPolicy(SQLModel, table=True):
    __tablename__ = "registration_policy"

    id: int = Field(default=1, primary_key=True)
    signup_enabled: bool = Field(default=True)
    approval_required: bool = Field(default=False)
    # Google 首次登入自動建帳：與信箱註冊分開控制，不需要 SMTP 或 Turnstile。
    google_signup_enabled: bool = Field(default=True)
