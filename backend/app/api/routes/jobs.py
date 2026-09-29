"""統一 Jobs API 路由。

提供 Job 中心使用的彙整查詢端點：
- GET /jobs/             清單（支援 kinds / statuses / active_only / 分頁）
- GET /jobs/recent       Banner popover 用：最近 N 筆 + active_count
"""

from __future__ import annotations

import enum
from typing import TypeVar

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, SessionDep
from app.schemas.jobs import JobDetail, JobKind, JobsListResponse, JobStatus
from app.services.jobs import jobs_service
from app.services.jobs.jobs_service import (
    JobAccessDeniedError,
    JobNotFoundError,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])

_E = TypeVar("_E", bound=enum.Enum)


def _parse_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


def _parse_enum_csv(value: str | None, enum_cls: type[_E]) -> list[_E] | None:
    """逗號分隔字串 → 枚舉清單。

    未帶參數（或只有空白）回 None＝不篩選；有帶但全都不認得時回空清單，
    不認得的值直接略過。
    """
    csv = _parse_csv(value)
    if csv is None:
        return None
    parsed: list[_E] = []
    for token in csv:
        try:
            parsed.append(enum_cls(token))
        except ValueError:
            continue
    return parsed


def _enum_values(enum_cls: type[enum.Enum]) -> str:
    return ", ".join(str(member.value) for member in enum_cls)


@router.get("/", response_model=JobsListResponse)
def list_unified_jobs(
    session: SessionDep,
    current_user: CurrentUser,
    kinds: str | None = Query(
        default=None,
        description=f"逗號分隔，可選: {_enum_values(JobKind)}",
    ),
    statuses: str | None = Query(
        default=None,
        description=f"逗號分隔: {_enum_values(JobStatus)}",
    ),
    active_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    history_days: int = Query(default=30, ge=1, le=365),
) -> JobsListResponse:
    return jobs_service.list_jobs(
        session=session,
        user=current_user,
        kinds=_parse_enum_csv(kinds, JobKind),
        statuses=_parse_enum_csv(statuses, JobStatus),
        active_only=active_only,
        limit=limit,
        offset=offset,
        history_days=history_days,
    )


@router.get("/recent", response_model=JobsListResponse)
def list_recent_jobs(
    session: SessionDep,
    current_user: CurrentUser,
    limit: int = Query(default=5, ge=1, le=20),
) -> JobsListResponse:
    return jobs_service.list_recent_for_user(
        session=session, user=current_user, limit=limit
    )


@router.get("/{job_id:path}", response_model=JobDetail)
def get_job(
    job_id: str,
    session: SessionDep,
    current_user: CurrentUser,
) -> JobDetail:
    """job_id 為複合 ID：<kind>:<source_id>，例如 `vm_request:<id>`。"""
    try:
        return jobs_service.get_job_detail(
            session=session, user=current_user, job_id=job_id
        )
    except JobNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except JobAccessDeniedError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
