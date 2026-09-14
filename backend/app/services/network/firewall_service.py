"""防火牆服務 — 整合 Proxmox 防火牆 API 與圖形化拓撲管理。

設計原則：
- Proxmox 是防火牆規則的 source of truth
- DB 只儲存圖形佈局（節點座標）
- 由 SkyLab 管理的規則以 `SkyLab:` 前綴作為 comment 標記
- 預設策略：policy_in=DROP, policy_out=ACCEPT（只出不進）
- 防火牆一旦啟用不允許關閉
"""

import logging
import re
from typing import Any

from sqlmodel import Session

from app.core.authorizers import can_bypass_resource_ownership
from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.infrastructure.proxmox import get_proxmox_api_for_node
from app.infrastructure.proxmox.operations import ResourceType
from app.models.user import User
from app.repositories import firewall_layout as layout_repo
from app.repositories import resource as resource_repo
from app.schemas.firewall import (
    PortSpec,
    PublishedService,
    PublishedServiceCreate,
    PublishedServiceRef,
    TopologyEdge,
    TopologyNode,
    TopologyResponse,
)
from app.services.proxmox import proxmox_service

logger = logging.getLogger(__name__)

# 預設佈局位置（首次開啟時自動排列）
_DEFAULT_GATEWAY_X = 800.0
_DEFAULT_GATEWAY_Y = 300.0

# SkyLab 管理規則的 comment 前綴
_CC_PREFIX = "SkyLab:"
_GATEWAY_COMMENT = f"{_CC_PREFIX}gateway:default"
_BLOCK_EXTRA_PREFIX = f"{_CC_PREFIX}block-extra:"
_GATEWAY_FULL_ACCESS_COMMENT = f"{_CC_PREFIX}gateway:full-access"


def _from_punycode_hostname(hostname: str) -> str:
    result_labels = []
    for label in hostname.split("."):
        if label.lower().startswith("xn--"):
            try:
                decoded = label[4:].encode("ascii").decode("punycode")
                result_labels.append(decoded)
            except Exception as e:
                logger.debug("Punycode decode failed for label %s: %s", label, e)
                result_labels.append(label)
        else:
            result_labels.append(label)
    return ".".join(result_labels)


# ─── Proxmox 防火牆 API 封裝 ─────────────────────────────────────────────────


def _firewall_api(node: str, vmid: int, resource_type: ResourceType):
    """回傳 VM/LXC 的 proxmoxer 防火牆端點"""
    proxmox = get_proxmox_api_for_node(node)
    if resource_type == "qemu":
        return proxmox.nodes(node).qemu(vmid).firewall
    return proxmox.nodes(node).lxc(vmid).firewall


def _upsert_marker_rule(
    node: str,
    vmid: int,
    resource_type: ResourceType,
    *,
    dest: str,
    comment: str,
) -> str:
    """冪等地建立/更新一條 out-DROP 規則，以 comment 為唯一標記。

    新規則永遠插入到規則清單最後（bottom），避免覆蓋上方的 ACCEPT 規則。

    回傳 'created' / 'updated' / 'skipped'。
    """
    try:
        rules = _firewall_api(node, vmid, resource_type).rules.get() or []
    except Exception as e:
        # 不可 silent fallback：拿不到現有規則就直接 raise，避免重複插入或覆蓋既有規則
        logger.error(
            "取得 %s/%s 防火牆規則失敗，無法安全 upsert (comment=%s): %s",
            node, vmid, comment, e,
        )
        raise ProxmoxError(
            t("firewall.getRulesFailed", resourceType=resource_type, vmid=vmid, error=e)
        ) from e
    existing = next(
        (r for r in rules if (r.get("comment") or "").strip() == comment), None
    )
    if existing is None:
        # 插入到最末位（pos = 目前規則數）
        _firewall_api(node, vmid, resource_type).rules.post(
            type="out", action="DROP", dest=dest, enable=1, comment=comment,
            pos=len(rules),
        )
        return "created"
    if (existing.get("dest") or "") != dest:
        _firewall_api(node, vmid, resource_type).rules(existing.get("pos")).put(
            type="out", action="DROP", dest=dest, enable=1, comment=comment,
        )
        return "updated"
    # dest 一致，但若不是最末位，移動到底部以避免被上方規則覆蓋
    try:
        cur_pos = int(existing.get("pos"))
        last_pos = len(rules) - 1
        if cur_pos < last_pos:
            _firewall_api(node, vmid, resource_type).rules(cur_pos).put(
                moveto=last_pos,
            )
            return "updated"
    except Exception as e:
        logger.debug(
            "重排 %s/%s 規則 pos 失敗 (comment=%s)，視為 skipped: %s",
            node, vmid, comment, e,
        )
    return "skipped"


def _extra_block_comment(dest: str) -> str:
    """為單條額外封鎖規則產生穩定 comment（含 dest 雜湊）。"""
    import hashlib  # noqa: PLC0415
    digest = hashlib.sha1(dest.encode("utf-8")).hexdigest()[:8]
    return f"{_BLOCK_EXTRA_PREFIX}{digest}"


def _apply_extra_block_rules(
    node: str,
    vmid: int,
    resource_type: ResourceType,
    targets: list[str],
) -> dict[str, list]:
    """為單一 VM/LXC 同步 extra block 規則：upsert 目標清單 + 清除孤兒。"""
    stats: dict[str, list] = {"created": [], "updated": [], "skipped": [], "deleted": [], "errors": []}
    desired = {_extra_block_comment(t): t for t in targets}
    try:
        rules = _firewall_api(node, vmid, resource_type).rules.get() or []
    except Exception as e:
        stats["errors"].append({"vmid": vmid, "error": f"list rules failed: {e}"})
        return stats

    # 清除不在 desired 內、但帶有 block-extra 前綴的孤兒規則
    for r in rules:
        comment = (r.get("comment") or "").strip()
        if comment.startswith(_BLOCK_EXTRA_PREFIX) and comment not in desired:
            try:
                _firewall_api(node, vmid, resource_type).rules(r.get("pos")).delete()
                stats["deleted"].append(comment)
            except Exception as e:
                stats["errors"].append({"vmid": vmid, "error": f"delete orphan {comment}: {e}"})

    # upsert desired 規則
    for comment, dest in desired.items():
        try:
            action = _upsert_marker_rule(
                node, vmid, resource_type, dest=dest, comment=comment,
            )
            stats[action].append(dest)
        except Exception as e:
            stats["errors"].append({"vmid": vmid, "dest": dest, "error": str(e)})
    return stats


