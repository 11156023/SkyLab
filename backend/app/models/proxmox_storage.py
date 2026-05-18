"""Proxmox storage pool model."""

import sqlalchemy as sa
from sqlmodel import Field, SQLModel, UniqueConstraint


class ProxmoxStorage(SQLModel, table=True):
    """Storage pool state and placement tuning for a Proxmox node."""

    __tablename__ = "proxmox_storages"
    __table_args__ = (
        UniqueConstraint("node_name", "storage", name="uq_proxmox_storages_node_storage"),
    )

    id: int | None = Field(default=None, primary_key=True)
    node_name: str = Field(
        sa_column=sa.Column(
            sa.String(length=255),
            sa.ForeignKey("proxmox_nodes.name", ondelete="CASCADE"),
            nullable=False,
        )
    )
    storage: str = Field(max_length=255)
    storage_type: str | None = Field(default=None, max_length=50)
    total_gb: float = Field(default=0.0)
    used_gb: float = Field(default=0.0)
    avail_gb: float = Field(default=0.0)
    can_vm: bool = Field(default=False)
    can_lxc: bool = Field(default=False)
    can_iso: bool = Field(default=False)
    can_backup: bool = Field(default=False)
    is_shared: bool = Field(default=False)
    active: bool = Field(default=True)
    enabled: bool = Field(default=True)
    speed_tier: str = Field(default="unknown", max_length=20)
    user_priority: int = Field(default=5, ge=1, le=10)


__all__ = ["ProxmoxStorage"]
