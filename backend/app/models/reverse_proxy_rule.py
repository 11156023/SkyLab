"""Reverse proxy rule model."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


class ReverseProxyRule(SQLModel, table=True):
    """Domain-to-VM reverse proxy rule for Traefik/Cloudflare integration."""

    __tablename__ = "reverse_proxy_rule"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    vmid: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        description="Target VM ID",
    )
    resource_vmid: int | None = Field(
        default=None,
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        description="Linked resource VMID",
    )
    vm_ip: str = Field(max_length=64, description="Target VM internal IP")

    domain: str = Field(
        max_length=255,
        sa_column_kwargs={"unique": True},
        description="Public domain, e.g. mysite.campus.edu",
    )
    zone_id: str | None = Field(
        default=None, max_length=64, description="Cloudflare Zone ID"
    )
    cloudflare_record_id: str | None = Field(
        default=None,
        max_length=64,
        description="Cloudflare DNS record ID managed by Campus Cloud",
    )
    internal_port: int = Field(ge=1, le=65535, description="VM internal port")
    enable_https: bool = Field(default=True, description="Enable HTTPS")
    dns_provider: str = Field(
        default="manual",
        max_length=32,
        description="DNS provider: manual, cloudflare, ...",
    )

    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
    )


__all__ = ["ReverseProxyRule"]