def get_vm_firewall_rules(node: str, vmid: int, resource_type: ResourceType) -> list[dict]:
    """從 Proxmox 取得 VM 防火牆規則列表"""
    try:
        rules = _firewall_api(node, vmid, resource_type).rules.get()
        return rules or []
    except Exception as e:
        logger.warning(f"無法取得 VM {vmid} 防火牆規則: {e}")
        return []


def create_rule(
    node: str, vmid: int, resource_type: ResourceType, rule: dict
) -> None:
    """在 Proxmox 建立防火牆規則"""
    try:
        _firewall_api(node, vmid, resource_type).rules.post(**rule)
    except Exception as e:
        raise ProxmoxError(t("firewall.createRuleFailed", error=e))


def update_rule(
    node: str, vmid: int, resource_type: ResourceType, pos: int, rule: dict
) -> None:
    """更新指定位置的防火牆規則"""
    try:
        _firewall_api(node, vmid, resource_type).rules(pos).put(**rule)
    except Exception as e:
        raise ProxmoxError(t("firewall.updateRuleFailed", pos=pos, error=e))


def delete_rule_by_pos(
    node: str, vmid: int, resource_type: ResourceType, pos: int
) -> None:
    """刪除指定位置的防火牆規則"""
    try:
        _firewall_api(node, vmid, resource_type).rules(pos).delete()
    except Exception as e:
        raise ProxmoxError(t("firewall.deleteRuleFailed", pos=pos, error=e))


def get_firewall_options(node: str, vmid: int, resource_type: ResourceType) -> dict:
    """取得 VM 防火牆選項（是否啟用、預設策略）"""
    try:
        return _firewall_api(node, vmid, resource_type).options.get()
    except Exception as e:
        logger.warning(f"無法取得 VM {vmid} 防火牆選項: {e}")
        return {}


def _set_firewall_options(
    node: str, vmid: int, resource_type: ResourceType, **options
) -> None:
    """設定 VM 防火牆選項"""
    try:
        _firewall_api(node, vmid, resource_type).options.put(**options)
    except Exception as e:
        raise ProxmoxError(t("firewall.setOptionsFailed", error=e))


# ─── 防火牆強制啟用 ────────────────────────────────────────────────────────────


def ensure_firewall_enabled(node: str, vmid: int, resource_type: ResourceType) -> None:
    """確保防火牆已啟用且入站仍為 DROP（VM 啟動時呼叫）。

    學生之間的隔離完全靠這兩件事：機器都在同一個 bridge、同一個 IP 子網，
    沒有 VLAN 隔離，擋下來的是每台自己的 policy_in=DROP。只檢查 enable
    不夠 —— 防火牆開著但 policy_in 被改成 ACCEPT 的話，這台就對整個實驗室
    子網敞開，拓樸的白名單也失去意義。

    policy_in 沒有值時代表沿用 PVE 預設（DROP），不需要處理。
    """
    try:
        opts = get_firewall_options(node, vmid, resource_type)
        fixes: dict[str, Any] = {}
        if not opts.get("enable"):
            fixes["enable"] = 1
        policy_in = opts.get("policy_in")
        if policy_in and str(policy_in).upper() != "DROP":
            fixes["policy_in"] = "DROP"
        if fixes:
            _set_firewall_options(node, vmid, resource_type, **fixes)
            logger.warning(f"VM {vmid}: 已回復防火牆設定 {fixes}")
    except Exception as e:
        logger.error(f"VM {vmid}: 確認防火牆啟用失敗: {e}")


def sync_block_local_subnet_rules() -> dict:
    """掃描所有 pool 內 VM/LXC，同步管理員設定的額外封鎖網段規則（含孤兒清理）。

    回傳 {"extra_blocks": {...}} 統計。
    """
    from app.core.db import engine  # noqa: PLC0415
    from app.infrastructure.proxmox.operations import (
        list_all_resources,  # noqa: PLC0415
    )
    from app.services.network import ip_management_service  # noqa: PLC0415

    with Session(engine) as s:
        subnet_config = ip_management_service.get_subnet_config(s)
        extra_blocks = ip_management_service.get_extra_blocked_subnets(subnet_config)
    if not extra_blocks:
        return {"noop": True, "reason": "未設定任何額外封鎖網段"}

    extra_aggregate: dict[str, list] = {
        "created": [], "updated": [], "skipped": [], "deleted": [], "errors": [],
    }

    for r in list_all_resources():
        vmid = int(r["vmid"])
        node = r.get("node")
        rtype = "lxc" if r.get("type") == "lxc" else "qemu"
        if not node:
            continue
        try:
            sub = _apply_extra_block_rules(node, vmid, rtype, extra_blocks)
            for k in extra_aggregate:
                extra_aggregate[k].extend(sub.get(k, []))
        except Exception as e:
            extra_aggregate["errors"].append({"vmid": vmid, "error": str(e)})

    logger.info(
        "block-extra 同步 -> targets=%s: created=%d updated=%d skipped=%d deleted=%d errors=%d",
        extra_blocks,
        len(extra_aggregate["created"]), len(extra_aggregate["updated"]),
        len(extra_aggregate["skipped"]), len(extra_aggregate["deleted"]),
        len(extra_aggregate["errors"]),
    )
    return {
        "extra_blocks": {"targets": extra_blocks, **extra_aggregate},
    }


