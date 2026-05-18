"""Tunnel proxy registry: one record per VM per forwarded service."""

import secrets
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, SQLModel


class TunnelProxy(SQLModel, table=True):
    __tablename__ = "tunnel_proxies"
    __table_args__ = (
        sa.UniqueConstraint(
            "vmid",
            "service",
            name="uq_tunnel_proxies_vmid_service",
        ),
        sa.UniqueConstraint(
            "visitor_port",
            name="uq_tunnel_proxies_visitor_port",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    vmid: int = Field(
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        description="PVE VMID",
    )
    resource_vmid: int | None = Field(
        default=None,
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        description="Linked resource VMID",
    )
    user_id: uuid.UUID = Field(
        foreign_key="user.id", index=True, description="VM owner"
    )

    service: str = Field(max_length=10, description="ssh or rdp")
    internal_port: int = Field(description="Port on the VM, e.g. 22 or 3389")
    secret_key: str = Field(
        default_factory=lambda: secrets.token_urlsafe(24),
        max_length=64,
        description="STCP secret key",
    )
    proxy_name: str = Field(
        max_length=100,
        unique=True,
        description="frp proxy name, e.g. vm-150-ssh",
    )
    visitor_port: int = Field(description="Localhost port on the desktop client side")
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
