"""群組管理 API 路由"""

import csv
import io
import logging
import secrets
import threading
import time
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.api.deps import InstructorUser, SessionDep
from app.core.authorizers import (
    can_bypass_group_ownership,
    require_group_access,
)
from app.core.config import settings
from app.infrastructure.proxmox import operations as proxmox_ops
from app.repositories import group as group_repo
from app.repositories.user import create_user as create_user_in_db
from app.repositories.user import get_user_by_email
from app.schemas.common import Message
from app.schemas.group import (
    CsvImportResult,
    GroupCreate,
    GroupDetailPublic,
    GroupMemberAdd,
    GroupMemberPublic,
    GroupPublic,
    GroupsPublic,
)
from app.schemas.user import UserCreate
from app.services.user import audit_service
from app.utils import generate_new_account_email, send_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["groups"])

_GROUP_RESOURCES_CACHE_TTL_SECONDS = 8.0
_group_resources_cache_lock = threading.Lock()


class _GroupResourcesCache:
    """群組 VM 查詢用的短 TTL Proxmox 資源快取。"""

    def __init__(self) -> None:
        self.data: list[dict] | None = None
        self.monotonic = 0.0


_group_resources_cache = _GroupResourcesCache()


def _check_group_access(current_user, db_group) -> None:
    """確認使用者是群組擁有者或 admin，否則拋出例外"""
    require_group_access(current_user, db_group.owner_id)


def _safe_usage_pct(used: object, total: object) -> float | None:
    """將 used/total 轉成 0~100 的百分比，無法計算時回傳 None。"""
    try:
        used_value = float(used)
        total_value = float(total)
    except (TypeError, ValueError):
        return None

    if total_value <= 0:
        return None

    pct = max(0.0, min((used_value / total_value) * 100, 100.0))
    return round(pct, 1)


def _get_cached_group_resources() -> list[dict]:
    """回傳群組 VM 查詢所需的 Proxmox 資源列表（短 TTL 快取）。"""
    now = time.monotonic()
    with _group_resources_cache_lock:
        if (
            _group_resources_cache.data is not None
            and now - _group_resources_cache.monotonic
            < _GROUP_RESOURCES_CACHE_TTL_SECONDS
        ):
            return list(_group_resources_cache.data)

    resources = proxmox_ops.list_all_resources()

    with _group_resources_cache_lock:
        _group_resources_cache.data = list(resources)
        _group_resources_cache.monotonic = time.monotonic()
        return list(_group_resources_cache.data)


@router.post("/", response_model=GroupPublic)
def create_group(
    group_in: GroupCreate,
    session: SessionDep,
    current_user: InstructorUser,
):
    db_group = group_repo.create_group(
        session=session,
        name=group_in.name,
        description=group_in.description,
        owner_id=current_user.id,
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="group_create",
        details=f"Created group '{db_group.name}' (id={db_group.id})",
    )
    member_count = group_repo.count_members(session=session, group_id=db_group.id)
    return GroupPublic(
        id=db_group.id,
        name=db_group.name,
        description=db_group.description,
        owner_id=db_group.owner_id,
        created_at=db_group.created_at,
        member_count=member_count,
    )


@router.get("/", response_model=GroupsPublic)
def list_groups(session: SessionDep, current_user: InstructorUser):
    if can_bypass_group_ownership(current_user):
        groups = group_repo.get_all_groups(session=session)
    else:
        groups = group_repo.get_groups_by_owner(
            session=session, owner_id=current_user.id
        )
    member_counts = group_repo.get_member_counts(
        session=session, group_ids=[g.id for g in groups]
    )
    data = [
        GroupPublic(
            id=g.id,
            name=g.name,
            description=g.description,
            owner_id=g.owner_id,
            created_at=g.created_at,
            member_count=member_counts.get(g.id, 0),
        )
        for g in groups
    ]
    return GroupsPublic(data=data, count=len(data))