def setup_default_rules(node: str, vmid: int, resource_type: ResourceType) -> None:
    """VM 建立後設定預設防火牆規則：
    - 啟用防火牆
    - policy_in=DROP（預設拒絕入站）
    - policy_out=ACCEPT（允許出站）
    - 套用管理員設定的額外封鎖網段（來自 IP 管理設定）
    - 新增預設出站 ACCEPT 規則作為往網關的 topology 標記
    """
    try:
        # 啟用防火牆並設定預設策略
        _set_firewall_options(
            node, vmid, resource_type,
            enable=1,
            policy_in="DROP",
            policy_out="ACCEPT",
        )
        logger.info(f"VM {vmid}: 設定防火牆預設策略 in=DROP, out=ACCEPT")

        # 套用管理員設定的額外封鎖網段（多筆）
        try:
            from app.core.db import engine  # noqa: PLC0415
            from app.services.network import ip_management_service  # noqa: PLC0415

            with Session(engine) as s:
                subnet_config = ip_management_service.get_subnet_config(s)
                extra_blocks = ip_management_service.get_extra_blocked_subnets(subnet_config)
            if extra_blocks:
                sub = _apply_extra_block_rules(node, vmid, resource_type, extra_blocks)
                logger.info(
                    f"VM {vmid}: extra-block 規則 created={len(sub['created'])} "
                    f"updated={len(sub['updated'])} deleted={len(sub['deleted'])} "
                    f"errors={len(sub['errors'])}"
                )
        except Exception as e:
            logger.warning(
                f"VM {vmid}: 套用額外封鎖網段規則失敗 (非致命): {e}"
            )

        # 新增預設出站規則（作為圖形介面的「往網關」連線標記，排在 DROP 之後）
        gateway_rule = {
            "type": "out",
            "action": "ACCEPT",
            "enable": 1,
            "comment": _GATEWAY_COMMENT,
        }
        _firewall_api(node, vmid, resource_type).rules.post(**gateway_rule)
        logger.info(f"VM {vmid}: 已新增預設出站規則（往網關）")

        # 新增 Gateway VM → VM 全埠 ACCEPT 規則（1-65535 TCP+UDP）
        try:
            from app.core.db import engine  # noqa: PLC0415
            from app.services.network import ip_management_service  # noqa: PLC0415

            with Session(engine) as s:
                subnet_config = ip_management_service.get_subnet_config(s)
            if subnet_config and subnet_config.gateway_vm_ip:
                gw_ip = subnet_config.gateway_vm_ip
                for proto in ("tcp", "udp"):
                    gw_access_rule = {
                        "type": "in",
                        "action": "ACCEPT",
                        "source": gw_ip,
                        "dport": "1:65535",
                        "proto": proto,
                        "enable": 1,
                        "comment": _GATEWAY_FULL_ACCESS_COMMENT,
                    }
                    _firewall_api(node, vmid, resource_type).rules.post(**gw_access_rule)
                logger.info(
                    f"VM {vmid}: 已新增 Gateway VM ({gw_ip}) → VM 全埠 ACCEPT 規則"
                )
        except Exception as gw_err:
            logger.warning(f"VM {vmid}: 新增 Gateway 全埠規則失敗（非致命）: {gw_err}")

    except Exception as e:
        logger.error(f"VM {vmid}: 設定防火牆預設規則失敗: {e}")
        raise ProxmoxError(t("firewall.setupDefaultRulesFailed", vmid=vmid, error=e))


# ─── 連線管理（高階 API）─────────────────────────────────────────────────────


def _get_vm_ip(vmid: int, session: object = None) -> str | None:
    """取得 VM 的 IP 位址。
    優先從 Proxmox 即時查詢；若 VM 離線則回退到 DB 快取。
    查詢成功時自動更新 DB 快取。
    """
    from app.repositories import resource as resource_repo  # noqa: PLC0415

    ip: str | None = None
    try:
        resource = proxmox_service.find_resource(vmid)
        node = resource["node"]
        resource_type = resource["type"]
        ip = proxmox_service.get_ip_address(node, vmid, resource_type)
    except Exception as e:
        logger.debug(
            "VM %s 即時取 IP 失敗，將回退到 DB 快取: %s", vmid, e
        )

    if session is None:
        return ip

    # 有即時 IP 就寫回快取；Proxmox 取不到就回退 DB 快取（DB 出錯會自行 rollback）
    return resource_repo.sync_ip_cache(session=session, vmid=vmid, live_ip=ip)  # type: ignore[arg-type]


def _parse_connection_comment(comment: str) -> dict | None:
    """解析 SkyLab 管理的規則 comment，回傳連線資訊。
    格式（有端口）:
      SkyLab:{src}->gateway:{port}/{proto}   → gateway_connection
      SkyLab:gateway->{tgt}:{port}/{proto}   → internet_connection
      SkyLab:{src}->{tgt}:{port}/{proto}     → connection
    格式（無端口，如 icmp/esp 等）:
      SkyLab:{src}->gateway:{proto}          → gateway_connection  (port=0)
      SkyLab:gateway->{tgt}:{proto}          → internet_connection (port=0)
      SkyLab:{src}->{tgt}:{proto}            → connection          (port=0)
    """
    if not comment or not comment.startswith(_CC_PREFIX):
        return None

    payload = comment[len(_CC_PREFIX):]

    # 往網關的預設規則
    if payload == "gateway:default":
        return {"type": "gateway_default"}

    # SkyLab:{source}->gateway:{port}/{proto}  （有端口）
    match = re.match(r"^(\d+)->gateway:(\d+)/(\w+)$", payload)
    if match:
        return {
            "type": "gateway_connection",
            "source_vmid": int(match.group(1)),
            "port": int(match.group(2)),
            "protocol": match.group(3),
        }

    # SkyLab:{source}->gateway:{proto}  （無端口，協定名以字母開頭）
    match = re.match(r"^(\d+)->gateway:([a-zA-Z]\w*)$", payload)
    if match:
        return {
            "type": "gateway_connection",
            "source_vmid": int(match.group(1)),
            "port": 0,
            "protocol": match.group(2),
        }

    # SkyLab:gateway->{target}:{port}/{proto}  （有端口）
    match = re.match(r"^gateway->(\d+):(\d+)/(\w+)$", payload)
    if match:
        return {
            "type": "internet_connection",
            "target_vmid": int(match.group(1)),
            "port": int(match.group(2)),
            "protocol": match.group(3),
        }

    # SkyLab:gateway->{target}:{proto}  （無端口）
    match = re.match(r"^gateway->(\d+):([a-zA-Z]\w*)$", payload)
    if match:
        return {
            "type": "internet_connection",
            "target_vmid": int(match.group(1)),
            "port": 0,
            "protocol": match.group(2),
        }

    # SkyLab:{source}->{target}:{port}/{proto}  （有端口）
    match = re.match(r"^(\d+)->(\d+):(\d+)/(\w+)$", payload)
    if match:
        return {
            "type": "connection",
            "source_vmid": int(match.group(1)),
            "target_vmid": int(match.group(2)),
            "port": int(match.group(3)),
            "protocol": match.group(4),
        }

    # SkyLab:{source}->{target}:{proto}  （無端口）
    match = re.match(r"^(\d+)->(\d+):([a-zA-Z]\w*)$", payload)
    if match:
        return {
            "type": "connection",
            "source_vmid": int(match.group(1)),
            "target_vmid": int(match.group(2)),
            "port": 0,
            "protocol": match.group(3),
        }

    return None


def _make_connection_comment(
    source_vmid: int, target_vmid: int, port: int, protocol: str
) -> str:
    """產生連線規則的 comment（port=0 表示無端口協定）"""
    if port == 0:
        return f"{_CC_PREFIX}{source_vmid}->{target_vmid}:{protocol}"
    return f"{_CC_PREFIX}{source_vmid}->{target_vmid}:{port}/{protocol}"


