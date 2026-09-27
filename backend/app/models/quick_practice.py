"""Multi-machine quick-practice sessions launched from published environments."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, SQLModel, UniqueConstraint

from .base import get_datetime_utc


class QuickPracticeSession(SQLModel, table=True):
    __tablename__ = "quick_practice_sessions"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('creating', 'ready', 'partial_failed', 'stopping', 'reclaiming', 'reclaimed')",
            name="ck_quick_practice_sessions_status",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    environment_version_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_environment_versions.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )
    status: str = Field(default="creating", max_length=24, index=True)
    topology_applied_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    reclaim_started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    reclaimed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )
    last_error: str | None = Field(default=None, max_length=2000)


class QuickPracticeSessionMachine(SQLModel, table=True):
    __tablename__ = "quick_practice_session_machines"
    __table_args__ = (
        sa.CheckConstraint(
            "resource_type IN ('qemu', 'lxc')",
            name="ck_quick_practice_session_machines_resource_type",
        ),
        UniqueConstraint(
            "session_id",
            "node_key",
            name="uq_quick_practice_session_node",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("quick_practice_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    vm_request_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("vm_requests.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        )
    )
    node_key: str = Field(max_length=80)
    name: str = Field(max_length=255)
    role: str = Field(max_length=120)
    resource_type: str = Field(max_length=10)
    sort_order: int = Field(default=0)


__all__ = ["QuickPracticeSession", "QuickPracticeSessionMachine"]
