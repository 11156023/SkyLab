"""WireGuard peers issued to authenticated desktop devices."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, Relationship, SQLModel

from .base import get_datetime_utc


class WireGuardPeerEndpoint(SQLModel, table=True):
    """One VM endpoint (ssh/rdp) currently allowed through a peer's Gateway ACL."""

    __tablename__ = "wireguard_peer_endpoints"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    peer_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("wireguard_peers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    position: int = Field(default=0)
    vmid: int
    name: str = Field(default="", max_length=255)
    service: str = Field(max_length=8)
    host: str = Field(max_length=45)
    port: int


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
    active: bool = Field(default=False)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=get_datetime_utc),
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

    endpoint_rows: list[WireGuardPeerEndpoint] = Relationship(
        sa_relationship_kwargs={
            "order_by": "WireGuardPeerEndpoint.position",
            "cascade": "all, delete-orphan",
            "lazy": "selectin",
        }
    )

    @property
    def allowed_endpoints(self) -> list[dict[str, object]]:
        """The ACL endpoints in the dict shape the Gateway sync code compares."""
        return [
            {
                "vmid": row.vmid,
                "name": row.name,
                "service": row.service,
                "host": row.host,
                "port": row.port,
            }
            for row in self.endpoint_rows
        ]

    @allowed_endpoints.setter
    def allowed_endpoints(self, endpoints: list[dict[str, object]]) -> None:
        # 整批換掉；舊列成為 orphan 由 delete-orphan 刪除（主鍵是 uuid，不會撞鍵）
        self.endpoint_rows = [
            WireGuardPeerEndpoint(
                position=index,
                vmid=int(str(endpoint["vmid"])),
                name=str(endpoint.get("name") or ""),
                service=str(endpoint["service"]),
                host=str(endpoint["host"]),
                port=int(str(endpoint["port"])),
            )
            for index, endpoint in enumerate(endpoints)
        ]


__all__ = ["WireGuardPeer", "WireGuardPeerEndpoint"]