def _make_rule_fields(port: int, protocol: str) -> dict:
    """產生 Proxmox 防火牆規則的 proto/dport 欄位（無端口協定省略 dport）"""
    fields: dict = {"proto": protocol}
    if port != 0:
        fields["dport"] = str(port)
    return fields


def create_connection(
    source_vmid: int | None,
    target_vmid: int | None,
    ports: list[PortSpec],
    direction: str = "one_way",
    session: object = None,
) -> None:
    """建立 VM 間連線（或 VM 到網關，或 Internet 入站）。

    Internet 入站（source_vmid=None）：在 target VM 上建立入站允許規則。
      - 若 port_spec.external_port 有值，額外建立 DNAT 規則（需傳入 session）。
    往網關（target_vmid=None）：在 source VM 上建立出站允許規則。
    VM 間連線：在 target VM 上建立入站允許規則，source 為 source VM 的 IP。
    雙向連線：同時在兩個 VM 上建立規則。
    """
    if not ports:
        raise BadRequestError(t("firewall.atLeastOnePortRequired"))

    # ── Internet → VM（入站開放）────────────────────────────────────────────
    if source_vmid is None:
        if target_vmid is None:
            raise BadRequestError(t("firewall.sourceAndTargetCannotBothBeGateway"))
        try:
            tgt_resource = proxmox_service.find_resource(target_vmid)
        except NotFoundError:
            raise BadRequestError(t("firewall.targetVmNotFound", vmid=target_vmid))
        tgt_node = tgt_resource["node"]
        tgt_type = tgt_resource["type"]

        # 判斷是否需要 Gateway VM（有 external_port 或 domain 的情況）
        needs_gateway = any(
            (p.external_port is not None and p.port != 0)
            or (getattr(p, "domain", None) is not None and p.port != 0)
            for p in ports
        )
        if needs_gateway:
            if session is None:
                raise BadRequestError(t("firewall.dbSessionRequiredForPortForwarding"))
            from app.repositories import gateway_config as gw_repo  # noqa: PLC0415
            gw_cfg = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
            if gw_cfg is None or not gw_cfg.host or not gw_cfg.encrypted_private_key:
                raise BadRequestError(
                    t("firewall.gatewayNotConfiguredForExternalAccess")
                )

        # 取得 VM IP（NAT / 反向代理規則需要）——在建立任何規則前先驗證
        if needs_gateway:
            tgt_ip = _get_vm_ip(target_vmid, session)
            if tgt_ip is None:
                raise BadRequestError(
                    t("firewall.targetVmNoIpForExternalAccess", vmid=target_vmid)
                )
        else:
            tgt_ip = None

        # 記錄已建立的防火牆規則 comment，供失敗時 rollback
        created_comments: list[str] = []
        try:
            for port_spec in ports:
                comment = (
                    f"{_CC_PREFIX}gateway->{target_vmid}:{port_spec.protocol}"
                    if port_spec.port == 0
                    else f"{_CC_PREFIX}gateway->{target_vmid}:{port_spec.port}/{port_spec.protocol}"
                )
                rule = {
                    "type": "in",
                    "action": "ACCEPT",
                    **_make_rule_fields(port_spec.port, port_spec.protocol),
                    "enable": 1,
                    "comment": comment,
                }
                create_rule(tgt_node, target_vmid, tgt_type, rule)
                created_comments.append(comment)

                if port_spec.port == 0 or session is None:
                    continue

                domain = getattr(port_spec, "domain", None)
                enable_https = getattr(port_spec, "enable_https", True)

                if domain:
                    # 🌐 反向代理（Traefik）
                    from app.services.network import (
                        reverse_proxy_service,  # noqa: PLC0415
                    )
                    reverse_proxy_service.apply_reverse_proxy_rule_for_domain(
                        session=session,
                        vmid=target_vmid,
                        vm_ip=tgt_ip,
                        domain=domain,
                        internal_port=port_spec.port,
                        enable_https=enable_https,
                    )
                elif port_spec.external_port is not None:
                    # 🔌 Port 轉發（haproxy）
                    from app.services.network import nat_service  # noqa: PLC0415
                    nat_service.apply_nat_rule(
                        session=session,
                        vmid=target_vmid,
                        vm_ip=tgt_ip,
                        external_port=port_spec.external_port,
                        internal_port=port_spec.port,
                        protocol=port_spec.protocol,
                    )
                # else: 🔓 僅開放防火牆，不需額外操作
        except Exception:
            # 回退：刪除已建立的 Proxmox 防火牆規則
            if created_comments:
                try:
                    existing = get_vm_firewall_rules(tgt_node, target_vmid, tgt_type)
                    comment_set = set(created_comments)
                    to_delete = sorted(
                        [r["pos"] for r in existing if r.get("comment") in comment_set],
                        reverse=True,
                    )
                    for pos in to_delete:
                        try:
                            delete_rule_by_pos(tgt_node, target_vmid, tgt_type, pos)
                        except Exception as rb_err:
                            logger.warning(f"rollback 刪除規則 pos={pos} 失敗: {rb_err}")
                except Exception as rb_err:
                    logger.warning(f"rollback 取得規則列表失敗: {rb_err}")
            raise
        return

    try:
        src_resource = proxmox_service.find_resource(source_vmid)
    except NotFoundError:
        raise BadRequestError(t("firewall.sourceVmNotFound", vmid=source_vmid))

    src_node = src_resource["node"]
    src_type = src_resource["type"]

    # ── VM → Gateway（出站上網，含還原 gateway:default marker）──────────────
    if target_vmid is None:
        # 若 gateway:default marker 不存在則補建
        existing = get_vm_firewall_rules(src_node, source_vmid, src_type)
        has_default = any(
            r.get("comment") == _GATEWAY_COMMENT for r in existing
        )
        if not has_default:
            create_rule(src_node, source_vmid, src_type, {
                "type": "out",
                "action": "ACCEPT",
                "enable": 1,
                "comment": _GATEWAY_COMMENT,
            })
        for port_spec in ports:
            comment = (
                f"{_CC_PREFIX}{source_vmid}->gateway:{port_spec.protocol}"
                if port_spec.port == 0
                else f"{_CC_PREFIX}{source_vmid}->gateway:{port_spec.port}/{port_spec.protocol}"
            )
            rule = {
                "type": "out",
                "action": "ACCEPT",
                **_make_rule_fields(port_spec.port, port_spec.protocol),
                "enable": 1,
                "comment": comment,
            }
            create_rule(src_node, source_vmid, src_type, rule)
        return

    # ── VM → VM ─────────────────────────────────────────────────────────────
    src_ip = _get_vm_ip(source_vmid, session)
    if not src_ip:
        raise BadRequestError(
            t("firewall.sourceVmNoIp", vmid=source_vmid)
        )

    try:
        tgt_resource = proxmox_service.find_resource(target_vmid)
    except NotFoundError:
        raise BadRequestError(t("firewall.targetVmNotFound", vmid=target_vmid))

    tgt_node = tgt_resource["node"]
    tgt_type = tgt_resource["type"]

    tgt_ip = _get_vm_ip(target_vmid, session)
    if not tgt_ip:
        raise BadRequestError(
            t("firewall.targetVmNoIp", vmid=target_vmid)
        )

    for port_spec in ports:
        comment_fwd = _make_connection_comment(source_vmid, target_vmid, port_spec.port, port_spec.protocol)
        rule_fields = _make_rule_fields(port_spec.port, port_spec.protocol)

        # 在目標 VM 建立入站允許規則
        create_rule(tgt_node, target_vmid, tgt_type, {
            "type": "in",
            "action": "ACCEPT",
            "source": src_ip,
            **rule_fields,
            "enable": 1,
            "comment": comment_fwd,
        })

        # 在來源 VM 建立出站允許規則（插在 block-local-subnet DROP 之前）
        create_rule(src_node, source_vmid, src_type, {
            "type": "out",
            "action": "ACCEPT",
            "pos": 0,
            "dest": tgt_ip,
            **rule_fields,
            "enable": 1,
            "comment": comment_fwd,
        })

        if direction == "bidirectional":
            comment_rev = _make_connection_comment(target_vmid, source_vmid, port_spec.port, port_spec.protocol)

            # 在來源 VM 建立反向入站規則
            create_rule(src_node, source_vmid, src_type, {
                "type": "in",
                "action": "ACCEPT",
                "source": tgt_ip,
                **rule_fields,
                "enable": 1,
                "comment": comment_rev,
            })

            # 在目標 VM 建立反向出站規則（插在 block-local-subnet DROP 之前）
            create_rule(tgt_node, target_vmid, tgt_type, {
                "type": "out",
                "action": "ACCEPT",
                "pos": 0,
                "dest": src_ip,
                **rule_fields,
                "enable": 1,
                "comment": comment_rev,
            })


