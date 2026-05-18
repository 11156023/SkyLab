"""NAT forwarding rule model."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


class NatRule(SQLModel, table=True):
    """Source of truth for nftables DNAT forwarding rules."""

    __tablename__ = "nat_rule"
    __table_args__ = (
        sa.UniqueConstraint(
            "ssh_host",
            "external_port",
            "protocol",
            name="uq_nat_rule_host_external_port_protocol",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    ssh_host: str = Field(max_length=255, description="PVE host where the rule is applied")

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

    external_port: int = Field(ge=1, le=65535, description="External port")
    internal_port: int = Field(ge=1, le=65535, description="VM internal port")
    protocol: str = Field(default="tcp", max_length=16, description="tcp or udp")

    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
    )


__all__ = ["NatRule"]
