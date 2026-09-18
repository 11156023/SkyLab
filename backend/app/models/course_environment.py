"""Reusable, versioned per-student course environments."""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import (
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    Field,
    SQLModel,
    UniqueConstraint,
)

from .base import get_datetime_utc


class CourseEnvironmentVersionStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    retired = "retired"


class CourseEnvironment(SQLModel, table=True):
    """Stable identity for a reusable course environment."""

    __tablename__ = "course_environments"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    usage_scope: str = Field(
        default="course",
        max_length=24,
        description="course, quick_practice, or both",
    )
    max_concurrent_sessions: int | None = Field(
        default=None,
        description=(
            "Quick-practice sessions this environment may run at once across "
            "all students; None means only the per-student limits apply"
        ),
    )
    audience: str = Field(
        default="class",
        max_length=24,
        description=(
            "Who may see this environment in the student quick-practice list: "
            "owner (nobody but the teacher), class (the linked classes' "
            "students), or campus (every signed-in user)"
        ),
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CourseEnvironmentAudience(SQLModel, table=True):
    """Classes whose students may see an ``audience="class"`` environment."""

    __tablename__ = "course_environment_audiences"
    __table_args__ = (
        UniqueConstraint(
            "environment_id",
            "class_id",
            name="uq_course_environment_audience",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    environment_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_environments.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    class_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("teaching_classes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CourseEnvironmentFile(SQLModel, table=True):
    """老師掛在環境上的說明文件。

    綁在環境身分而不是版本上：換版本是機器設定改了，講義不該跟著消失。
    """

    __tablename__ = "course_environment_files"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    environment_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_environments.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    filename: str = Field(max_length=255)
    storage_key: str = Field(max_length=255, unique=True)
    size_bytes: int = Field(default=0, ge=0)
    uploaded_by: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CourseEnvironmentVersion(SQLModel, table=True):
    """Immutable after publication; a class pins exactly one published version."""

    __tablename__ = "course_environment_versions"
    __table_args__ = (
        UniqueConstraint(
            "environment_id",
            "version",
            name="uq_course_environment_version",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    environment_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_environments.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    version: int = Field(ge=1)
    status: CourseEnvironmentVersionStatus = Field(
        default=CourseEnvironmentVersionStatus.draft,
        sa_column=Column(
            Enum(CourseEnvironmentVersionStatus),
            nullable=False,
            default=CourseEnvironmentVersionStatus.draft,
            index=True,
        ),
    )
    configuration_hash: str | None = Field(default=None, max_length=64)
    # 機器之間怎麼互通：
    # - explicit：只開老師在拓撲圖上畫的連線；一條都沒畫就是完全隔離。
    # - segment：舊行為，共用邏輯網段的機器全協定全埠互通，畫的線視為多餘。
    # 以前是「沒畫線就全通、畫了第一條就變白名單」，老師以為沒連線等於隔離，
    # 實際上是全開；改成顯式欄位讓兩種意圖分開表達。
    peer_policy: str = Field(default="explicit", max_length=16)
    # Unfinished editor content is kept apart from deployable configuration.
    draft_data: str | None = Field(
        default=None, sa_column=Column(sa.Text, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    published_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class CourseEnvironmentNode(SQLModel, table=True):
    """One machine in the environment issued to every enrolled student."""

    __tablename__ = "course_environment_nodes"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "node_key",
            name="uq_course_environment_version_node",
        ),
        CheckConstraint(
            "("
            "source_type = 'template' AND source_template_id IS NOT NULL "
            "AND custom_image_ref IS NULL"
            ") OR ("
            "source_type = 'custom' AND source_template_id IS NULL "
            "AND custom_image_ref IS NOT NULL"
            ")",
            name="ck_course_environment_node_source",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    version_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_environment_versions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    node_key: str = Field(max_length=80)
    source_type: str = Field(default="template", max_length=16)
    source_template_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("vm_templates.id", ondelete="RESTRICT"),
            nullable=True,
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
    # Comma-separated logical segments. Students get independent instances of
    # segments with the same name; it is not a raw PVE bridge name.
    network: str = Field(default="lab-net", max_length=255)
    position_x: float = Field(default=80.0, ge=-5000, le=5000)
    position_y: float = Field(default=120.0, ge=-5000, le=5000)
    sort_order: int = Field(default=0)


class CourseEnvironmentPublication(SQLModel, table=True):
    """一條「外網 → 機器」的宣告。

    每位學生都會拿到一份自己的環境，所以網址不能寫死在模板上：老師只填
    主機名樣板（含 ``{student}``），實際網域在開課／開練習時逐人組出來。
    """

    __tablename__ = "course_environment_publications"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "node_key",
            "port",
            "protocol",
            name="uq_course_environment_publication",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    version_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_environment_versions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    node_key: str = Field(max_length=80)
    # domain = 給每位學生一個對外網址；port_forward = 開課時逐人從池子配一個
    # 對外 port（見 nat_service.allocate_external_port）。
    # 以前還有 firewall_only（只開入站規則）：那條規則沒有 source 限制，等於對
    # 整個實驗室子網敞開，而 Gateway VM 本來就有全埠 ACCEPT，正當用途已被涵蓋，
    # 所以拿掉了；舊資料由 migration 轉成 port_forward。
    mode: str = Field(default="domain", max_length=16)
    port: int = Field(ge=1, le=65535, description="機器內部 port")
    protocol: str = Field(default="tcp", max_length=16)
    # 主機名樣板，例如 "{student}-n8n"；僅 mode=domain 有意義
    hostname_prefix: str | None = Field(default=None, max_length=120)
    zone_id: str | None = Field(default=None, max_length=64)
    enable_https: bool = Field(default=True)
    sort_order: int = Field(default=0)


class CourseEnvironmentEdge(SQLModel, table=True):
    """A firewall-style connection between two nodes in one course version."""

    __tablename__ = "course_environment_edges"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "source_node_key",
            "target_node_key",
            "direction",
            "protocol",
            "port",
            name="uq_course_environment_edge",
        ),
        CheckConstraint(
            "source_node_key <> target_node_key",
            name="ck_course_environment_edge_distinct_nodes",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    version_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_environment_versions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    source_node_key: str = Field(max_length=80)
    target_node_key: str = Field(max_length=80)
    direction: str = Field(default="one_way", max_length=16)
    protocol: str = Field(default="tcp", max_length=8)
    port: int | None = Field(default=22, ge=1, le=65535)


class ClassCapacityReservation(SQLModel, table=True):
    """Atomic whole-class capacity snapshot created before batch jobs."""

    __tablename__ = "class_capacity_reservations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    class_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("teaching_classes.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        )
    )
    course_version_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_environment_versions.id", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    student_count: int = Field(ge=1)
    machine_count: int = Field(ge=1)
    cpu_cores: int = Field(ge=1)
    memory_mb: int = Field(ge=1)
    disk_gb: int = Field(ge=1)
    ip_count: int = Field(ge=1)
    network_count: int = Field(ge=1)
    placement_plan: str = Field(
        default="{}",
        sa_column=Column(sa.Text, nullable=False),
    )
    # {machine_node_id: {user_id: 節點名}} —— 整班固定在同一個叢集，但叢集內
    # 依容量把學生分散到不同節點（同一個叢集不代表同一台 server）。預留時
    # 定案並存下，建機時查表，避免兩個時間點各自重算而與預留不一致。
    student_placements: str = Field(
        default="{}",
        sa_column=Column(sa.Text, nullable=False, server_default="{}"),
    )
    status: str = Field(default="reserved", max_length=24)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = [
    "ClassCapacityReservation",
    "CourseEnvironment",
    "CourseEnvironmentEdge",
    "CourseEnvironmentFile",
    "CourseEnvironmentNode",
    "CourseEnvironmentVersion",
    "CourseEnvironmentVersionStatus",
]
