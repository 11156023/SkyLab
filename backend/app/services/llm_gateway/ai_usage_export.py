"""AI 使用量 CSV 匯出服務。

此服務只讀取 ``ai_api_usage`` 與使用者 email，不接觸 prompt／response 或任何
API secret。查詢與輸出分批進行，並以 ``(created_at, id)`` keyset cursor 固定
順序，避免大量 OFFSET 在匯出期間造成漏列或重複。
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, col, func, or_, select

from app.models import AIAPIUsage, User
from app.utils.csv import csv_safe

EXPORT_MAX_ROWS = 50_000
EXPORT_BATCH_SIZE = 500
ERROR_MESSAGE_MAX_LENGTH = 2_048

EXPORT_CSV_HEADER = [
    "id",
    "created_at",
    "started_at",
    "completed_at",
    "user_id",
    "user_email",
    "source",
    "credential_id",
    "model_name",
    "response_model",
    "call_type",
    "preset",
    "request_id",
    "upstream_request_id",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "request_duration_ms",
    "first_token_ms",
    "stream",
    "usage_reported",
    "status",
    "error_message",
]

# error_message 來自上游例外，不能假設它只包含一般錯誤文字。先移除常見的
# Authorization/API key/password 形式，再套用 CSV 公式注入防護。
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|token|secret|password|passwd)\b\s*[:=]\s*"
    r"(?:bearer\s+)?[^\s,;]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KNOWN_KEY = re.compile(r"\b(?:ccai_[A-Za-z0-9._~-]+|sk-[A-Za-z0-9._~-]+)")
_SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
    r"password)=)[^&\s]+"
)


def _sanitize_error_message(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    text = _SENSITIVE_ASSIGNMENT.sub("[REDACTED]", text)
    text = _BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    text = _KNOWN_KEY.sub("[REDACTED]", text)
    text = _SENSITIVE_QUERY.sub(r"\1[REDACTED]", text)
    return text[:ERROR_MESSAGE_MAX_LENGTH]


def _utc_iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _bool_text(value: bool | None) -> str:
    if value is None:
        return ""
    return "true" if value else "false"


def _cell(value: object) -> str:
    if value is None:
        return ""
    return csv_safe(str(value))


def _filters(
    *,
    start_date: datetime | None,
    end_date: datetime,
    source: str,
    status: str | None,
    model_name: str | None,
    call_type: str | None,
    user_id: uuid.UUID | None,
) -> list[Any]:
    filters: list[Any] = [
        AIAPIUsage.created_at <= end_date,
    ]
    if start_date is not None:
        filters.append(AIAPIUsage.created_at >= start_date)
    if source != "all":
        filters.append(AIAPIUsage.source == source)
    if status is not None:
        filters.append(AIAPIUsage.status == status)
    if model_name is not None:
        filters.append(AIAPIUsage.model_name == model_name)
    if call_type is not None:
        filters.append(AIAPIUsage.call_type == call_type)
    if user_id is not None:
        filters.append(AIAPIUsage.user_id == user_id)
    return filters


def count_rows(
    *,
    session: Session,
    start_date: datetime | None,
    end_date: datetime,
    source: str,
    status: str | None,
    model_name: str | None,
    call_type: str | None,
    user_id: uuid.UUID | None,
) -> int:
    """Count matching rows before opening the streaming response."""
    statement = select(func.count(col(AIAPIUsage.id))).select_from(AIAPIUsage)
    statement = statement.where(
        *_filters(
            start_date=start_date,
            end_date=end_date,
            source=source,
            status=status,
            model_name=model_name,
            call_type=call_type,
            user_id=user_id,
        )
    )
    return int(session.exec(statement).one() or 0)


def _row_values(usage: AIAPIUsage, user_email: str | None) -> list[str]:
    return [
        _cell(usage.id),
        _utc_iso(usage.created_at),
        _utc_iso(usage.started_at),
        _utc_iso(usage.completed_at),
        _cell(usage.user_id),
        _cell(user_email),
        _cell(usage.source),
        _cell(usage.credential_id),
        _cell(usage.model_name),
        _cell(usage.response_model),
        _cell(usage.call_type),
        _cell(usage.preset),
        _cell(usage.request_id),
        _cell(usage.upstream_request_id),
        _cell(usage.input_tokens),
        _cell(usage.output_tokens),
        _cell((usage.input_tokens or 0) + (usage.output_tokens or 0)),
        _cell(usage.request_duration_ms),
        _cell(usage.first_token_ms),
        _bool_text(usage.stream),
        _bool_text(usage.usage_reported),
        _cell(usage.status),
        _cell(_sanitize_error_message(usage.error_message)),
    ]


def export_csv_chunks(
    *,
    session: Session,
    start_date: datetime | None,
    end_date: datetime,
    source: str,
    status: str | None,
    model_name: str | None,
    call_type: str | None,
    user_id: uuid.UUID | None,
    batch_size: int = EXPORT_BATCH_SIZE,
) -> Iterator[str]:
    """Yield a UTF-8-BOM CSV header and rows in bounded DB batches."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")

    def flush() -> str:
        text = buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        return text

    writer.writerow(EXPORT_CSV_HEADER)
    yield "\ufeff" + flush()

    filters = _filters(
        start_date=start_date,
        end_date=end_date,
        source=source,
        status=status,
        model_name=model_name,
        call_type=call_type,
        user_id=user_id,
    )
    cursor: tuple[datetime, uuid.UUID] | None = None
    emitted = 0
    batch_size = max(1, min(batch_size, EXPORT_BATCH_SIZE))

    while emitted < EXPORT_MAX_ROWS:
        statement = (
            select(AIAPIUsage, User.email)
            .join(User, col(User.id) == col(AIAPIUsage.user_id))
            .order_by(col(AIAPIUsage.created_at).desc(), col(AIAPIUsage.id).desc())
        )
        statement = statement.where(*filters)
        if cursor is not None:
            last_created_at, last_id = cursor
            statement = statement.where(
                or_(
                    AIAPIUsage.created_at < last_created_at,
                    (AIAPIUsage.created_at == last_created_at)
                    & (AIAPIUsage.id < last_id),
                )
            )
        statement = statement.limit(min(batch_size, EXPORT_MAX_ROWS - emitted))

        rows = list(session.exec(statement).all())
        if not rows:
            return

        for usage, user_email in rows:
            writer.writerow(_row_values(usage, str(user_email) if user_email else None))
            yield flush()

        emitted += len(rows)
        last_usage = rows[-1][0]
        cursor = (last_usage.created_at, last_usage.id)
        if len(rows) < batch_size:
            return


__all__ = [
    "ERROR_MESSAGE_MAX_LENGTH",
    "EXPORT_BATCH_SIZE",
    "EXPORT_CSV_HEADER",
    "EXPORT_MAX_ROWS",
    "_sanitize_error_message",
    "count_rows",
    "export_csv_chunks",
]
