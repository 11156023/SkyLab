"""配置文件分發（E2）：逐 VM fan-out 寫入，任務狀態存 in-memory。

- 權限：逐 vmid 過 ``require_vm_teaching_access``（老師僅能選自己群組成員的 VM）。
- 併發：``asyncio.Semaphore``，上限沿用 ``GovernanceConfig.provision_max_concurrency``。
- 單台失敗不中斷整批；逐台記錄 ok / error + 原因。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session

from app.core.permissions import is_admin
from app.exceptions import (
    AppError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
)
from app.infrastructure.proxmox import guest
from app.infrastructure.worker import ExpiringStore, background_tasks
from app.services.proxmox import proxmox_service
from app.services.teaching.access import require_vm_teaching_access

logger = logging.getLogger(__name__)

TASK_TTL = timedelta(hours=2)


@dataclass
class PushItemResult:
    vmid: int
    status: str = "pending"  # pending | running | ok | error
    error: str | None = None


@dataclass
class PushTask:
    id: str
    requested_by: uuid.UUID
    file_name: str
    target_path: str
    created_at: datetime
    items: dict[int, PushItemResult] = field(default_factory=dict)
    targets: list[dict[str, Any]] = field(default_factory=list)


_tasks: ExpiringStore[PushTask] = ExpiringStore(
    ttl=TASK_TTL,
    is_expired=lambda task, now, ttl: now - task.created_at > ttl,
    now_factory=lambda: datetime.now(timezone.utc),
)


def _max_concurrency() -> int:
    from app.core.db import engine  # noqa: PLC0415 — 測試環境不一定有 DB
    from app.repositories import governance as governance_repo  # noqa: PLC0415

    with Session(engine) as session:
        return int(
            governance_repo.get_governance_config(
                session=session
            ).provision_max_concurrency
        )


def _resolve_targets(
    session: Session, vmids: list[int], user: Any
) -> list[dict[str, Any]]:
    """權限檢查 + PVE node/type 解析；任一台不合法整批 4xx。"""
    targets: list[dict[str, Any]] = []
    for vmid in dict.fromkeys(vmids):  # 去重保序
        require_vm_teaching_access(session, user, vmid)
        info = proxmox_service.find_resource(vmid)
        targets.append(
            {
                "vmid": int(vmid),
                "node": str(info["node"]),
                "type": "lxc" if str(info.get("type") or "") == "lxc" else "qemu",
            }
        )
    return targets


def _new_task(
    *,
    requested_by: uuid.UUID,
    file_name: str,
    target_path: str,
    targets: list[dict[str, Any]],
) -> PushTask:
    task = PushTask(
        id=uuid.uuid4().hex,
        requested_by=requested_by,
        file_name=file_name,
        target_path=target_path,
        created_at=datetime.now(timezone.utc),
        targets=targets,
        items={t["vmid"]: PushItemResult(vmid=t["vmid"]) for t in targets},
    )
    _tasks.upsert(task.id, task)
    return task


def _write_one(target: dict[str, Any], target_path: str, content: bytes) -> None:
    if target["type"] == "lxc":
        guest.write_file_lxc(target["node"], target["vmid"], target_path, content)
    else:
        guest.write_file_qemu(target["node"], target["vmid"], target_path, content)


async def _run_push(
    task_id: str, content: bytes, target_path: str, *, concurrency: int
) -> None:
    task = _tasks.get(task_id)
    if task is None:
        return
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(target: dict[str, Any]) -> None:
        item = task.items[target["vmid"]]
        async with semaphore:
            item.status = "running"
            try:
                await asyncio.to_thread(_write_one, target, target_path, content)
                item.status = "ok"
            except Exception as exc:
                item.status = "error"
                item.error = str(exc)[:300]
                logger.warning(
                    "Config push failed for vmid=%s: %s", target["vmid"], exc
                )

    await asyncio.gather(*(_one(t) for t in task.targets))
    logger.info(
        "Config push %s done: %d ok / %d total",
        task_id,
        sum(1 for i in task.items.values() if i.status == "ok"),
        len(task.items),
    )


def start_push(
    session: Session,
    *,
    content: bytes,
    file_name: str,
    target_path: str,
    vmids: list[int],
    user: Any,
) -> str:
    if not vmids:
        raise BadRequestError("至少需要選擇一台 VM")
    if len(content) > guest.MAX_CONFIG_FILE_BYTES:
        raise AppError("檔案超過 1 MB 上限", 413)
    guest.validate_target_path(target_path)
    targets = _resolve_targets(session, vmids, user)
    task = _new_task(
        requested_by=user.id,
        file_name=file_name,
        target_path=target_path,
        targets=targets,
    )
    concurrency = _max_concurrency()
    background_tasks.submit_factory(
        lambda: _run_push(task.id, content, target_path, concurrency=concurrency),
        name="config-push",
        task_id=f"config-push-{task.id}",
    )
    return task.id


def get_push_status(task_id: str, user: Any) -> PushTask:
    task = _tasks.get(task_id)
    if task is None:
        raise NotFoundError("分發任務不存在或已過期")
    if task.requested_by != user.id and not is_admin(user):
        raise PermissionDeniedError("只能查看自己發起的分發任務")
    return task
