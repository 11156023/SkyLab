"""Guest OS 身份偵測的純函式契約層（不做任何網路 / DB I/O）。

本模組定義 Guest OS 身份的統一資料契約與各來源的正規化規則：

.. code-block:: json

    {
      "family": "linux",
      "id": "ubuntu",
      "version": "24.04",
      "pretty_name": "Ubuntu 24.04.3 LTS",
      "source": "qemu_guest_agent",
      "confidence": "high"
    }

來源與可信度對應：

- QEMU Guest Agent ``get-osinfo`` → family/id/version 完整，confidence=high。
- PVE LXC ``ostype`` → 發行版欄位（pct 設定用途），confidence=medium；
  ``unmanaged`` 不代表任何發行版身份。
- PVE QEMU ``ostype`` → 只是 VM 相容性設定，僅能推 family hint；
  ``l26`` 只能說 linux（low），不得標示 Ubuntu/Debian。

I/O（agent 探測、PVE config 讀取、DB 回寫）在
``app.services.os_identity_service``；本模組只做轉換與判讀。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

OS_FAMILY_LINUX = "linux"
OS_FAMILY_WINDOWS = "windows"
OS_FAMILY_UNKNOWN = "unknown"

OS_SOURCE_QEMU_GUEST_AGENT = "qemu_guest_agent"
OS_SOURCE_PVE_OSTYPE = "pve_ostype"
OS_SOURCE_OS_RELEASE = "os_release"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_UNKNOWN = "unknown"

# PVE QEMU config ostype 相容性代碼（僅 family hint 用）。
_QEMU_OSTYPE_WINDOWS = frozenset(
    {
        "wxp",
        "w2k",
        "w2k3",
        "w2k8",
        "vista",
        "win7",
        "win8",
        "win8.1",
        "win10",
        "win11",
        "win2000",
    }
)
_QEMU_OSTYPE_LINUX_HINT = frozenset({"l24", "l26"})

# PVE LXC ostype（pct 用於發行版偵測與 setup；unmanaged 不代表任何發行版）。
_LXC_OSTYPE_UNMANAGED = "unmanaged"

_WINDOWS_ID = "mswindows"

GuestOS = dict[str, Any]


def build_guest_os(
    *,
    family: str,
    source: str,
    confidence: str,
    os_id: str | None = None,
    version: str | None = None,
    pretty_name: str | None = None,
) -> GuestOS:
    """組出契約 dict；一律包含固定欄位，未知值為 None。"""
    return {
        "family": family,
        "id": os_id,
        "version": version,
        "pretty_name": pretty_name,
        "source": source,
        "confidence": confidence,
    }


def unknown_guest_os() -> GuestOS:
    return build_guest_os(
        family=OS_FAMILY_UNKNOWN,
        source="",
        confidence=CONFIDENCE_UNKNOWN,
    )


def normalize_qga_osinfo(payload: Mapping[str, Any] | None) -> GuestOS | None:
    """正規化 QEMU Guest Agent ``get-osinfo`` 回應。

    無法判讀（空 payload、無 id/name）時回 None，由呼叫端決定 fallback。
    """
    if not isinstance(payload, Mapping):
        return None
    os_id = str(payload.get("id") or "").strip().lower()
    if not os_id:
        return None
    version = (
        str(payload.get("version") or "").strip()
        or str(payload.get("version-id") or "").strip()
        or None
    )
    pretty_name = (
        str(payload.get("pretty-name") or "").strip()
        or str(payload.get("name") or "").strip()
        or None
    )
    if os_id == _WINDOWS_ID:
        family = OS_FAMILY_WINDOWS
    else:
        family = OS_FAMILY_LINUX
    return build_guest_os(
        family=family,
        source=OS_SOURCE_QEMU_GUEST_AGENT,
        confidence=CONFIDENCE_HIGH,
        os_id=os_id,
        version=version,
        pretty_name=pretty_name,
    )


def normalize_lxc_ostype(ostype: str | None) -> GuestOS:
    """正規化 PVE LXC ``ostype``（發行版欄位）。"""
    normalized = str(ostype or "").strip().lower()
    if not normalized or normalized == _LXC_OSTYPE_UNMANAGED:
        return unknown_guest_os()
    return build_guest_os(
        family=OS_FAMILY_LINUX,
        source=OS_SOURCE_PVE_OSTYPE,
        confidence=CONFIDENCE_MEDIUM,
        os_id=normalized,
    )


def normalize_qemu_ostype(ostype: str | None) -> GuestOS:
    """正規化 PVE QEMU ``ostype``（VM 相容性設定，只能是 family hint）。

    ``l26`` 只能代表 Linux 系家族（confidence=low），不得推導成 Ubuntu/Debian。
    """
    normalized = str(ostype or "").strip().lower()
    if not normalized:
        return unknown_guest_os()
    if normalized in _QEMU_OSTYPE_WINDOWS:
        return build_guest_os(
            family=OS_FAMILY_WINDOWS,
            source=OS_SOURCE_PVE_OSTYPE,
            confidence=CONFIDENCE_MEDIUM,
        )
    if normalized in _QEMU_OSTYPE_LINUX_HINT:
        return build_guest_os(
            family=OS_FAMILY_LINUX,
            source=OS_SOURCE_PVE_OSTYPE,
            confidence=CONFIDENCE_LOW,
        )
    return unknown_guest_os()


def normalize_os_release_text(text: str | None) -> GuestOS | None:
    """解析 ``/etc/os-release`` 內容（經執行器取回後的純文字）。"""
    if not text:
        return None
    fields: dict[str, str] = {}
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        value = value.strip().strip('"').strip("'")
        fields[key.strip().upper()] = value
    os_id = fields.get("ID", "").strip().lower()
    if not os_id:
        return None
    return build_guest_os(
        family=OS_FAMILY_LINUX,
        source=OS_SOURCE_OS_RELEASE,
        confidence=CONFIDENCE_HIGH,
        os_id=os_id,
        version=fields.get("VERSION_ID") or None,
        pretty_name=fields.get("PRETTY_NAME") or None,
    )


def is_windows_guest_identity(guest_os: Any) -> bool | None:
    """由結構化 guest_os 判斷是否 Windows。

    回傳 True/False 表示有結構化證據；None 表示沒有可信結構資料，
    呼叫端應退回舊的字串判斷（legacy fallback）。
    """
    if not isinstance(guest_os, Mapping):
        return None
    family = str(guest_os.get("family") or "").strip().lower()
    if family == OS_FAMILY_WINDOWS:
        return True
    if family == OS_FAMILY_LINUX:
        return False
    return None


def format_os_token(guest_os: Any) -> str:
    """把 guest_os 契約壓成給模型看的單行 token。

    例：``linux/ubuntu 24.04 (high)``、``windows 11 (high)``、
    ``linux (low)``；無可信資料時一律 ``unknown``。
    """
    if not isinstance(guest_os, Mapping):
        return OS_FAMILY_UNKNOWN
    family = str(guest_os.get("family") or "").strip().lower()
    if family not in {OS_FAMILY_LINUX, OS_FAMILY_WINDOWS}:
        return OS_FAMILY_UNKNOWN
    parts = [family]
    os_id = str(guest_os.get("id") or "").strip().lower()
    if os_id and os_id not in {family, _WINDOWS_ID}:
        parts = [f"{family}/{os_id}"]
    version = str(guest_os.get("version") or "").strip()
    if version:
        parts.append(version)
    confidence = str(guest_os.get("confidence") or "").strip().lower()
    return " ".join(parts) + f" ({confidence or CONFIDENCE_UNKNOWN})"


__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "CONFIDENCE_UNKNOWN",
    "OS_FAMILY_LINUX",
    "OS_FAMILY_UNKNOWN",
    "OS_FAMILY_WINDOWS",
    "OS_SOURCE_OS_RELEASE",
    "OS_SOURCE_PVE_OSTYPE",
    "OS_SOURCE_QEMU_GUEST_AGENT",
    "build_guest_os",
    "format_os_token",
    "is_windows_guest_identity",
    "normalize_lxc_ostype",
    "normalize_os_release_text",
    "normalize_qemu_ostype",
    "normalize_qga_osinfo",
    "unknown_guest_os",
]
