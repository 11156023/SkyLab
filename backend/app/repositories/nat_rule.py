"""NAT 規則資料庫操作"""

import uuid

from sqlmodel import Session, col, select

from app.models.nat_rule import NatRule


def list_rules(session: Session) -> list[NatRule]:
    """列出所有 NAT 規則"""
    return list(session.exec(select(NatRule)).all())


def list_rules_by_vmid(session: Session, vmid: int) -> list[NatRule]:
    """列出指定 VM 的 NAT 規則"""
    return list(session.exec(select(NatRule).where(NatRule.vmid == vmid)).all())


def list_rules_by_vmids(session: Session, vmids: list[int]) -> list[NatRule]:
    """一次列出多台 VM 的 NAT 規則（清單頁用，不逐台查）。"""
    if not vmids:
        return []
    return list(
        session.exec(select(NatRule).where(col(NatRule.vmid).in_(vmids))).all()
    )


def get_rule(session: Session, rule_id: uuid.UUID) -> NatRule | None:
    return session.get(NatRule, rule_id)


def is_external_port_taken(
    session: Session, external_port: int, protocol: str
) -> bool:
    """檢查外網 port 是否已被佔用"""
    existing = session.exec(
        select(NatRule).where(
            NatRule.external_port == external_port,
            NatRule.protocol == protocol,
        )
    ).first()
    return existing is not None


def taken_external_ports(
    session: Session, protocol: str, start: int, end: int
) -> set[int]:
    """配號池範圍內已被佔用的對外 port（配號用，一次查完不逐一問）。"""
    rows = session.exec(
        select(NatRule.external_port).where(
            NatRule.protocol == protocol,
            NatRule.external_port >= start,
            NatRule.external_port <= end,
        )
    ).all()
    return {int(port) for port in rows}


def create_rule(session: Session, rule: NatRule) -> NatRule:
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def delete_rule(session: Session, rule: NatRule) -> None:
    session.delete(rule)
    session.commit()


def delete_rules_by_vmid(session: Session, vmid: int) -> list[NatRule]:
    """刪除指定 VM 的所有 NAT 規則，回傳被刪除的規則列表"""
    rules = list_rules_by_vmid(session, vmid)
    for r in rules:
        session.delete(r)
    session.commit()
    return rules


def delete_rules_by_vmid_and_port(
    session: Session, vmid: int, internal_port: int, protocol: str
) -> list[NatRule]:
    """刪除指定 VM 特定內部 port 的 NAT 規則（用於刪除連線時）"""
    rules = list(
        session.exec(
            select(NatRule).where(
                NatRule.vmid == vmid,
                NatRule.internal_port == internal_port,
                NatRule.protocol == protocol,
            )
        ).all()
    )
    for r in rules:
        session.delete(r)
    session.commit()
    return rules


__all__ = [
    "list_rules",
    "list_rules_by_vmid",
    "list_rules_by_vmids",
    "get_rule",
    "is_external_port_taken",
    "taken_external_ports",
    "create_rule",
    "delete_rule",
    "delete_rules_by_vmid",
    "delete_rules_by_vmid_and_port",
]
