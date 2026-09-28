"""Persistent teaching-class orchestration models."""

import enum
import uuid
from datetime import date, datetime, time

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, SQLModel, UniqueConstraint

from .base import get_datetime_utc


class TeachingClassStatus(str, enum.Enum):
    planning = "planning"
    pending_review = "pending_review"
    provisioning = "provisioning"
    partial_failed = "partial_failed"
    active = "active"
    archived = "archived"


class TeachingClass(SQLModel, table=True):
    __tablename__ = "teaching_classes"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        )
    )
    name: str = Field(max_length=255)
    code: str = Field(max_length=80)
    term: str = Field(max_length=80)
    location: str | None = Field(default=None, max_length=255)
    start_date: date
    end_date: date
    weekday: int = Field(ge=0, le=6, description="Monday=0")
    start_time: time
    end_time: time
    timezone: str = Field(default="Asia/Taipei", max_length=64)
    boot_lead_minutes: int = Field(default=10, ge=0, le=120)
    shutdown_grace_minutes: int = Field(default=30, ge=0, le=240)
    course_version_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_environment_versions.id", ondelete="RESTRICT"),
            nullable=True,
            index=True,
        ),
    )
    locked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    status: TeachingClassStatus = Field(
        default=TeachingClassStatus.planning,
        sa_column=Column(sa.Enum(TeachingClassStatus), nullable=False, index=True),
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=get_datetime_utc),
    )
    archived_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    reclaim_requested_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    resources_reclaimed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class TeachingClassMachineNode(SQLModel, table=True):
    __tablename__ = "teaching_class_machine_nodes"
    __table_args__ = (
        sa.CheckConstraint(
            "source_type IN ('template', 'custom')",
            name="ck_teaching_class_machine_nodes_source_type",
        ),
        UniqueConstraint("class_id", "node_key", name="uq_teaching_class_machine_node"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    class_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("teaching_classes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    node_key: str = Field(max_length=80)
    source_type: str = Field(default="template", max_length=16)
    source_template_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.ForeignKey("vm_templates.id", ondelete="RESTRICT"), nullable=True, index=True
        ),
    )
    custom_image_ref: str | None = Field(default=None, max_length=500)
    custom_storage: str | None = Field(default=None, max_length=120)
    custom_username: str | None = Field(default=None, max_length=32)
    custom_unprivileged: bool = Field(default=True)
    name: str = Field(max_length=255)
    role: str = Field(max_length=120)
    resource_type: str = Field(max_length=10)
    cpu: int = Field(ge=1, le=64)
    memory_mb: int = Field(ge=128, le=131072)
    disk_gb: int = Field(ge=1, le=2000)
    network: str | None = Field(default=None, max_length=255)
    sort_order: int = Field(default=0)
    batch_job_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.ForeignKey("batch_provision_jobs.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )


class TeachingClassWeek(SQLModel, table=True):
    __tablename__ = "teaching_class_weeks"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'completed')",
            name="ck_teaching_class_weeks_status",
        ),
        UniqueConstraint("class_id", "week_number", name="uq_teaching_class_week"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    class_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("teaching_classes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    week_number: int = Field(ge=1)
    session_date: date
    title: str = Field(default="", max_length=255)
    target_node_key: str | None = Field(default=None, max_length=80)
    status: str = Field(default="draft", max_length=24)


class TeachingClassTaskFile(SQLModel, table=True):
    __tablename__ = "teaching_class_task_files"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    week_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("teaching_class_weeks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    filename: str = Field(max_length=255)
    storage_key: str | None = Field(default=None, max_length=500)
    target_path: str | None = Field(default=None, max_length=500)


# 班級擁有者自己的那一列：跟學生一樣佔容量、拿一整套機器、套同一份拓樸，
# 但學生端的課程、提醒、評分都只看 status == "active"，不會把老師算進去。
INSTRUCTOR_ENROLLMENT_STATUS = "instructor"


class TeachingClassStudent(SQLModel, table=True):
    __tablename__ = "teaching_class_students"
    __table_args__ = (
        UniqueConstraint("class_id", "user_id", name="uq_teaching_class_student"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    class_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("teaching_classes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    user_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
        )
    )
    status: str = Field(default="active", max_length=24)
    joined_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class TeachingClassStudentMachine(SQLModel, table=True):
    __tablename__ = "teaching_class_student_machines"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'reclaimed')",
            name="ck_teaching_class_student_machines_status",
        ),
        UniqueConstraint(
            "class_student_id",
            "machine_node_id",
            name="uq_teaching_class_student_machine",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    class_student_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("teaching_class_students.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    machine_node_id: uuid.UUID = Field(
        sa_column=Column(
            sa.ForeignKey("teaching_class_machine_nodes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    batch_task_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.ForeignKey("batch_provision_tasks.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    vmid: int | None = Field(default=None)
    status: str = Field(default="pending", max_length=32)
    error: str | None = Field(default=None, max_length=500)


__all__ = [
    "INSTRUCTOR_ENROLLMENT_STATUS",
    "TeachingClass",
    "TeachingClassStatus",
    "TeachingClassMachineNode",
    "TeachingClassWeek",
    "TeachingClassTaskFile",
    "TeachingClassStudent",
    "TeachingClassStudentMachine",
]
