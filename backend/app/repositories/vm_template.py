"""VMTemplate CRUD 與可見範圍查詢。"""

import uuid
from datetime import datetime, timezone

from sqlmodel import Session, col, or_, select

from app.models import (
    Group,
    GroupMember,
    VMTemplate,
    VMTemplateGroupLink,
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
    visibility: VMTemplateVisibility = VMTemplateVisibility.groups,
    default_cores: int | None = None,
    default_memory: int | None = None,
    default_disk: int | None = None,
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
        default_disk=default_disk,
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
    *, session: Session, pve_vmid: int
) -> VMTemplate | None:
    return session.exec(
        select(VMTemplate).where(VMTemplate.pve_vmid == pve_vmid)
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
    """非 admin 的可見範圍：自己擁有的、全域的、或所屬/擁有群組綁定的範本。"""
    member_group_ids = select(GroupMember.group_id).where(
        GroupMember.user_id == user_id
    )
    owned_group_ids = select(Group.id).where(Group.owner_id == user_id)
    linked_template_ids = select(VMTemplateGroupLink.template_id).where(
        or_(
            col(VMTemplateGroupLink.group_id).in_(member_group_ids),
            col(VMTemplateGroupLink.group_id).in_(owned_group_ids),
        )
    )
    stmt = (
        select(VMTemplate)
        .where(VMTemplate.status != VMTemplateStatus.deleted)
        .where(
            or_(
                VMTemplate.owner_id == user_id,
                VMTemplate.visibility == VMTemplateVisibility.global_,
                col(VMTemplate.id).in_(linked_template_ids),
            )
        )
    )
    if only_ready:
        stmt = stmt.where(VMTemplate.status == VMTemplateStatus.ready)
    stmt = stmt.order_by(col(VMTemplate.created_at).desc())
    return list(session.exec(stmt).all())


def get_group_ids(
    *, session: Session, template_id: uuid.UUID
) -> list[uuid.UUID]:
    stmt = select(VMTemplateGroupLink.group_id).where(
        VMTemplateGroupLink.template_id == template_id
    )
    return list(session.exec(stmt).all())


def set_group_links(
    *,
    session: Session,
    template_id: uuid.UUID,
    group_ids: list[uuid.UUID],
    commit: bool = True,
) -> None:
    existing = session.exec(
        select(VMTemplateGroupLink).where(
            VMTemplateGroupLink.template_id == template_id
        )
    ).all()
    wanted = set(group_ids)
    for link in existing:
        if link.group_id not in wanted:
            session.delete(link)
        else:
            wanted.discard(link.group_id)
    for group_id in wanted:
        session.add(
            VMTemplateGroupLink(template_id=template_id, group_id=group_id)
        )
    if commit:
        session.commit()


def touch(*, session: Session, template: VMTemplate, commit: bool = True) -> None:
    template.updated_at = datetime.now(timezone.utc)
    session.add(template)
    if commit:
        session.commit()
        session.refresh(template)


def get_groups_by_ids(
    *, session: Session, group_ids: list[uuid.UUID]
) -> list[Group]:
    if not group_ids:
        return []
    stmt = select(Group).where(col(Group.id).in_(group_ids))
    return list(session.exec(stmt).all())


def is_template_visible_to_user(
    *, session: Session, template: VMTemplate, user_id: uuid.UUID
) -> bool:
    if template.owner_id == user_id:
        return True
    if template.visibility == VMTemplateVisibility.global_:
        return True
    linked_groups = get_group_ids(session=session, template_id=template.id)
    if not linked_groups:
        return False
    member_stmt = select(GroupMember.group_id).where(
        GroupMember.user_id == user_id,
        col(GroupMember.group_id).in_(linked_groups),
    )
    if session.exec(member_stmt).first() is not None:
        return True
    owner_stmt = select(Group.id).where(
        Group.owner_id == user_id,
        col(Group.id).in_(linked_groups),
    )
    return session.exec(owner_stmt).first() is not None