@router.get("/{group_id}", response_model=GroupDetailPublic)
def get_group(
    group_id: uuid.UUID, session: SessionDep, current_user: InstructorUser
):
    db_group = group_repo.get_group_by_id(session=session, group_id=group_id)
    if not db_group:
        raise HTTPException(status_code=404, detail="Group not found")
    _check_group_access(current_user, db_group)

    member_rows = group_repo.get_member_rows(session=session, group_id=group_id)
    members_map = {m.user_id: m for m in member_rows}
    users = group_repo.get_group_members(session=session, group_id=group_id)

    # 取得每個成員最新的 vmid
    member_vmids = group_repo.get_member_vmids(session=session, group_id=group_id)

    # 批量取得所有 VM 的即時狀態與類型（一次 Proxmox API 呼叫）
    vm_status_map: dict[int, str] = {}
    vm_type_map: dict[int, str] = {}
    vm_cpu_usage_map: dict[int, float | None] = {}
    vm_ram_usage_map: dict[int, float | None] = {}
    vm_disk_usage_map: dict[int, float | None] = {}
    vmids_to_check = [v for v in member_vmids.values() if v is not None]
    if vmids_to_check:
        try:
            vmid_set = set(vmids_to_check)
            all_resources = _get_cached_group_resources()
            for r in all_resources:
                raw_vmid = r.get("vmid")
                try:
                    vmid = int(raw_vmid)
                except (TypeError, ValueError):
                    continue

                if vmid not in vmid_set:
                    continue

                vm_status_map[vmid] = r.get("status", "unknown")
                vm_type_map[vmid] = r.get("type", "unknown")
                vm_cpu_usage_map[vmid] = _safe_usage_pct(r.get("cpu"), 1)
                vm_ram_usage_map[vmid] = _safe_usage_pct(
                    r.get("mem"), r.get("maxmem")
                )
                vm_disk_usage_map[vmid] = _safe_usage_pct(
                    r.get("disk"), r.get("maxdisk")
                )
        except Exception:
            logger.warning("無法取得 Proxmox 資源狀態，將略過 VM 狀態欄位")

    members_public = [
        GroupMemberPublic(
            user_id=u.id,
            email=u.email,
            full_name=u.full_name,
            added_at=members_map[u.id].added_at if u.id in members_map else None,
            vmid=member_vmids.get(u.id),
            vm_status=vm_status_map.get(member_vmids[u.id])
            if u.id in member_vmids
            else None,
            vm_type=vm_type_map.get(member_vmids[u.id])
            if u.id in member_vmids
            else None,
            vm_cpu_usage_pct=vm_cpu_usage_map.get(member_vmids[u.id])
            if u.id in member_vmids
            else None,
            vm_ram_usage_pct=vm_ram_usage_map.get(member_vmids[u.id])
            if u.id in member_vmids
            else None,
            vm_disk_usage_pct=vm_disk_usage_map.get(member_vmids[u.id])
            if u.id in member_vmids
            else None,
        )
        for u in users
    ]

    return GroupDetailPublic(
        id=db_group.id,
        name=db_group.name,
        description=db_group.description,
        owner_id=db_group.owner_id,
        created_at=db_group.created_at,
        members=members_public,
    )


@router.delete("/{group_id}", response_model=Message)
def delete_group(
    group_id: uuid.UUID, session: SessionDep, current_user: InstructorUser
):
    db_group = group_repo.get_group_by_id(session=session, group_id=group_id)
    if not db_group:
        raise HTTPException(status_code=404, detail="Group not found")
    _check_group_access(current_user, db_group)

    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="group_delete",
        details=f"Deleted group '{db_group.name}' (id={group_id})",
    )
    group_repo.delete_group(session=session, group_id=group_id)
    return Message(message="Group deleted")


