"""資源進階設定：規格摘要、開機選項（開機順序 / ISO）、標籤。

Proxmox 的 guest config 是這些設定的 source of truth，這裡只做讀寫與驗證，
不在 DB 另存副本。

``onboot``（主機開機時自動啟動）不由使用者設定：``proxmox operations.control``
會在每次電源動作後自動對齊（開機→1、關機→0），這裡不再讀寫它。
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlmodel import Session

from app.core.i18n import t
from app.exceptions import BadRequestError, ProxmoxError
from app.infrastructure.proxmox import get_proxmox_settings_for_node
from app.schemas.resource_settings import (
    BootDevicePublic,
    BootOptionsPublic,
    BootOptionsUpdate,
    IsoImagePublic,
    ResourceMetadataPublic,
    ResourceMetadataUpdate,
    ResourceSpecsPublic,
)
from app.services.proxmox import proxmox_service
from app.services.user import audit_service

logger = logging.getLogger(__name__)

_DISK_KEY_RE = re.compile(r"^(scsi|virtio|sata|ide|nvme)(\d{1,2})$")
_NET_KEY_RE = re.compile(r"^net\d{1,2}$")
# 掛 ISO 的候選 ide 槽位（ide2 是 Proxmox 慣用的光碟槽，cloud-init 常也用 ide2）
_CDROM_SLOT_CANDIDATES = ("ide2", "ide0", "ide1", "ide3")


def _rtype(resource_info: dict[str, Any]) -> str:
    return "lxc" if str(resource_info.get("type") or "") == "lxc" else "qemu"


def _is_running(resource_info: dict[str, Any]) -> bool:
    return str(resource_info.get("status") or "") == "running"


# ─── 規格摘要 ─────────────────────────────────────────────────────────────────


def get_specs(*, vmid: int, resource_info: dict[str, Any]) -> ResourceSpecsPublic:
    rtype = _rtype(resource_info)
    try:
        specs = proxmox_service.get_current_specs(resource_info["node"], vmid, rtype)
    except Exception as exc:
        logger.error("Failed to read specs for %s: %s", vmid, exc)
        raise ProxmoxError(t("resource_settings.readConfigFailed", vmid=vmid))
    return ResourceSpecsPublic(
        vmid=vmid,
        resource_type=rtype,
        cpu_cores=specs.get("cpu"),
        memory_mb=specs.get("memory"),
        disk_gb=specs.get("disk"),
    )


# ─── 開機選項 ─────────────────────────────────────────────────────────────────


def _parse_volid(value: str) -> str | None:
    head = str(value).split(",", 1)[0].strip()
    if not head or head == "none":
        return None
    return head


def _parse_boot_order(raw: Any) -> list[str]:
    if not raw:
        return []
    text = str(raw).strip()
    for part in text.split(","):
        part = part.strip()
        if part.startswith("order="):
            return [p for p in part[len("order=") :].split(";") if p]
    # 舊格式（例如 "cdn"）不再轉換；使用者存一次新順序就會改成 order= 格式
    return []


def _collect_devices(
    config: dict[str, Any],
) -> tuple[list[BootDevicePublic], str | None]:
    """列出可當開機裝置的鍵，並找出目前的光碟槽（排除 cloud-init 磁碟）。"""
    devices: list[BootDevicePublic] = []
    cdrom_slot: str | None = None
    for key in sorted(config.keys()):
        value = config.get(key)
        if not isinstance(value, str):
            continue
        if _DISK_KEY_RE.match(key):
            if "cloudinit" in value:
                continue  # cloud-init 磁碟不能開機，也不能被拿去掛 ISO
            if "media=cdrom" in value:
                volid = _parse_volid(value)
                if cdrom_slot is None:
                    cdrom_slot = key
                devices.append(
                    BootDevicePublic(
                        key=key,
                        kind="cdrom",
                        description=volid.split("/")[-1] if volid else None,
                    )
                )
            else:
                size = None
                for part in value.split(","):
                    if part.startswith("size="):
                        size = part[len("size=") :]
                devices.append(
                    BootDevicePublic(
                        key=key,
                        kind="disk",
                        description=size,
                    )
                )
        elif _NET_KEY_RE.match(key):
            bridge = None
            for part in value.split(","):
                if part.startswith("bridge="):
                    bridge = part[len("bridge=") :]
            devices.append(
                BootDevicePublic(key=key, kind="network", description=bridge)
            )
    return devices, cdrom_slot


def get_boot_options(
    *, vmid: int, resource_info: dict[str, Any]
) -> BootOptionsPublic:
    rtype = _rtype(resource_info)
    node = resource_info["node"]
    try:
        config = proxmox_service.get_config(node, vmid, rtype)
    except Exception as exc:
        logger.error("Failed to read config for %s: %s", vmid, exc)
        raise ProxmoxError(t("resource_settings.readConfigFailed", vmid=vmid))

    if rtype == "lxc":
        return BootOptionsPublic(
            vmid=vmid,
            resource_type="lxc",
            supports_boot_order=False,
            supports_cdrom=False,
            running=_is_running(resource_info),
        )

    devices, cdrom_slot = _collect_devices(config)
    cdrom_iso = _parse_volid(str(config.get(cdrom_slot, ""))) if cdrom_slot else None
    try:
        iso_storage = get_proxmox_settings_for_node(node).iso_storage
    except Exception:
        iso_storage = None
    return BootOptionsPublic(
        vmid=vmid,
        resource_type="qemu",
        supports_boot_order=True,
        boot_order=_parse_boot_order(config.get("boot")),
        boot_devices=devices,
        supports_cdrom=True,
        cdrom_slot=cdrom_slot,
        cdrom_iso=cdrom_iso,
        iso_storage=iso_storage,
        running=_is_running(resource_info),
    )


def list_iso_images(*, resource_info: dict[str, Any]) -> list[IsoImagePublic]:
    node = resource_info["node"]
    try:
        items = proxmox_service.list_iso_images(node)
    except Exception as exc:
        logger.error("Failed to list ISO images on %s: %s", node, exc)
        raise ProxmoxError(t("resource_settings.listIsoFailed"))
    images: list[IsoImagePublic] = []
    for item in items:
        volid = str(item.get("volid") or "")
        if not volid:
            continue
        images.append(
            IsoImagePublic(
                volid=volid,
                name=volid.split("/")[-1],
                size=item.get("size"),
            )
        )
    images.sort(key=lambda i: i.name.lower())
    return images


def _pick_cdrom_slot(config: dict[str, Any], current_slot: str | None) -> str:
    if current_slot:
        return current_slot
    for slot in _CDROM_SLOT_CANDIDATES:
        if slot not in config:
            return slot
    raise BadRequestError(t("resource_settings.noFreeCdromSlot"))


def update_boot_options(
    *,
    session: Session,
    vmid: int,
    resource_info: dict[str, Any],
    user_id: uuid.UUID,
    data: BootOptionsUpdate,
) -> BootOptionsPublic:
    rtype = _rtype(resource_info)
    node = resource_info["node"]

    if rtype == "lxc" and (
        data.boot_order is not None or data.cdrom_iso or data.eject_cdrom
    ):
        raise BadRequestError(t("resource_settings.lxcNoBootOrder"))

    try:
        config = proxmox_service.get_config(node, vmid, rtype)
    except Exception as exc:
        logger.error("Failed to read config for %s: %s", vmid, exc)
        raise ProxmoxError(t("resource_settings.readConfigFailed", vmid=vmid))

    params: dict[str, Any] = {}
    to_delete: list[str] = []
    changes: list[str] = []

    if rtype == "qemu":
        devices, current_slot = _collect_devices(config)
        device_keys = {d.key for d in devices}

        if data.boot_order is not None:
            unknown = [k for k in data.boot_order if k not in device_keys]
            if unknown:
                raise BadRequestError(
                    t("resource_settings.unknownBootDevice", device=", ".join(unknown))
                )
            if data.boot_order:
                params["boot"] = "order=" + ";".join(data.boot_order)
                changes.append(f"boot={params['boot']}")
            else:
                to_delete.append("boot")
                changes.append("boot=(default)")

        if data.cdrom_iso:
            volid = data.cdrom_iso.strip()
            valid = {img.volid for img in list_iso_images(resource_info=resource_info)}
            if volid not in valid:
                raise BadRequestError(t("resource_settings.isoNotFound", volid=volid))
            slot = _pick_cdrom_slot(config, current_slot)
            params[slot] = f"{volid},media=cdrom"
            changes.append(f"{slot}={volid}")
        elif data.eject_cdrom:
            if current_slot is None:
                raise BadRequestError(t("resource_settings.noCdromMounted"))
            params[current_slot] = "none,media=cdrom"
            changes.append(f"{current_slot}=none")

    if not params and not to_delete:
        raise BadRequestError(t("resource_settings.nothingToUpdate"))

    try:
        if to_delete:
            params["delete"] = ",".join(to_delete)
        proxmox_service.update_config(node, vmid, rtype, **params)
    except Exception as exc:
        logger.error("Failed to update boot options for %s: %s", vmid, exc)
        raise ProxmoxError(
            t("resource_settings.updateConfigFailed", vmid=vmid, error=exc)
        )

    audit_service.log_action(
        session=session,
        user_id=user_id,
        vmid=vmid,
        action="config_update",
        details=f"Boot options updated on {rtype} {vmid}: {', '.join(changes)}",
    )
    return get_boot_options(vmid=vmid, resource_info=resource_info)


# ─── 標籤 ─────────────────────────────────────────────────────────────────────


def parse_tags(raw: Any) -> list[str]:
    """Proxmox 的 tags 欄位是 ``;`` 分隔字串（cluster/resources 與 guest config 皆同）。"""
    if not raw:
        return []
    return [tag for tag in str(raw).replace(",", ";").split(";") if tag]


def get_metadata(*, vmid: int, resource_info: dict[str, Any]) -> ResourceMetadataPublic:
    rtype = _rtype(resource_info)
    try:
        config = proxmox_service.get_config(resource_info["node"], vmid, rtype)
    except Exception as exc:
        logger.error("Failed to read config for %s: %s", vmid, exc)
        raise ProxmoxError(t("resource_settings.readConfigFailed", vmid=vmid))
    return ResourceMetadataPublic(vmid=vmid, tags=parse_tags(config.get("tags")))


def update_metadata(
    *,
    session: Session,
    vmid: int,
    resource_info: dict[str, Any],
    user_id: uuid.UUID,
    data: ResourceMetadataUpdate,
) -> ResourceMetadataPublic:
    rtype = _rtype(resource_info)
    node = resource_info["node"]
    params: dict[str, Any] = {}
    if data.tags:
        params["tags"] = ";".join(data.tags)
        changes = f"tags={params['tags']}"
    else:
        params["delete"] = "tags"
        changes = "tags=(none)"

    try:
        proxmox_service.update_config(node, vmid, rtype, **params)
    except Exception as exc:
        logger.error("Failed to update metadata for %s: %s", vmid, exc)
        raise ProxmoxError(
            t("resource_settings.updateConfigFailed", vmid=vmid, error=exc)
        )

    audit_service.log_action(
        session=session,
        user_id=user_id,
        vmid=vmid,
        action="config_update",
        details=f"Tags updated on {rtype} {vmid}: {changes}",
    )
    return get_metadata(vmid=vmid, resource_info=resource_info)


__all__ = [
    "get_boot_options",
    "get_metadata",
    "get_specs",
    "list_iso_images",
    "parse_tags",
    "update_boot_options",
    "update_metadata",
]
