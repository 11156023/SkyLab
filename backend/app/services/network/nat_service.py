"""NAT 端口轉發服務 — 透過 Gateway 主機上的 nginx stream 模組管理 TCP/UDP 轉發規則。

設計原則：
- DB 為 source of truth，儲存所有 external_port → vm_ip:internal_port 映射
- 每次新增 / 刪除後，從 DB 完整重建 ``/etc/nginx/skylab/stream.conf``、驗證並 reload
- 該檔案由 SkyLab 完整持有（nginx.conf 以 include 載入），不與手動設定混放
- Gateway 尚未設定時同步會失敗，呼叫端負責回滾 DB（見 apply_nat_rule）
"""

import logging

from app.core.i18n import t
from app.exceptions import BadRequestError, ProxmoxError
from app.services.network.publish_target_policy import assert_publishable_vm_ip

logger = logging.getLogger(__name__)

# PVE 保留 port（禁止被分配為外網入口）
RESERVED_PORTS: frozenset[int] = frozenset(
    [
        22,    # SSH
        80,    # HTTP
        443,   # HTTPS
        3128,  # Spice Proxy
        4007,  # PVE cluster
        4008,  # PVE cluster
        5900, 5901, 5902, 5903, 5904, 5905,  # VNC
        6789,  # Ceph MON
        6800, 6801, 6802, 6803,              # Ceph OSD
        8006,  # PVE Web UI
        8007,  # PVE SPICE proxy
        111,   # rpcbind
    ]
)

# ─── 檢查 port 可用性 ──────────────────────────────────────────────────────────


def check_port_available(external_port: int, protocol: str, session: object) -> None:
    """檢查外網 port 是否可用（保留 port 檢查 + DB 衝突檢查）"""
    if external_port in RESERVED_PORTS:
        raise BadRequestError(
            t("nat.reservedPort", port=external_port)
        )
    from app.repositories import nat_rule as nat_repo

    if nat_repo.is_external_port_taken(session, external_port, protocol):  # type: ignore[arg-type]
        raise BadRequestError(
            t("nat.externalPortTaken", port=external_port, protocol=protocol)
        )


def allocate_external_port(
    session: object, protocol: str, *, exclude: frozenset[int] = frozenset()
) -> int:
    """從管理員設定的配號池挑一個沒用過的對外 port。

    給「一份規格、逐位學生實體化」的課程發布用：模板上不能寫死對外 port，
    只能在開課時配。這裡只挑號、不寫入；真正的佔用由 apply_nat_rule 的
    唯一約束把關，兩個班同時開課撞號時呼叫端重挑一次即可。``exclude`` 是
    同一輪已經挑出去、還沒寫進 DB 的 port。
    """
    from app.repositories import nat_rule as nat_repo
    from app.services.network import ip_management_service

    pool = ip_management_service.get_forward_port_range(
        ip_management_service.get_subnet_config(session)  # type: ignore[arg-type]
    )
    if pool is None:
        raise BadRequestError(t("nat.poolNotConfigured"))
    start, end = pool
    taken = nat_repo.taken_external_ports(session, protocol, start, end)  # type: ignore[arg-type]
    for candidate in range(start, end + 1):
        if candidate in RESERVED_PORTS or candidate in taken or candidate in exclude:
            continue
        return candidate
    raise BadRequestError(t("nat.poolExhausted", start=start, end=end))


# ─── nginx 同步（核心） ───────────────────────────────────────────────────────


def _sync_nginx_stream(session: object, rules: list | None = None) -> None:
    """從 DB 重建 nginx 的 stream.conf、驗證並 reload。
    Gateway 未設定時拋 ProxmoxError。

    ``rules`` 給刪除流程用：先拿「排除待刪規則後的清單」同步上去，
    同步成功才把 DB 的規則刪掉，避免 DB 刪了、Gateway 上還在轉發。
    """
    from app.infrastructure.ssh import create_key_client
    from app.repositories import gateway_config as gw_repo
    from app.repositories import nat_rule as nat_repo
    from app.repositories.gateway_config import (
        get_decrypted_private_key,
    )
    from app.services.network import nginx_gateway_service as nginx

    config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
    if config is None or not config.host or not config.encrypted_private_key:
        raise ProxmoxError(t("nat.gatewayNotConfiguredSyncFailed"))

    if rules is None:
        rules = nat_repo.list_rules(session)  # type: ignore[arg-type]
    private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]

    client = create_key_client(
        config.host,
        config.ssh_port,
        config.ssh_user,
        private_key_pem,
    )
    try:
        nginx.write_validated_config(
            client, nginx.NGINX_STREAM_CONF_PATH, nginx.build_stream_config(rules)
        )
        logger.info(f"[NAT] nginx 已同步 {len(rules)} 條轉發規則並 reload")
    except ProxmoxError:
        raise
    except Exception as e:
        raise ProxmoxError(t("nat.nginxSyncFailed", error=e))
    finally:
        client.close()


