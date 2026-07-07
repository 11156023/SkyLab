"""批次動態資源調整（E6）：逐台過配額 → set config → 逐台結果。

LXC 的 cores/memory 更新即時生效；QEMU 更新 config 後若 VM 在跑，
標記 ``needs_restart``（重啟後生效）。單台失敗不中斷整批。
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
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.infrastructure.worker import ExpiringStore, background_tasks
from app.services.proxmox import proxmox_service
from app.services.teaching.access import require_vm_teaching_access

logger = logging.getLogger(__name__)

TASK_TTL = timedelta(hours=2)


@dataclass
class SpecItemResult:
    vmid: int
    status: str = "pending"  # pending|running|ok|needs_restart|quota_exceeded|error
    error: str | None = None


@dataclass
class SpecTask:
    id: str
    requested_by: uuid.UUID
    created_at: datetime
    items: dict[int, SpecItemResult] = field(default_factory=dict)
    targets: list[dict[str, Any]] = field(default_factory=list)


_tasks: ExpiringStore[SpecTask] = ExpiringStore(
    ttl=TASK_TTL,
    is_expired=lambda task, now, ttl: now - task.created_at > ttl,
    now_factory=lambda: datetime.now(timezone.utc),
)


def _max_concurrency() -> int:
    from app.core.db import engine  # noqa: PLC0415
    from app.repositories import governance as governance_repo  # noqa: PLC0415

    with Session(engine) as session:
        return int(
            governance_repo.get_governance_config(
                session=session
            ).provision_max_concurrency
        )


def _check_quota_for_owner(owner_id: uuid.UUID, **deltas: int) -> None:
    from app.core.db import engine  # noqa: PLC0415
    from app.services.resource import quota_service  # noqa: PLC0415

    with Session(engine) as session:
        quota_service.check_quota(session, owner_id, **deltas)


def _resolve_targets(
    session: Session,
    *,
    vmids: list[int] | None,
    group_id: uuid.UUID | None,
    user: Any,
) -> list[dict[str, Any]]:
    resolved_vmids: list[int]
    if vmids:
        resolved_vmids = list(dict.fromkeys(vmids))
    elif group_id is not None:
        from app.repositories import group as group_repo  # noqa: PLC0415

        # get_member_vmids 回傳 {user_id: vmid | None}（該成員最新一次
        # 成功批量建立的 vmid），非 list[int]。過濾掉沒有 vmid 的成員並去重。
        member_vmids = group_repo.get_member_vmids(session=session, group_id=group_id)
        resolved_vmids = list(
            dict.fromkeys(vmid for vmid in member_vmids.values() if vmid is not None)
        )
        if not resolved_vmids:
            raise BadRequestError("該群組成員沒有任何 VM")
    else:
        raise BadRequestError("必須提供 vmids 或 group_id")

    targets: list[dict[str, Any]] = []
    for vmid in resolved_vmids:
        resource = require_vm_teaching_access(session, user, vmid)
        info = proxmox_service.find_resource(vmid)
        targets.append(
            {
                "vmid": int(vmid),
                "node": str(info["node"]),
                "type": "lxc" if str(info.get("type") or "") == "lxc" else "qemu",
                "owner_id": resource.user_id,
            }
        )
    return targets


def _new_task(*, requested_by: uuid.UUID, targets: list[dict[str, Any]]) -> SpecTask:
    task = SpecTask(
        id=uuid.uuid4().hex,
        requested_by=requested_by,
        created_at=datetime.now(timezone.utc),
        targets=targets,
        items={t["vmid"]: SpecItemResult(vmid=t["vmid"]) for t in targets},
    )
    _tasks.upsert(task.id, task)
    return task


def _apply_one(
    target: dict[str, Any], *, cores: int | None, memory_mb: int | None
) -> tuple[str, str | None]:
    node, vmid, rtype = target["node"], target["vmid"], target["type"]
    current = proxmox_service.get_current_specs(node, vmid, rtype)
    delta_cores = (
        max(0, cores - int(current.get("cpu") or 0)) if cores is not None else 0
    )
    delta_memory = (
        max(0, memory_mb - int(current.get("memory") or 0))
        if memory_mb is not None
        else 0
    )
    try:
        _check_quota_for_owner(
            target["owner_id"],
            delta_cores=delta_cores,
            delta_memory_mb=delta_memory,
        )
    except ConflictError as exc:
        return "quota_exceeded", exc.message

    params: dict[str, int] = {}
    if cores is not None:
        params["cores"] = cores
    if memory_mb is not None:
        params["memory"] = memory_mb
    proxmox_service.update_config(node, vmid, rtype, **params)

    if rtype == "qemu":
        status = proxmox_service.get_status(node, vmid, rtype)
        if str(status.get("status") or "").lower() == "running":
            return "needs_restart", None
    return "ok", None


async def _run_batch(
    task_id: str, *, cores: int | None, memory_mb: int | None, concurrency: int
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
                status, error = await asyncio.to_thread(
                    _apply_one, target, cores=cores, memory_mb=memory_mb
                )
                item.status = status
                item.error = error
            except Exception as exc:
                item.status = "error"
                item.error = str(exc)[:300]
                logger.warning(
                    "Batch spec failed for vmid=%s: %s", target["vmid"], exc
                )

    await asyncio.gather(*(_one(t) for t in task.targets))


def start_batch_spec(
    session: Session,
    *,
    vmids: list[int] | None,
    group_id: uuid.UUID | None,
    cores: int | None,
    memory_mb: int | None,
    user: Any,
) -> str:
    if cores is None and memory_mb is None:
        raise BadRequestError("至少需要指定 cores 或 memory_mb 其中之一")
    targets = _resolve_targets(session, vmids=vmids, group_id=group_id, user=user)
    task = _new_task(requested_by=user.id, targets=targets)
    concurrency = _max_concurrency()
    background_tasks.submit_factory(
        lambda: _run_batch(
            task.id, cores=cores, memory_mb=memory_mb, concurrency=concurrency
        ),
        name="batch-spec",
        task_id=f"batch-spec-{task.id}",
    )
    return task.id


def get_batch_status(task_id: str, user: Any) -> SpecTask:
    task = _tasks.get(task_id)
    if task is None:
        raise NotFoundError("批次任務不存在或已過期")
    if task.requested_by != user.id and not is_admin(user):
        raise PermissionDeniedError("只能查看自己發起的批次任務")
    return task
