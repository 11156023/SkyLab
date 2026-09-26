"""WireGuard peers issued to authenticated desktop devices."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, SQLModel

from .base import get_datetime_utc


class WireGuardPeer(SQLModel, table=True):
    __tablename__ = "wireguard_peers"
    __table_args__ = (
        sa.UniqueConstraint(
            "user_id",
            "device_id",
            name="uq_wireguard_peers_user_device",
        ),
        sa.UniqueConstraint("public_key", name="uq_wireguard_peers_public_key"),
        sa.UniqueConstraint("tunnel_ip", name="uq_wireguard_peers_tunnel_ip"),
        sa.Index("ix_wireguard_peers_user_id", "user_id"),
        sa.Index("ix_wireguard_peers_active_expires", "active", "expires_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    device_id: str = Field(max_length=128)
    public_key: str = Field(max_length=64)
    tunnel_ip: str = Field(max_length=45)
    allowed_endpoints: list[dict[str, object]] = Field(
        default_factory=list,
        sa_column=Column(sa.JSON, nullable=False, default=list),
    )
    active: bool = Field(default=False)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    last_connected_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    revoked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


__all__ = ["WireGuardPeer"]
