"""Cloudflare provider configuration model."""

from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


class CloudflareConfig(SQLModel, table=True):
    """Singleton Cloudflare configuration used by admin-only domain tooling."""

    __tablename__ = "cloudflare_config"

    id: int = Field(default=1, primary_key=True)
    account_id: str = Field(default="", max_length=128)
    encrypted_api_token: str = Field(
        default="",
        sa_column=sa.Column(sa.Text(), nullable=False),
    )
    default_dns_target_type: str = Field(default="", max_length=16)
    default_dns_target_value: str = Field(default="", max_length=255)
    last_verified_at: datetime | None = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=True),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False),
    )


__all__ = ["CloudflareConfig"]
