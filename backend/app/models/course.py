"""互動式實作教學系統（Course Lab）模型。

學習路徑 → 房間 → 任務 → 題目 四層結構；
進度記在 question 層（任務完成 = 該任務所有題目完成，百分比為衍生查詢）。
CourseDeployment 是課程域與 VM 域的唯一接點（部署狀態 join vm_requests 取得）。
"""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, SQLModel, UniqueConstraint

from .base import get_datetime_utc


class CoursePathStatus(str, enum.Enum):
    draft = "draft"
    published = "published"


class CourseDifficulty(str, enum.Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class CourseQuestionType(str, enum.Enum):
    flag = "flag"
    no_answer = "no_answer"


class CoursePath(SQLModel, table=True):
    """學習路徑（發布制：published 後全站學生可見）"""

    __tablename__ = "course_paths"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    status: CoursePathStatus = Field(
        default=CoursePathStatus.draft,
        sa_column=Column(
            Enum(CoursePathStatus),
            nullable=False,
            default=CoursePathStatus.draft,
            index=True,
        ),
    )
    created_by: uuid.UUID | None = Field(
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
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CourseRoom(SQLModel, table=True):
    """房間：綁定範本系統 2.0 範本（NULL = 純理論房）"""

    __tablename__ = "course_rooms"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    path_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_paths.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    title: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    difficulty: CourseDifficulty = Field(
        default=CourseDifficulty.easy,
        sa_column=Column(
            Enum(CourseDifficulty),
            nullable=False,
            default=CourseDifficulty.easy,
        ),
    )
    category: str | None = Field(default=None, max_length=100)
    template_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("vm_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    order: int = Field(default=0)


class CourseTask(SQLModel, table=True):
    """任務：Markdown 教學內容"""

    __tablename__ = "course_tasks"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    room_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_rooms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    title: str = Field(max_length=255)
    content: str = Field(sa_column=Column(sa.Text, nullable=False))
    order: int = Field(default=0)


class CourseQuestion(SQLModel, table=True):
    """題目：flag（SHA-256 hash 比對）或 no_answer（閱讀型，點完成即可）"""

    __tablename__ = "course_questions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    task_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_tasks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    prompt: str = Field(max_length=1000)
    question_type: CourseQuestionType = Field(
        default=CourseQuestionType.flag,
        sa_column=Column(
            Enum(CourseQuestionType),
            nullable=False,
            default=CourseQuestionType.flag,
        ),
    )
    flag_hash: str | None = Field(default=None, max_length=64)
    points: int = Field(default=10)
    order: int = Field(default=0)


class UserCourseProgress(SQLModel, table=True):
    """學生答題進度（question 層；一列 = 一題已完成）"""

    __tablename__ = "user_course_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "question_id", name="uq_user_course_progress"),
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
    question_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_questions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    completed_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CourseDeployment(SQLModel, table=True):
    """課程實驗機部署記錄（expires_at = 對應 VMRequest 的 end_at 冗餘）"""

    __tablename__ = "course_deployments"
    __table_args__ = (
        sa.Index("ix_course_deployments_user_expires", "user_id", "expires_at"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    room_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("course_rooms.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    user_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    vm_request_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("vm_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = [
    "CoursePath",
    "CoursePathStatus",
    "CourseRoom",
    "CourseDifficulty",
    "CourseTask",
    "CourseQuestion",
    "CourseQuestionType",
    "UserCourseProgress",
    "CourseDeployment",
]
