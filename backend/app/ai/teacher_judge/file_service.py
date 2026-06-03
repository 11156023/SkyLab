"""Teacher Judge uploaded rubric file lifecycle service."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, desc, func, select

from app.ai.teacher_judge.schemas import (
    TeacherJudgeFilePublic,
    TeacherJudgeRubricAnalysis,
)
from app.models.teacher_judge_file import TeacherJudgeFile, TeacherJudgeFileStatus
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptArtifact
from app.services.rubric_parser import parse_document

ConflictStrategy = Literal["overwrite", "copy"]

DATA_ROOT = Path(__file__).resolve().parents[4] / "data" / "teacher-judge" / "files"
logger = logging.getLogger(__name__)


def _now():
    from app.models.base import get_datetime_utc

    return get_datetime_utc()


def _safe_filename(filename: str) -> str:
    name = Path(filename or "rubric").name.strip()
    return name or "rubric"


def _suffix(filename: str) -> str:
    return Path(filename).suffix.lower()


def _stored_path(file_id: uuid.UUID, original_filename: str) -> Path:
    return DATA_ROOT / f"{file_id}{_suffix(original_filename)}"


def _temp_path(file_id: uuid.UUID, original_filename: str) -> Path:
    return DATA_ROOT / f"{file_id}{_suffix(original_filename)}.tmp"


def _backup_path(file_id: uuid.UUID, original_filename: str) -> Path:
    return DATA_ROOT / f"{file_id}{_suffix(original_filename)}.bak"


def _deleted_path(file_id: uuid.UUID, original_filename: str) -> Path:
    return DATA_ROOT / f"{file_id}{_suffix(original_filename)}.deleted"


def _unlink_if_exists(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        logger.warning("Failed to remove Teacher Judge file path: %s", path)


def _raise_name_conflict(existing: TeacherJudgeFile | None = None) -> None:
    detail: dict[str, str] = {
        "code": "teacher_judge_file_name_conflict",
        "message": "已有同名評分表。",
    }
    if existing is not None:
        detail["file_id"] = str(existing.id)
        detail["original_filename"] = existing.original_filename
    raise HTTPException(
        status_code=409,
        detail=detail,
    )


def _file_to_public(file: TeacherJudgeFile) -> TeacherJudgeFilePublic:
    return TeacherJudgeFilePublic(
        id=str(file.id),
        group_id=str(file.group_id),
        uploaded_by=str(file.uploaded_by) if file.uploaded_by else None,
        original_filename=file.original_filename,
        file_hash=file.file_hash,
        template_key=file.template_key,
        analysis_json=file.analysis_json,
        status=file.status.value,
        created_at=file.created_at.isoformat(),
        updated_at=file.updated_at.isoformat(),
    )


def _active_file_by_name(
    *,
    session: Session,
    group_id: uuid.UUID,
    original_filename: str,
    for_update: bool = False,
) -> TeacherJudgeFile | None:
    statement = select(TeacherJudgeFile).where(
        TeacherJudgeFile.group_id == group_id,
        TeacherJudgeFile.original_filename == original_filename,
        TeacherJudgeFile.status == TeacherJudgeFileStatus.active,
    )
    if for_update:
        statement = statement.with_for_update()
    return session.exec(statement).first()


def raise_if_file_name_conflict(
    *,
    session: Session,
    group_id: uuid.UUID,
    original_filename: str,
    conflict_strategy: ConflictStrategy | None,
) -> None:
    if conflict_strategy is not None:
        return
    existing = _active_file_by_name(
        session=session,
        group_id=group_id,
        original_filename=original_filename,
    )
    if existing is None:
        return
    _raise_name_conflict(existing)


def _linked_script_count(*, session: Session, file_id: uuid.UUID) -> int:
    count = session.exec(
        select(func.count()).select_from(TeacherJudgeScriptArtifact).where(
            TeacherJudgeScriptArtifact.source_file_id == file_id
        )
    ).one()
    return int(count or 0)


def _copy_filename(
    *,
    session: Session,
    group_id: uuid.UUID,
    original_filename: str,
) -> str:
    path = Path(original_filename)
    stem = path.stem or "rubric"
    suffix = path.suffix
    existing = set(
        session.exec(
            select(TeacherJudgeFile.original_filename).where(
                TeacherJudgeFile.group_id == group_id
            )
        ).all()
    )
    for index in range(2, 1000):
        candidate = f"{stem} ({index}){suffix}"
        if candidate not in existing:
            return candidate
    raise HTTPException(status_code=409, detail="無法建立同名副本，請重新命名檔案。")


def _file_snapshot(file: TeacherJudgeFile | None) -> dict[str, Any]:
    if file is None:
        return {}
    return {
        "id": str(file.id),
        "original_filename": file.original_filename,
        "file_hash": file.file_hash,
        "template_key": file.template_key,
        "status": file.status.value,
        "created_at": file.created_at.isoformat(),
        "updated_at": file.updated_at.isoformat(),
    }


def list_files(
    *,
    session: Session,
    group_id: uuid.UUID,
) -> list[TeacherJudgeFilePublic]:
    files = session.exec(
        select(TeacherJudgeFile)
        .where(TeacherJudgeFile.group_id == group_id)
        .order_by(desc(TeacherJudgeFile.created_at))
    ).all()
    return [_file_to_public(file) for file in files]


def get_file(
    *,
    session: Session,
    group_id: uuid.UUID,
    file_id: uuid.UUID,
) -> TeacherJudgeFile:
    file = session.get(TeacherJudgeFile, file_id)
    if file is None or file.group_id != group_id:
        raise HTTPException(status_code=404, detail="Teacher Judge file not found")
    return file


def get_file_download(
    *,
    session: Session,
    group_id: uuid.UUID,
    file_id: uuid.UUID,
) -> tuple[Path, str]:
    file = get_file(session=session, group_id=group_id, file_id=file_id)
    path = _stored_path(file.id, file.original_filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="原始評分表檔案不存在。")
    return path, file.original_filename


def prepare_file_payload(
    *,
    filename: str,
    file_bytes: bytes,
    allowed_suffixes: set[str],
    max_upload_size_bytes: int,
) -> tuple[str, str, str]:
    original_filename = _safe_filename(filename)
    suffix = _suffix(original_filename)
    if suffix not in allowed_suffixes:
        raise HTTPException(
            status_code=415,
            detail=f"不支援的格式 '{suffix}'，目前接受：{', '.join(sorted(allowed_suffixes))}",
        )
    if len(file_bytes) > max_upload_size_bytes:
        file_size_mb = len(file_bytes) / (1024 * 1024)
        max_size_mb = max_upload_size_bytes / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"檔案大小 {file_size_mb:.1f}MB 超過限制（最大 {max_size_mb:.0f}MB）",
        )
    if not file_bytes:
        raise HTTPException(status_code=400, detail="上傳的檔案是空的。")
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    raw_text = parse_document(original_filename, file_bytes)
    if not raw_text.strip():
        raise HTTPException(
            status_code=422,
            detail="無法從文件中提取任何文字，請確認文件不是掃描版 PDF。",
        )
    return original_filename, file_hash, raw_text


def save_analyzed_file(
    *,
    session: Session,
    group_id: uuid.UUID,
    uploaded_by: uuid.UUID | None,
    original_filename: str,
    file_hash: str,
    template_key: str,
    file_bytes: bytes,
    analysis: TeacherJudgeRubricAnalysis,
    conflict_strategy: ConflictStrategy | None,
) -> TeacherJudgeFilePublic:
    existing = _active_file_by_name(
        session=session,
        group_id=group_id,
        original_filename=original_filename,
        for_update=conflict_strategy == "overwrite",
    )
    target_filename = original_filename
    target_file: TeacherJudgeFile | None = None
    now = _now()

    if existing is not None and conflict_strategy is None:
        raise_if_file_name_conflict(
            session=session,
            group_id=group_id,
            original_filename=original_filename,
            conflict_strategy=conflict_strategy,
        )

    if existing is not None and conflict_strategy == "copy":
        target_filename = _copy_filename(
            session=session,
            group_id=group_id,
            original_filename=original_filename,
        )
    elif existing is not None and conflict_strategy == "overwrite":
        if _linked_script_count(session=session, file_id=existing.id) > 0:
            existing.status = TeacherJudgeFileStatus.replaced
            existing.updated_at = now
            session.add(existing)
        else:
            target_file = existing

    if target_file is None:
        target_file = TeacherJudgeFile(
            group_id=group_id,
            uploaded_by=uploaded_by,
            original_filename=target_filename,
            file_hash=file_hash,
            template_key=template_key,
            analysis_json=analysis.model_dump(mode="json"),
            status=TeacherJudgeFileStatus.active,
            updated_at=now,
        )
    else:
        target_file.uploaded_by = uploaded_by
        target_file.file_hash = file_hash
        target_file.template_key = template_key
        target_file.analysis_json = analysis.model_dump(mode="json")
        target_file.status = TeacherJudgeFileStatus.active
        target_file.updated_at = now
        target_file.original_filename = target_filename

    session.add(target_file)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    session.flush()

    final_path = _stored_path(target_file.id, target_file.original_filename)
    temp_path = _temp_path(target_file.id, target_file.original_filename)
    backup_path = _backup_path(target_file.id, target_file.original_filename)
    backed_up_existing = False
    try:
        temp_path.write_bytes(file_bytes)
        if final_path.exists():
            _unlink_if_exists(backup_path)
            os.replace(final_path, backup_path)
            backed_up_existing = True
        os.replace(temp_path, final_path)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        _unlink_if_exists(final_path)
        if backed_up_existing and backup_path.exists():
            os.replace(backup_path, final_path)
        _raise_name_conflict()
        raise AssertionError("unreachable") from exc
    except Exception:
        session.rollback()
        _unlink_if_exists(temp_path)
        _unlink_if_exists(final_path)
        if backed_up_existing and backup_path.exists():
            os.replace(backup_path, final_path)
        raise
    else:
        _unlink_if_exists(backup_path)
    session.refresh(target_file)
    return _file_to_public(target_file)


def update_file_analysis(
    *,
    session: Session,
    group_id: uuid.UUID,
    file_id: uuid.UUID,
    analysis: TeacherJudgeRubricAnalysis,
) -> TeacherJudgeFilePublic:
    file = get_file(session=session, group_id=group_id, file_id=file_id)
    file.analysis_json = analysis.model_dump(mode="json")
    file.updated_at = _now()
    session.add(file)
    session.commit()
    session.refresh(file)
    return _file_to_public(file)


def delete_file(
    *,
    session: Session,
    group_id: uuid.UUID,
    file_id: uuid.UUID,
) -> None:
    file = get_file(session=session, group_id=group_id, file_id=file_id)
    path = _stored_path(file.id, file.original_filename)
    deleted_path = _deleted_path(file.id, file.original_filename)
    if path.exists():
        _unlink_if_exists(deleted_path)
        os.replace(path, deleted_path)
    linked_artifacts = session.exec(
        select(TeacherJudgeScriptArtifact).where(
            TeacherJudgeScriptArtifact.source_file_id == file.id
        )
    ).all()
    for artifact in linked_artifacts:
        artifact.source_file_id = None
        session.add(artifact)
    session.delete(file)
    try:
        session.commit()
    except Exception:
        session.rollback()
        if deleted_path.exists():
            os.replace(deleted_path, path)
        raise
    _unlink_if_exists(deleted_path)


def source_file_snapshot(
    *,
    session: Session,
    group_id: uuid.UUID,
    file_id: uuid.UUID | None,
) -> tuple[TeacherJudgeFile | None, dict[str, Any]]:
    if file_id is None:
        return None, {}
    file = get_file(session=session, group_id=group_id, file_id=file_id)
    return file, _file_snapshot(file)


def parse_conflict_strategy(value: str | None) -> ConflictStrategy | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized not in {"overwrite", "copy"}:
        raise HTTPException(status_code=400, detail="未知的同名檔案處理方式。")
    return cast("ConflictStrategy", normalized)