# ─── 公開操作 ──────────────────────────────────────────────────────────────────


def apply_nat_rule(
    session: object,
    vmid: int,
    vm_ip: str,
    external_port: int,
    internal_port: int,
    protocol: str,
) -> None:
    """建立 NAT 規則：寫入 DB + 同步 nginx。"""
    from app.models.nat_rule import NatRule
    from app.repositories import nat_rule as nat_repo

    check_port_available(external_port, protocol, session)
    # vm_ip 來自 guest agent 回報，VM 擁有者可偽造：不可讓外網 port 轉到
    # Gateway / PVE 節點等內部主機
    assert_publishable_vm_ip(session, vm_ip, vmid=vmid)
    get = getattr(session, "get", None)
    resource_vmid = None
    if get is not None:
        from app.models import Resource

        resource_vmid = vmid if get(Resource, vmid) is not None else None

    rule = NatRule(
        ssh_host="",  # 已改為 Gateway VM 架構，此欄位保留但不再使用
        vmid=vmid,
        resource_vmid=resource_vmid,
        vm_ip=vm_ip,
        external_port=external_port,
        internal_port=internal_port,
        protocol=protocol,
    )
    created = nat_repo.create_rule(session, rule)  # type: ignore[arg-type]
    try:
        _sync_nginx_stream(session)
    except Exception:
        # 同步失敗時補償刪除剛建立的規則：否則規則留在 DB（實際未生效）
        # 會永久佔住該外網 port，使用者重試會收到「Port 已被佔用」。
        try:
            nat_repo.delete_rule(session, created)  # type: ignore[arg-type]
        except Exception:
            logger.exception(
                "[NAT] 規則 %s 同步失敗後的回滾刪除也失敗，DB 可能殘留無效規則",
                created.id,
            )
        raise


def _sync_then_delete(session: object, doomed: list) -> None:
    """先把「排除這些規則後的清單」同步到 nginx，成功才刪 DB。

    反過來做（先刪 DB 再同步）的話，同步失敗就會留下「DB 查不到、Gateway 仍在
    轉發」的孤兒 port：既撤不掉，那個對外 port 也會被重新配給別人。
    """
    from app.repositories import nat_rule as nat_repo

    if not doomed:
        return
    doomed_ids = {r.id for r in doomed}
    remaining = [
        r
        for r in nat_repo.list_rules(session)  # type: ignore[arg-type]
        if r.id not in doomed_ids
    ]
    _sync_nginx_stream(session, remaining)
    nat_repo.delete_rules(session, doomed)  # type: ignore[arg-type]


def sync_to_gateway(session: object) -> None:
    """依 DB 把全部轉發規則重建到 Gateway 的 stream.conf。

    給管理員「重新同步」用：Gateway 重灌或剛從 haproxy 換成 nginx 時，
    stream.conf 是空的，平常只有規則異動才會重建，不手動同步就一直沒有轉發。
    """
    _sync_nginx_stream(session)


def remove_nat_rules_for_vmid(session: object, vmid: int) -> None:
    """刪除指定 VM 的所有 NAT 規則（VM 刪除時使用）。"""
    from app.repositories import nat_rule as nat_repo

    _sync_then_delete(
        session,
        nat_repo.list_rules_by_vmid(session, vmid),  # type: ignore[arg-type]
    )


def remove_nat_rules_by_internal_port(
    session: object, vmid: int, internal_port: int, protocol: str
) -> None:
    """刪除指定 VM 特定內部 port 的 NAT 規則（刪除連線 edge 時使用）。"""
    from app.repositories import nat_rule as nat_repo

    _sync_then_delete(
        session,
        nat_repo.list_rules_by_vmid_and_port(  # type: ignore[arg-type]
            session, vmid, internal_port, protocol
        ),
    )

