"""反向代理規則資料庫操作"""

import uuid

from sqlmodel import Session, col, select

from app.models.reverse_proxy_rule import ReverseProxyRule


def list_rules(session: Session) -> list[ReverseProxyRule]:
    return list(session.exec(select(ReverseProxyRule)).all())


def list_rules_by_vmid(session: Session, vmid: int) -> list[ReverseProxyRule]:
    return list(
        session.exec(select(ReverseProxyRule).where(ReverseProxyRule.vmid == vmid)).all()
    )


def list_rules_by_vmids(
    session: Session, vmids: list[int]
) -> list[ReverseProxyRule]:
    """一次撈多台機器的反向代理規則（清單頁用，避免逐台查詢）。"""
    if not vmids:
        return []
    return list(
        session.exec(
            select(ReverseProxyRule).where(col(ReverseProxyRule.vmid).in_(vmids))
        ).all()
    )


def get_rule(session: Session, rule_id: uuid.UUID) -> ReverseProxyRule | None:
    return session.get(ReverseProxyRule, rule_id)


def is_domain_taken(
    session: Session,
    domain: str,
    exclude_rule_id: uuid.UUID | None = None,
) -> bool:
    statement = select(ReverseProxyRule).where(ReverseProxyRule.domain == domain)
    if exclude_rule_id is not None:
        statement = statement.where(ReverseProxyRule.id != exclude_rule_id)
    existing = session.exec(statement).first()
    return existing is not None


def create_rule(session: Session, rule: ReverseProxyRule) -> ReverseProxyRule:
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def update_rule(session: Session, rule: ReverseProxyRule) -> ReverseProxyRule:
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def delete_rule(session: Session, rule: ReverseProxyRule) -> None:
    session.delete(rule)
    session.commit()


def delete_rules_by_vmid(session: Session, vmid: int) -> list[ReverseProxyRule]:
    rules = list_rules_by_vmid(session, vmid)
    for r in rules:
        session.delete(r)
    session.commit()
    return rules


def delete_rules_by_vmid_and_port(
    session: Session, vmid: int, internal_port: int
) -> list[ReverseProxyRule]:
    """刪除指定 VM 特定內部 port 的反向代理規則"""
    rules = list(
        session.exec(
            select(ReverseProxyRule).where(
                ReverseProxyRule.vmid == vmid,
                ReverseProxyRule.internal_port == internal_port,
            )
        ).all()
    )
    for r in rules:
        session.delete(r)
    session.commit()
    return rules