def delete_connection(
    source_vmid: int | None,
    target_vmid: int | None,
    ports: list[PortSpec] | None = None,
    session: object = None,
) -> None:
    """刪除 VM 間連線（透過 comment 前綴識別 SkyLab 管理的規則）。
    從最高 pos 開始刪除，避免 pos 位移問題。
    Internet→VM 時同步清理 NAT DB 記錄並更新 Gateway VM haproxy。
    """
    # ── Internet → VM 入站規則刪除 ─────────────────────────────────────────
    if source_vmid is None:
        if target_vmid is None:
            return
        try:
            tgt_resource = proxmox_service.find_resource(target_vmid)
        except NotFoundError:
            return
        _delete_matching_rules(
            node=tgt_resource["node"],
            vmid=target_vmid,
            resource_type=tgt_resource["type"],
            source_vmid=None,
            target_vmid=target_vmid,
            ports=ports,
        )
        # 同步清理 Gateway VM 規則（haproxy + Traefik）
        if session is not None:
            from app.services.network import (  # noqa: PLC0415
                nat_service,
                reverse_proxy_service,
            )
            if ports is None:
                nat_service.remove_nat_rules_for_vmid(session, target_vmid)
                reverse_proxy_service.remove_reverse_proxy_rules_for_vmid(session, target_vmid)
            else:
                for port_spec in ports:
                    nat_service.remove_nat_rules_by_internal_port(
                        session, target_vmid, port_spec.port, port_spec.protocol
                    )
                    reverse_proxy_service.remove_reverse_proxy_rules_by_internal_port(
                        session, target_vmid, port_spec.port
                    )
        return

    # 決定要在哪個 VM 上刪除規則
    if target_vmid is None:
        # 刪除往網關的規則（在 source VM 的 out 規則）
        try:
            src_resource = proxmox_service.find_resource(source_vmid)
        except NotFoundError:
            return
        _delete_matching_rules(
            node=src_resource["node"],
            vmid=source_vmid,
            resource_type=src_resource["type"],
            source_vmid=source_vmid,
            target_vmid=None,
            ports=ports,
        )
    else:
        # VM-to-VM：刪除雙方所有相關規則（IN/OUT 四條，含雙向）
        try:
            tgt_resource = proxmox_service.find_resource(target_vmid)
        except NotFoundError:
            return
        try:
            src_resource = proxmox_service.find_resource(source_vmid)
        except NotFoundError:
            return

        # src→tgt：target 的 IN + source 的 OUT
        _delete_matching_rules(
            node=tgt_resource["node"], vmid=target_vmid,
            resource_type=tgt_resource["type"],
            source_vmid=source_vmid, target_vmid=target_vmid, ports=ports,
        )
        try:
            _delete_matching_rules(
                node=src_resource["node"], vmid=source_vmid,
                resource_type=src_resource["type"],
                source_vmid=source_vmid, target_vmid=target_vmid, ports=ports,
            )
        except Exception as e:
            logger.warning(
                "刪除 src=%s→tgt=%s OUT 規則失敗 (best-effort): %s",
                source_vmid, target_vmid, e,
            )

        # tgt→src（雙向反向）：source 的 IN + target 的 OUT
        try:
            _delete_matching_rules(
                node=src_resource["node"], vmid=source_vmid,
                resource_type=src_resource["type"],
                source_vmid=target_vmid, target_vmid=source_vmid, ports=ports,
            )
        except Exception as e:
            logger.warning(
                "刪除 tgt=%s→src=%s IN 規則失敗 (best-effort): %s",
                target_vmid, source_vmid, e,
            )
        try:
            _delete_matching_rules(
                node=tgt_resource["node"], vmid=target_vmid,
                resource_type=tgt_resource["type"],
                source_vmid=target_vmid, target_vmid=source_vmid, ports=ports,
            )
        except Exception as e:
            logger.warning(
                "刪除 tgt=%s→src=%s OUT 規則失敗 (best-effort): %s",
                target_vmid, source_vmid, e,
            )


