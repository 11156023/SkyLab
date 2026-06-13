"""NAT 端口轉發服務 — 透過 Gateway VM 的 haproxy 管理 TCP/UDP 轉發規則。

設計原則：
- DB 為 source of truth，儲存所有 external_port → vm_ip:internal_port 映射
- 每次新增 / 刪除後，從 DB 完整重建 haproxy managed section 並 reload
- haproxy.cfg 以 BEGIN/END 標記區隔手動設定與自動管理部分
- 若 Gateway VM 尚未設定，只寫 DB、跳過 haproxy 同步（不中斷主流程）
"""

import logging

from app.exceptions import BadRequestError, ProxmoxError

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

# haproxy.cfg 自動管理區段標記（與 install.sh 保持一致）
_HAPROXY_BEGIN = "# BEGIN_skylab_MANAGED"
_HAPROXY_END = "# END_skylab_MANAGED"


# ─── 檢查 port 可用性 ──────────────────────────────────────────────────────────


def check_port_available(external_port: int, protocol: str, session: object) -> None:
    """檢查外網 port 是否可用（保留 port 檢查 + DB 衝突檢查）"""
    if external_port in RESERVED_PORTS:
        raise BadRequestError(
            f"Port {external_port} 為系統保留 port，不可用作外網入口"
        )
    from app.repositories import nat_rule as nat_repo  # noqa: PLC0415

    if nat_repo.is_external_port_taken(session, external_port, protocol):  # type: ignore[arg-type]
        raise BadRequestError(
            f"外網 Port {external_port}/{protocol} 已被其他 VM 佔用"
        )


# ─── haproxy config 產生 ───────────────────────────────────────────────────────


def _build_haproxy_managed_block(rules: list) -> str:
    """從 DB 規則列表產生 haproxy frontend/backend 設定文字"""
    if not rules:
        return ""
    lines: list[str] = []
    for r in rules:
        name = f"cc-{r.vmid}-{r.external_port}-{r.protocol}"
        lines += [
            f"frontend {name}",
            f"    bind *:{r.external_port}",
            "    mode tcp",
            f"    default_backend {name}-back",
            "",
            f"backend {name}-back",
            "    mode tcp",
            f"    server vm{r.vmid} {r.vm_ip}:{r.internal_port} check inter 10s",
            "",
        ]
    return "\n".join(lines)


# ─── haproxy 同步（核心） ──────────────────────────────────────────────────────