@router.post("/{group_id}/members", response_model=Message)
def add_members(
    group_id: uuid.UUID,
    body: GroupMemberAdd,
    session: SessionDep,
    current_user: InstructorUser,
):
    db_group = group_repo.get_group_by_id(session=session, group_id=group_id)
    if not db_group:
        raise HTTPException(status_code=404, detail="Group not found")
    _check_group_access(current_user, db_group)

    added, not_found = group_repo.add_members_by_emails(
        session=session, group_id=group_id, emails=body.emails
    )

    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="group_member_add",
        details=(
            f"Added {len(added)} member(s) to group '{db_group.name}'"
            + (f"; not found: {not_found}" if not_found else "")
        ),
    )

    msg = f"Added {len(added)} member(s)"
    if not_found:
        msg += f". Not found: {', '.join(not_found)}"
    return Message(message=msg)


@router.delete("/{group_id}/members/{user_id}", response_model=Message)
def remove_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
):
    db_group = group_repo.get_group_by_id(session=session, group_id=group_id)
    if not db_group:
        raise HTTPException(status_code=404, detail="Group not found")
    _check_group_access(current_user, db_group)

    removed = group_repo.remove_member(
        session=session, group_id=group_id, user_id=user_id
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found in group")

    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="group_member_remove",
        details=f"Removed user {user_id} from group '{db_group.name}'",
    )
    return Message(message="Member removed")


@router.post("/{group_id}/import-csv", response_model=CsvImportResult)
async def import_members_from_csv(
    group_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    file: UploadFile = File(...),
):
    """從 CSV 大量匯入學生帳號並加入群組。

    CSV 格式（支援 Big5/UTF-8）：學號, 姓名, 班級
    帳號不存在時自動建立，email 為 {學號}@ntub.edu.tw，並發送通知信。
    """
    db_group = group_repo.get_group_by_id(session=session, group_id=group_id)
    if not db_group:
        raise HTTPException(status_code=404, detail="Group not found")
    _check_group_access(current_user, db_group)

    raw = await file.read()
    content: str | None = None
    for encoding in ("cp950", "utf-8-sig", "utf-8"):
        try:
            content = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if content is None:
        raise HTTPException(status_code=400, detail="無法解析 CSV 檔案編碼")

    reader = csv.reader(io.StringIO(content))
    next(reader, None)  # 略過標題列

    result = CsvImportResult()
    emails_to_add: list[str] = []

    for row in reader:
        if len(row) < 2:
            continue
        student_id = row[0].strip()
        full_name = row[1].strip()
        if not student_id:
            continue
        email = f"{student_id}@ntub.edu.tw"

        existing = get_user_by_email(session=session, email=email)
        if existing:
            result.already_existed.append(email)
        else:
            password = secrets.token_urlsafe(12)
            user_in = UserCreate(
                email=email,
                password=password,
                full_name=full_name,
                is_active=True,
            )
            try:
                create_user_in_db(session=session, user_create=user_in)
            except Exception as exc:
                result.errors.append(f"{email}: 建立帳號失敗 {exc}")
                continue
            result.created.append(email)
            if settings.emails_enabled:
                try:
                    email_data = generate_new_account_email(
                        email_to=email, username=email, password=password
                    )
                    send_email(
                        email_to=email,
                        subject=email_data.subject,
                        html_content=email_data.html_content,
                    )
                except Exception as exc:
                    logger.warning("寄信失敗 %s: %s", email, exc)

        emails_to_add.append(email)

    # Commit user creations now so accounts exist in DB even if group-add fails.
    session.commit()

    added, _ = group_repo.add_members_by_emails(
        session=session, group_id=group_id, emails=emails_to_add
    )
    result.added_to_group = len(added)

    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="group_member_add",
        details=(
            f"CSV import to group '{db_group.name}': "
            f"created={len(result.created)}, existed={len(result.already_existed)}, "
            f"added={result.added_to_group}, errors={len(result.errors)}"
        ),
    )
    return result
