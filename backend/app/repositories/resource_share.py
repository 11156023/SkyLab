"""Resource share repository."""

import uuid

from sqlmodel import Session, select

from app.models.resource_share import ResourceShare


def list_shares_for_resource(*, session: Session, vmid: int) -> list[ResourceShare]:
    statement = (
        select(ResourceShare)
        .where(ResourceShare.resource_vmid == vmid)
        .order_by(ResourceShare.created_at)
    )
    return list(session.exec(statement).all())


def list_shares_for_user(
    *, session: Session, user_id: uuid.UUID
) -> list[ResourceShare]:
    statement = select(ResourceShare).where(ResourceShare.user_id == user_id)
    return list(session.exec(statement).all())


def get_share(
    *, session: Session, vmid: int, user_id: uuid.UUID
) -> ResourceShare | None:
    statement = select(ResourceShare).where(
        ResourceShare.resource_vmid == vmid, ResourceShare.user_id == user_id
    )
    return session.exec(statement).first()


def get_share_by_id(*, session: Session, share_id: uuid.UUID) -> ResourceShare | None:
    return session.get(ResourceShare, share_id)


def create_share(
    *,
    session: Session,
    vmid: int,
    user_id: uuid.UUID,
    granted_by: uuid.UUID | None,
    permission: str,
    commit: bool = True,
) -> ResourceShare:
    share = ResourceShare(
        resource_vmid=vmid,
        user_id=user_id,
        granted_by=granted_by,
        permission=permission,
    )
    session.add(share)
    if commit:
        session.commit()
        session.refresh(share)
    else:
        session.flush()
    return share


def delete_share(
    *, session: Session, share: ResourceShare, commit: bool = True
) -> None:
    session.delete(share)
    if commit:
        session.commit()
    else:
        session.flush()


def delete_shares_for_resource(
    *, session: Session, vmid: int, commit: bool = True
) -> int:
    shares = list_shares_for_resource(session=session, vmid=vmid)
    for share in shares:
        session.delete(share)
    if commit:
        session.commit()
    else:
        session.flush()
    return len(shares)


__all__ = [
    "create_share",
    "delete_share",
    "delete_shares_for_resource",
    "get_share",
    "get_share_by_id",
    "list_shares_for_resource",
    "list_shares_for_user",
]