def _delete_matching_rules(
    node: str,
    vmid: int,
    resource_type: ResourceType,
    source_vmid: int | None,
    target_vmid: int | None,
    ports: list[PortSpec] | None,
) -> None:
    """刪除符合條件的 SkyLab 管理規則（從最高 pos 開始）"""
    rules = get_vm_firewall_rules(node, vmid, resource_type)

    # 找到要刪除的規則 pos（從高到低排序）
    to_delete = []
    for rule in rules:
        comment = rule.get("comment", "") or ""
        parsed = _parse_connection_comment(comment)
        if not parsed:
            continue

        if source_vmid is None and target_vmid is not None:
            # 刪除 internet→VM 入站規則
            if parsed["type"] == "internet_connection" and parsed.get("target_vmid") == target_vmid:
                if ports is None:
                    to_delete.append(rule["pos"])
                else:
                    for port_spec in ports:
                        if (
                            parsed.get("port") == port_spec.port
                            and parsed.get("protocol") == port_spec.protocol
                        ):
                            to_delete.append(rule["pos"])
        elif target_vmid is None:
            # 匹配往網關的規則（gateway_default 或 gateway_connection）
            is_gateway_rule = (
                parsed["type"] == "gateway_default"
                or (
                    parsed["type"] == "gateway_connection"
                    and parsed.get("source_vmid") == source_vmid
                )
            )
            if is_gateway_rule:
                if ports is None:
                    to_delete.append(rule["pos"])
                elif parsed["type"] == "gateway_connection":
                    for port_spec in ports:
                        if (
                            parsed.get("port") == port_spec.port
                            and parsed.get("protocol") == port_spec.protocol
                        ):
                            to_delete.append(rule["pos"])
        else:
            # 匹配 VM 間連線規則
            if (
                parsed.get("source_vmid") == source_vmid
                and parsed.get("target_vmid") == target_vmid
            ):
                if ports is None:
                    to_delete.append(rule["pos"])
                else:
                    for port_spec in ports:
                        if (
                            parsed.get("port") == port_spec.port
                            and parsed.get("protocol") == port_spec.protocol
                        ):
                            to_delete.append(rule["pos"])

    # 從最大 pos 開始刪除（避免位移）
    for pos in sorted(set(to_delete), reverse=True):
        try:
            delete_rule_by_pos(node, vmid, resource_type, pos)
        except Exception as e:
            logger.warning(f"刪除規則 pos={pos} 失敗: {e}")


# ─── 拓撲資料聚合 ─────────────────────────────────────────────────────────────


def get_connections_from_rules(vmids: list[int]) -> list[TopologyEdge]:
    """從 VM 的防火牆規則中解析出 SkyLab 管理的連線（edges）"""
    edges: dict[str, TopologyEdge] = {}

    for vmid in vmids:
        try:
            resource = proxmox_service.find_resource(vmid)
            node = resource["node"]
            resource_type = resource["type"]
            rules = get_vm_firewall_rules(node, vmid, resource_type)
        except Exception as e:
            logger.warning(
                "讀取 VMID=%s 防火牆規則失敗，拓撲圖將略過該節點連線: %s",
                vmid, e,
            )
            continue

        for rule in rules:
            comment = rule.get("comment", "") or ""
            parsed = _parse_connection_comment(comment)
            if not parsed:
                continue

            if parsed["type"] == "gateway_default":
                # 預設網關規則（無特定 port）
                edge_key = f"{vmid}->None"
                if edge_key not in edges:
                    edges[edge_key] = TopologyEdge(
                        source_vmid=vmid,
                        target_vmid=None,
                        ports=[],
                        direction="one_way",
                    )
            elif parsed["type"] == "gateway_connection":
                # 有特定 port 的往網關規則
                src = parsed["source_vmid"]
                port = parsed["port"]
                proto = parsed["protocol"]
                edge_key = f"{src}->None"
                if edge_key not in edges:
                    edges[edge_key] = TopologyEdge(
                        source_vmid=src,
                        target_vmid=None,
                        ports=[],
                        direction="one_way",
                    )
                edges[edge_key].ports.append(PortSpec(port=port, protocol=proto))
            elif parsed["type"] == "internet_connection":
                tgt = parsed["target_vmid"]
                port = parsed["port"]
                proto = parsed["protocol"]
                edge_key = f"None->{tgt}"
                if edge_key not in edges:
                    edges[edge_key] = TopologyEdge(
                        source_vmid=None,
                        target_vmid=tgt,
                        ports=[],
                        direction="one_way",
                    )
                edges[edge_key].ports.append(PortSpec(port=port, protocol=proto))
            elif parsed["type"] == "connection":
                src = parsed["source_vmid"]
                tgt = parsed["target_vmid"]
                port = parsed["port"]
                proto = parsed["protocol"]
                edge_key = f"{src}->{tgt}"
                if edge_key not in edges:
                    edges[edge_key] = TopologyEdge(
                        source_vmid=src,
                        target_vmid=tgt,
                        ports=[],
                        direction="one_way",
                    )
                edges[edge_key].ports.append(PortSpec(port=port, protocol=proto))

    return list(edges.values())


def _enrich_edges_from_db(
    edges: list[TopologyEdge], session: Session
) -> None:
    """將 Internet→VM edge 中的 port specs 充實 DB 資訊。
    - NatRule → 填入 external_port
    - ReverseProxyRule → 填入 domain + enable_https
    """
    from app.repositories import nat_rule as nat_repo  # noqa: PLC0415
    from app.repositories import reverse_proxy as rp_repo  # noqa: PLC0415

    # 只處理 Internet→VM edges（source_vmid=None）
    inbound_edges = [e for e in edges if e.source_vmid is None and e.target_vmid is not None]
    if not inbound_edges:
        return

    # 一次載入所有相關 VM 的 NAT / Reverse Proxy 規則
    vmids = {e.target_vmid for e in inbound_edges}
    nat_rules = nat_repo.list_rules(session)
    rp_rules = rp_repo.list_rules(session)

    # 建立快查 dict：(vmid, internal_port, protocol) → NatRule
    nat_lookup: dict[tuple[int, int, str], object] = {}
    for r in nat_rules:
        if r.vmid in vmids:
            nat_lookup[(r.vmid, r.internal_port, r.protocol)] = r

    # 建立快查 dict：(vmid, internal_port) → ReverseProxyRule
    rp_lookup: dict[tuple[int, int], object] = {}
    for r in rp_rules:
        if r.vmid in vmids:
            rp_lookup[(r.vmid, r.internal_port)] = r

    # 充實 port specs
    for edge in inbound_edges:
        tgt = edge.target_vmid
        for port_spec in edge.ports:
            # 先查 reverse proxy
            rp_key = (tgt, port_spec.port)
            rp_rule = rp_lookup.get(rp_key)
            if rp_rule:
                port_spec.domain = rp_rule.domain
                port_spec.enable_https = rp_rule.enable_https
                continue

            # 再查 NAT
            nat_key = (tgt, port_spec.port, port_spec.protocol)
            nat_rule = nat_lookup.get(nat_key)
            if nat_rule:
                port_spec.external_port = nat_rule.external_port


