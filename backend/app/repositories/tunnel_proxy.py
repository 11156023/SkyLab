"""Data-access layer for tunnel proxy records."""

import uuid
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.models import Resource
from app.models.tunnel_proxy import TunnelProxy


def create_proxy(
    *,
    session: Session,
    vmid: int,
    user_id: uuid.UUID,
    service: str,
    internal_port: int,
    proxy_name: str,
    visitor_port: int,
    commit: bool = True,
) -> TunnelProxy:
    proxy = TunnelProxy(
        vmid=vmid,
        resource_vmid=vmid if session.get(Resource, vmid) is not None else None,
        user_id=user_id,
        service=service,
        internal_port=internal_port,
        proxy_name=proxy_name,
        visitor_port=visitor_port,
        created_at=datetime.now(timezone.utc),
    )
    session.add(proxy)
    if commit:
        session.commit()
        session.refresh(proxy)
    return proxy


def get_proxies_by_vmid(*, session: Session, vmid: int) -> list[TunnelProxy]:
    return list(session.exec(
        select(TunnelProxy).where(TunnelProxy.vmid == vmid)
    ).all())


def get_proxies_by_user(*, session: Session, user_id: uuid.UUID) -> list[TunnelProxy]:
    return list(session.exec(
        select(TunnelProxy)
        .where(TunnelProxy.user_id == user_id)
        .order_by(TunnelProxy.vmid, TunnelProxy.service)
    ).all())


def get_all_proxies(*, session: Session) -> list[TunnelProxy]:
    return list(session.exec(
        select(TunnelProxy).order_by(TunnelProxy.vmid, TunnelProxy.service)
    ).all())


def delete_proxies_by_vmid(*, session: Session, vmid: int, commit: bool = True) -> int:
    proxies = get_proxies_by_vmid(session=session, vmid=vmid)
    count = len(proxies)
    for p in proxies:
        session.delete(p)
    if commit:
        session.commit()
    return count


def proxy_name_exists(*, session: Session, proxy_name: str) -> bool:
    return session.exec(
        select(TunnelProxy).where(TunnelProxy.proxy_name == proxy_name)
    ).first() is not None
