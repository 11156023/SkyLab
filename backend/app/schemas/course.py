"""Course Lab（互動式實作教學）schemas。

flag 明文只出現在管理端的 Create/Update 輸入；所有輸出 schema 一律不含
flag_hash，學生端與管理端讀取皆然。
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.course import (
    CourseDifficulty,
    CoursePathStatus,
    CourseQuestionType,
)

# ── 管理端：路徑 ────────────────────────────────────────────────────────────


class CoursePathCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class CoursePathUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class CoursePathPublish(BaseModel):
    published: bool


class CoursePathPublic(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None = None
    status: CoursePathStatus
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    room_count: int = 0


# ── 管理端：房間 ────────────────────────────────────────────────────────────


class CourseRoomCreate(BaseModel):
    path_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    difficulty: CourseDifficulty = CourseDifficulty.easy
    category: str | None = Field(default=None, max_length=100)
    template_id: uuid.UUID | None = None
    order: int = 0


class CourseRoomUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    difficulty: CourseDifficulty | None = None
    category: str | None = Field(default=None, max_length=100)
    template_id: uuid.UUID | None = None
    clear_template: bool = False
    order: int | None = None


class CourseRoomPublic(BaseModel):
    id: uuid.UUID
    path_id: uuid.UUID
    title: str
    description: str | None = None
    difficulty: CourseDifficulty
    category: str | None = None
    template_id: uuid.UUID | None = None
    template_name: str | None = None
    order: int
    task_count: int = 0


# ── 管理端：任務 ────────────────────────────────────────────────────────────


class CourseTaskCreate(BaseModel):
    room_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(default="")
    order: int = 0


class CourseTaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = None
    order: int | None = None


class CourseTaskPublic(BaseModel):
    id: uuid.UUID
    room_id: uuid.UUID
    title: str
    content: str
    order: int


# ── 管理端：題目（flag 明文僅入不出）─────────────────────────────────────────


class CourseQuestionCreate(BaseModel):
    task_id: uuid.UUID
    prompt: str = Field(min_length=1, max_length=1000)
    question_type: CourseQuestionType = CourseQuestionType.flag
    flag: str | None = Field(default=None, max_length=500)
    points: int = Field(default=10, ge=0, le=1000)
    order: int = 0


class CourseQuestionUpdate(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=1000)
    question_type: CourseQuestionType | None = None
    flag: str | None = Field(default=None, max_length=500)
    points: int | None = Field(default=None, ge=0, le=1000)
    order: int | None = None


class CourseQuestionPublic(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    prompt: str
    question_type: CourseQuestionType
    points: int
    order: int


# ── 學生端 ─────────────────────────────────────────────────────────────────


class CoursePathSummary(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None = None
    room_count: int
    total_questions: int
    completed_questions: int
    progress_percent: float


class CourseRoomSummary(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None = None
    difficulty: CourseDifficulty
    category: str | None = None
    has_lab: bool
    order: int
    total_questions: int
    completed_questions: int
    progress_percent: float


class CoursePathDetail(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None = None
    rooms: list[CourseRoomSummary]


class CourseQuestionStudent(BaseModel):
    id: uuid.UUID
    prompt: str
    question_type: CourseQuestionType
    points: int
    order: int
    completed: bool


class CourseTaskStudent(BaseModel):
    id: uuid.UUID
    title: str
    content: str
    order: int
    questions: list[CourseQuestionStudent]


DeploymentStatus = Literal["provisioning", "running", "failed", "expired"]


class CourseDeploymentPublic(BaseModel):
    id: uuid.UUID
    room_id: uuid.UUID
    vm_request_id: uuid.UUID
    vmid: int | None = None
    status: DeploymentStatus
    error: str | None = None
    created_at: datetime
    expires_at: datetime


class CourseRoomStudentDetail(BaseModel):
    id: uuid.UUID
    path_id: uuid.UUID
    title: str
    description: str | None = None
    difficulty: CourseDifficulty
    category: str | None = None
    has_lab: bool
    tasks: list[CourseTaskStudent]
    my_deployment: CourseDeploymentPublic | None = None


class CourseAnswerSubmit(BaseModel):
    answer: str | None = Field(default=None, max_length=500)


class CourseAnswerResult(BaseModel):
    correct: bool
    question_id: uuid.UUID
    task_completed: bool
    room_progress_percent: float


# ── 老師端進度監控 ──────────────────────────────────────────────────────────


class StudentRoomProgress(BaseModel):
    room_id: uuid.UUID
    room_title: str
    total_questions: int
    completed_questions: int
    progress_percent: float


class StudentPathProgress(BaseModel):
    user_id: uuid.UUID
    user_email: str
    user_name: str | None = None
    total_questions: int
    completed_questions: int
    progress_percent: float
    rooms: list[StudentRoomProgress]


class PathProgressReport(BaseModel):
    path_id: uuid.UUID
    total_questions: int
    students: list[StudentPathProgress]


__all__ = [
    "CoursePathCreate",
    "CoursePathUpdate",
    "CoursePathPublish",
    "CoursePathPublic",
    "CourseRoomCreate",
    "CourseRoomUpdate",
    "CourseRoomPublic",
    "CourseTaskCreate",
    "CourseTaskUpdate",
    "CourseTaskPublic",
    "CourseQuestionCreate",
    "CourseQuestionUpdate",
    "CourseQuestionPublic",
    "CoursePathSummary",
    "CourseRoomSummary",
    "CoursePathDetail",
    "CourseQuestionStudent",
    "CourseTaskStudent",
    "CourseDeploymentPublic",
    "CourseRoomStudentDetail",
    "CourseAnswerSubmit",
    "CourseAnswerResult",
    "StudentRoomProgress",
    "StudentPathProgress",
    "PathProgressReport",
    "DeploymentStatus",
]