def get_topology(user: User, session: Session) -> TopologyResponse:
    """取得使用者的防火牆拓撲（節點 + 連線）

    權限邏輯：
    - superuser: 所有 VM
    - 一般使用者: 只看自己的 VM
    """
    # 取得有權限的 user_id 清單
    if can_bypass_resource_ownership(user):
        all_resources = resource_repo.get_all_resources(session=session)
        target_vmids = [r.vmid for r in all_resources]
    else:
        own_resources = resource_repo.get_resources_by_user(
            session=session, user_id=user.id
        )
        target_vmids = [r.vmid for r in own_resources]

    # 取得使用者的佈局記錄
    layout_records = layout_repo.get_layout(session=session, user_id=user.id)
    layout_map: dict[str, tuple[float, float]] = {}
    for rec in layout_records:
        key = f"{rec.vmid}:{rec.node_type}"
        layout_map[key] = (rec.position_x, rec.position_y)

    # 建立節點清單
    nodes: list[TopologyNode] = []
    valid_vmids: list[int] = []

    # 自動排列起始位置
    col_x = 100.0
    row_y_step = 120.0

    for i, vmid in enumerate(target_vmids):
        try:
            resource = proxmox_service.find_resource(vmid)
        except Exception as e:
            logger.warning(
                "拓撲圖跳過 VMID=%s（無法在 Proxmox 找到資源）: %s", vmid, e
            )
            continue

        node_name = _from_punycode_hostname(resource.get("name", f"VM-{vmid}"))
        status = resource.get("status", "unknown")
        ip_address = None
        firewall_enabled = False

        try:
            ip_address = proxmox_service.get_ip_address(
                resource["node"], vmid, resource["type"]
            )
        except Exception as e:
            logger.debug(
                "拓撲圖 VMID=%s 即時 IP 查詢失敗（改查 DB 快取）: %s", vmid, e
            )
        # 有即時 IP 就寫回快取，否則回退 DB 快取。DB 出錯時 sync_ip_cache 會
        # rollback，避免 session 帶著無效交易撐到後面的 _enrich_edges_from_db。
        ip_address = resource_repo.sync_ip_cache(
            session=session, vmid=vmid, live_ip=ip_address
        )

        try:
            opts = get_firewall_options(resource["node"], vmid, resource["type"])
            firewall_enabled = bool(opts.get("enable", False))
        except Exception as e:
            logger.debug(
                "拓撲圖 VMID=%s 防火牆狀態查詢失敗（將顯示為 disabled）: %s",
                vmid, e,
            )

        layout_key = f"{vmid}:vm"
        if layout_key in layout_map:
            px, py = layout_map[layout_key]
        else:
            px = col_x
            py = 100.0 + i * row_y_step

        nodes.append(
            TopologyNode(
                vmid=vmid,
                name=node_name,
                node_type="vm",
                vm_type=resource.get("type", "qemu"),
                status=status,
                ip_address=ip_address,
                firewall_enabled=firewall_enabled,
                position_x=px,
                position_y=py,
            )
        )
        valid_vmids.append(vmid)

    # 新增網關節點
    gw_key = "None:gateway"
    gw_x, gw_y = layout_map.get(gw_key, (_DEFAULT_GATEWAY_X, _DEFAULT_GATEWAY_Y))
    nodes.append(
        TopologyNode(
            vmid=None,
            name="Internet",
            node_type="gateway",
            status="online",
            ip_address=None,
            firewall_enabled=True,
            position_x=gw_x,
            position_y=gw_y,
        )
    )

    # 解析連線並充實 DB 資訊（external_port / domain）
    edges = get_connections_from_rules(valid_vmids)
    _enrich_edges_from_db(edges, session)

    return TopologyResponse(nodes=nodes, edges=edges)


# ─── 單台 VM：對外服務與迷你拓撲 ──────────────────────────────────────────────


def _service_url(domain: str | None, enable_https: bool) -> str | None:
    if not domain:
        return None
    scheme = "https" if enable_https else "http"
    return f"{scheme}://{domain}"


def _service_from_spec(
    spec: PortSpec, *, firewall_rule_present: bool
) -> PublishedService:
    if spec.domain:
        mode = "domain"
    elif spec.external_port is not None:
        mode = "port_forward"
    else:
        mode = "firewall_only"
    return PublishedService(
        port=spec.port,
        protocol=spec.protocol,
        mode=mode,
        domain=spec.domain,
        enable_https=spec.enable_https,
        external_port=spec.external_port,
        url=_service_url(spec.domain, spec.enable_https),
        firewall_rule_present=firewall_rule_present,
    )


def list_vm_published_services(
    vmid: int, session: Session
) -> list[PublishedService]:
    """列出這台 VM 的所有 Internet 入站發布（對外網址 / port 轉發 / 僅開放）。

    以 Proxmox 上 ``SkyLab:gateway->{vmid}`` 規則為主；DB 裡有反向代理或
    NAT 紀錄但 Proxmox 上找不到對應規則的（例如舊版反向代理頁直接建的網址），
    也一併列出並標記 ``firewall_rule_present=False``，讓使用者看得到、刪得掉。
    """
    from app.repositories import nat_rule as nat_repo  # noqa: PLC0415
    from app.repositories import reverse_proxy as rp_repo  # noqa: PLC0415

    edges = get_connections_from_rules([vmid])
    _enrich_edges_from_db(edges, session)

    services: list[PublishedService] = []
    seen: set[tuple[int, str]] = set()
    for edge in edges:
        if edge.source_vmid is not None or edge.target_vmid != vmid:
            continue
        for spec in edge.ports:
            key = (spec.port, spec.protocol)
            if key in seen:
                continue
            seen.add(key)
            services.append(_service_from_spec(spec, firewall_rule_present=True))

    for rp_rule in rp_repo.list_rules_by_vmid(session, vmid):
        key = (rp_rule.internal_port, "tcp")
        if key in seen:
            continue
        seen.add(key)
        services.append(
            _service_from_spec(
                PortSpec(
                    port=rp_rule.internal_port,
                    protocol="tcp",
                    domain=rp_rule.domain,
                    enable_https=rp_rule.enable_https,
                ),
                firewall_rule_present=False,
            )
        )

    for nat_rule in nat_repo.list_rules_by_vmid(session, vmid):
        key = (nat_rule.internal_port, nat_rule.protocol)
        if key in seen:
            continue
        seen.add(key)
        services.append(
            _service_from_spec(
                PortSpec(
                    port=nat_rule.internal_port,
                    protocol=nat_rule.protocol,
                    external_port=nat_rule.external_port,
                ),
                firewall_rule_present=False,
            )
        )

    services.sort(key=lambda s: (s.port, s.protocol))
    return services