def _sync_haproxy(session: object) -> None:
    """從 DB 重建 haproxy managed section 並 reload。
    若 Gateway VM 未設定則靜默略過（不拋錯，讓主流程繼續）。
    """
    from app.infrastructure.ssh import create_key_client, exec_command  # noqa: PLC0415
    from app.repositories import gateway_config as gw_repo  # noqa: PLC0415
    from app.repositories import nat_rule as nat_repo  # noqa: PLC0415
    from app.repositories.gateway_config import (
        get_decrypted_private_key,  # noqa: PLC0415
    )
    from app.services.network.gateway_service import (
        SERVICE_CONFIG_PATHS,  # noqa: PLC0415
    )

    config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
    if config is None or not config.host or not config.encrypted_private_key:
        raise ProxmoxError("Gateway VM 尚未設定，無法同步 haproxy 規則")

    rules = nat_repo.list_rules(session)  # type: ignore[arg-type]
    private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]
    haproxy_path = SERVICE_CONFIG_PATHS["haproxy"]
    tmp_path = haproxy_path + ".SkyLab.tmp"

    client = create_key_client(
        config.host,
        config.ssh_port,
        config.ssh_user,
        private_key_pem,
    )
    try:
        # 讀取現有 haproxy.cfg
        sftp = client.open_sftp()
        try:
            try:
                with sftp.open(haproxy_path, "r") as f:
                    current_cfg = f.read().decode()
            except OSError:
                current_cfg = ""
        finally:
            sftp.close()

        # 重建 managed section
        new_block = _build_haproxy_managed_block(rules)
        begin_idx = current_cfg.find(_HAPROXY_BEGIN)
        end_idx = current_cfg.find(_HAPROXY_END)

        if begin_idx != -1 and end_idx != -1:
            new_cfg = (
                current_cfg[:begin_idx]
                + _HAPROXY_BEGIN + "\n"
                + new_block
                + _HAPROXY_END + "\n"
                + current_cfg[end_idx + len(_HAPROXY_END):].lstrip("\n")
            )
        else:
            # 標記不存在時附加在末尾
            new_cfg = (
                current_cfg.rstrip()
                + f"\n\n{_HAPROXY_BEGIN}\n{new_block}{_HAPROXY_END}\n"
            )

        # 原子性寫入 + 驗證 + reload
        sftp = client.open_sftp()
        try:
            with sftp.open(tmp_path, "w") as f:
                f.write(new_cfg.encode())
        finally:
            sftp.close()

        code, out, err = exec_command(
            client,
            f"haproxy -c -f {tmp_path} 2>&1 "
            f"&& mv {tmp_path} {haproxy_path} "
            f"&& systemctl reload haproxy 2>&1",
        )
        if code != 0:
            exec_command(client, f"rm -f {tmp_path}")
            raise ProxmoxError(f"haproxy 設定同步失敗：{out}{err}")

        logger.info(f"[NAT] haproxy 已同步 {len(rules)} 條轉發規則並 reload")

    except ProxmoxError:
        raise
    except Exception as e:
        raise ProxmoxError(f"haproxy 同步失敗：{e}")
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
    """建立 NAT 規則：寫入 DB + 同步 haproxy。"""
    from app.models.nat_rule import NatRule  # noqa: PLC0415
    from app.repositories import nat_rule as nat_repo  # noqa: PLC0415

    check_port_available(external_port, protocol, session)
    get = getattr(session, "get", None)
    resource_vmid = None
    if get is not None:
        from app.models import Resource  # noqa: PLC0415

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
        _sync_haproxy(session)
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


def remove_nat_rule_by_id(session: object, rule_id: str) -> None:
    """刪除指定 NAT 規則：從 DB 刪除後同步 haproxy。"""
    import uuid as _uuid  # noqa: PLC0415

    from app.repositories import nat_rule as nat_repo  # noqa: PLC0415

    rule = nat_repo.get_rule(session, _uuid.UUID(rule_id))  # type: ignore[arg-type]
    if rule is None:
        raise BadRequestError(f"NAT 規則 {rule_id} 不存在")

    nat_repo.delete_rule(session, rule)  # type: ignore[arg-type]
    _sync_haproxy(session)


def remove_nat_rules_for_vmid(session: object, vmid: int) -> None:
    """刪除指定 VM 的所有 NAT 規則（VM 刪除時使用）。"""
    from app.repositories import nat_rule as nat_repo  # noqa: PLC0415

    deleted = nat_repo.delete_rules_by_vmid(session, vmid)  # type: ignore[arg-type]
    if deleted:
        _sync_haproxy(session)


def remove_nat_rules_by_internal_port(
    session: object, vmid: int, internal_port: int, protocol: str
) -> None:
    """刪除指定 VM 特定內部 port 的 NAT 規則（刪除連線 edge 時使用）。"""
    from app.repositories import nat_rule as nat_repo  # noqa: PLC0415

    deleted = nat_repo.delete_rules_by_vmid_and_port(  # type: ignore[arg-type]
        session, vmid, internal_port, protocol
    )
    if deleted:
        _sync_haproxy(session)


def sync_to_gateway(session: object) -> None:
    """手動觸發 haproxy 同步（供管理員 API 使用）。
    Gateway VM 未設定時拋錯（讓 API 回 500，給使用者明確提示）。
    """
    _sync_haproxy(session)
