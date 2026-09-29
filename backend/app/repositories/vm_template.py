"""VMTemplate CRUD 與可見範圍查詢。"""

import uuid
from datetime import datetime, timezone

from sqlmodel import Session, col, or_, select

from app.models import (
    VMTemplate,
    VMTemplateStatus,
    VMTemplateVisibility,
)


def create_template(
    *,
    session: Session,
    pve_vmid: int,
    name: str,
    owner_id: uuid.UUID,
    node: str,
    resource_type: str,
    description: str | None = None,
    storage: str | None = None,
    visibility: VMTemplateVisibility = VMTemplateVisibility.private,
    default_cores: int | None = None,
    default_memory: int | None = None,
    allow_password_change: bool = True,
    requires_gpu: bool = False,
    source_vmid: int | None = None,
    commit: bool = True,
) -> VMTemplate:
    template = VMTemplate(
        pve_vmid=pve_vmid,
        name=name,
        description=description,
        owner_id=owner_id,
        node=node,
        storage=storage,
        resource_type=resource_type,
        visibility=visibility,
        default_cores=default_cores,
        default_memory=default_memory,
        allow_password_change=allow_password_change,
        requires_gpu=requires_gpu,
        source_vmid=source_vmid,
    )
    session.add(template)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(template)
    return template


def get_template(
    *, session: Session, template_id: uuid.UUID
) -> VMTemplate | None:
    return session.get(VMTemplate, template_id)


def get_template_by_pve_vmid(
    *, session: Session, pve_vmid: int, include_deleted: bool = False
) -> VMTemplate | None:
    stmt = select(VMTemplate).where(VMTemplate.pve_vmid == pve_vmid)
    if not include_deleted:
        stmt = stmt.where(VMTemplate.status != VMTemplateStatus.deleted)
    return session.exec(stmt).first()


def get_updating_template_by_source_vmid(
    *, session: Session, source_vmid: int
) -> VMTemplate | None:
    """找出目前以 source_vmid 當更新循環暫存母機的範本（status == updating）。"""
    return session.exec(
        select(VMTemplate).where(
            VMTemplate.status == VMTemplateStatus.updating,
            VMTemplate.source_vmid == source_vmid,
        )
    ).first()


def list_all_templates(
    *, session: Session, include_deleted: bool = False
) -> list[VMTemplate]:
    stmt = select(VMTemplate)
    if not include_deleted:
        stmt = stmt.where(VMTemplate.status != VMTemplateStatus.deleted)
    stmt = stmt.order_by(col(VMTemplate.created_at).desc())
    return list(session.exec(stmt).all())


def list_visible_templates(
    *,
    session: Session,
    user_id: uuid.UUID,
    only_ready: bool = False,
) -> list[VMTemplate]:
    """非 admin 只看自己擁有的範本，或所有人可見的範本。"""
    stmt = (
        select(VMTemplate)
        .where(VMTemplate.status != VMTemplateStatus.deleted)
        .where(
            or_(
                VMTemplate.owner_id == user_id,
                VMTemplate.visibility == VMTemplateVisibility.global_,
            )
        )
    )
    if only_ready:
        stmt = stmt.where(VMTemplate.status == VMTemplateStatus.ready)
    stmt = stmt.order_by(col(VMTemplate.created_at).desc())
    return list(session.exec(stmt).all())

def list_student_catalog(*, session: Session) -> list[VMTemplate]:
    """Templates a student may pick in the ordinary request form.

    學生能不能用只看可見範圍：「全部可見」且 ready 的範本才進目錄。
    """
    stmt = (
        select(VMTemplate)
        .where(
            VMTemplate.status == VMTemplateStatus.ready,
            VMTemplate.visibility == VMTemplateVisibility.global_,
        )
        .order_by(col(VMTemplate.name))
    )
    return list(session.exec(stmt).all())


def registered_pve_vmids(*, session: Session) -> set[int]:
    """PVE VMIDs already registered as platform templates (any live status)."""
    stmt = select(VMTemplate.pve_vmid).where(
        VMTemplate.status != VMTemplateStatus.deleted
    )
    return {int(vmid) for vmid in session.exec(stmt).all()}


def touch(*, session: Session, template: VMTemplate, commit: bool = True) -> None:
    template.updated_at = datetime.now(timezone.utc)
    session.add(template)
    if commit:
        session.commit()
        session.refresh(template)

def is_template_visible_to_user(
    *, template: VMTemplate, user_id: uuid.UUID
) -> bool:
    return (
        template.owner_id == user_id
        or template.visibility == VMTemplateVisibility.global_
    )