def publish_vm_service(
    vmid: int, data: PublishedServiceCreate, session: Session
) -> PublishedService:
    """發布一條對外服務：走 ``create_connection``（先開防火牆，再套 NAT / 反向代理）。"""
    existing = {
        (s.port, s.protocol) for s in list_vm_published_services(vmid, session)
    }
    if (data.port, data.protocol) in existing:
        raise BadRequestError(
            t(
                "firewall.servicePortAlreadyPublished",
                port=data.port,
                protocol=data.protocol,
            )
        )
    if data.mode == "domain" and data.domain:
        from app.services.network import reverse_proxy_service  # noqa: PLC0415

        reverse_proxy_service.assert_domain_available(session, data.domain)
    spec = data.to_port_spec()
    create_connection(
        source_vmid=None, target_vmid=vmid, ports=[spec], session=session
    )
    return _service_from_spec(spec, firewall_rule_present=True)


def unpublish_vm_service(
    vmid: int, ref: PublishedServiceRef, session: Session
) -> None:
    """撤下一條對外服務：刪 Proxmox 入站規則並清 NAT / 反向代理紀錄。"""
    delete_connection(
        source_vmid=None,
        target_vmid=vmid,
        ports=[PortSpec(port=ref.port, protocol=ref.protocol)],
        session=session,
    )


def replace_vm_service(
    vmid: int,
    current: PublishedServiceRef,
    replacement: PublishedServiceCreate,
    session: Session,
) -> PublishedService:
    """換掉一條服務的發布方式：先撤下舊的，再依新設定發布。"""
    existing = {
        (s.port, s.protocol) for s in list_vm_published_services(vmid, session)
    }
    if (current.port, current.protocol) not in existing:
        raise NotFoundError(
            t("firewall.serviceNotFound", port=current.port, protocol=current.protocol)
        )
    same_port = (current.port, current.protocol) == (
        replacement.port,
        replacement.protocol,
    )
    if not same_port and (replacement.port, replacement.protocol) in existing:
        raise BadRequestError(
            t(
                "firewall.servicePortAlreadyPublished",
                port=replacement.port,
                protocol=replacement.protocol,
            )
        )
    if replacement.mode == "domain" and replacement.domain:
        from app.repositories import reverse_proxy as rp_repo  # noqa: PLC0415
        from app.services.network import reverse_proxy_service  # noqa: PLC0415

        # 同一條服務沿用原本網域時，不能把自己的紀錄算成衝突
        own_rule = next(
            (
                r
                for r in rp_repo.list_rules_by_vmid(session, vmid)
                if r.internal_port == current.port
                and r.domain == replacement.domain
            ),
            None,
        )
        reverse_proxy_service.assert_domain_available(
            session,
            replacement.domain,
            exclude_rule_id=own_rule.id if own_rule else None,
        )
    unpublish_vm_service(vmid, current, session)
    spec = replacement.to_port_spec()
    create_connection(
        source_vmid=None, target_vmid=vmid, ports=[spec], session=session
    )
    return _service_from_spec(spec, firewall_rule_present=True)


def _topology_node_for_vm(
    vmid: int,
    resource: dict,
    session: Session | None,
    *,
    x: float,
    y: float,
    with_details: bool,
) -> TopologyNode:
    ip_address: str | None = None
    firewall_enabled = False
    if with_details:
        try:
            ip_address = _get_vm_ip(vmid, session)
        except Exception as e:  # pragma: no cover - 防禦性
            logger.debug("VMID=%s IP 查詢失敗: %s", vmid, e)
        try:
            opts = get_firewall_options(resource["node"], vmid, resource["type"])
            firewall_enabled = bool(opts.get("enable", False))
        except Exception as e:
            logger.debug("VMID=%s 防火牆狀態查詢失敗: %s", vmid, e)
    return TopologyNode(
        vmid=vmid,
        name=_from_punycode_hostname(resource.get("name", f"VM-{vmid}")),
        node_type="vm",
        vm_type=resource.get("type", "qemu"),
        status=resource.get("status", "unknown"),
        ip_address=ip_address,
        firewall_enabled=firewall_enabled,
        position_x=x,
        position_y=y,
    )


_MINI_PEER_X = 0.0
_MINI_CENTER_X = 340.0
_MINI_GATEWAY_X = 680.0
_MINI_ROW_H = 110.0


def get_vm_topology(vmid: int, session: Session) -> TopologyResponse:
    """以單台 VM 為中心的迷你拓撲：這台 VM、Internet 節點、有連線關係的其他 VM。

    只讀這台 VM 自己的防火牆規則：VM 對 VM 的連線在雙方都會留下同一組
    ``SkyLab:{src}->{tgt}`` 註解，所以從單邊就能還原所有跟它有關的連線。
    """
    resource = proxmox_service.find_resource(vmid)
    edges = get_connections_from_rules([vmid])
    _enrich_edges_from_db(edges, session)

    peer_vmids = sorted(
        {
            v
            for edge in edges
            for v in (edge.source_vmid, edge.target_vmid)
            if v is not None and v != vmid
        }
    )

    nodes: list[TopologyNode] = []
    known: set[int | None] = {None, vmid}
    for i, peer in enumerate(peer_vmids):
        try:
            peer_resource = proxmox_service.find_resource(peer)
        except Exception as e:
            logger.debug("迷你拓撲略過 VMID=%s（找不到資源）: %s", peer, e)
            continue
        nodes.append(
            _topology_node_for_vm(
                peer,
                peer_resource,
                session,
                x=_MINI_PEER_X,
                y=40.0 + i * _MINI_ROW_H,
                with_details=False,
            )
        )
        known.add(peer)

    rows = max(len(peer_vmids), 1)
    center_y = 40.0 + (rows - 1) * _MINI_ROW_H / 2
    nodes.append(
        _topology_node_for_vm(
            vmid,
            resource,
            session,
            x=_MINI_CENTER_X,
            y=center_y,
            with_details=True,
        )
    )
    nodes.append(
        TopologyNode(
            vmid=None,
            name="Internet",
            node_type="gateway",
            status="online",
            ip_address=None,
            firewall_enabled=True,
            position_x=_MINI_GATEWAY_X,
            position_y=center_y,
        )
    )

    visible_edges = [
        edge
        for edge in edges
        if edge.source_vmid in known and edge.target_vmid in known
    ]
    return TopologyResponse(nodes=nodes, edges=visible_edges)
