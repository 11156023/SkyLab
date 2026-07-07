"""VM vs LXC 工作負載自動判斷規則引擎。

純函式、無外部呼叫：輸入申請表單欄位，輸出建議的 resource_type、
信心水準與使用者可讀的理由。規則先命中先贏。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# 需要完整虛擬化的 OS 關鍵字（LXC 只能跑 Linux）
_NON_LINUX_OS_KEYWORDS = ("windows", "freebsd", "openbsd", "netbsd", "macos")

# 在 LXC 內受限的工作負載關鍵字
_FULL_KERNEL_KEYWORDS = (
    "docker",
    "kubernetes",
    "k8s",
    "kernel",
    "核心模組",
    "nested",
    "嵌套",
    "vpn",
    "wireguard",
    "systemd-nspawn",
)

# 輕量規格上限（≤ 這個規格的 Linux 負載建議容器）
_LIGHT_MAX_CORES = 2
_LIGHT_MAX_MEMORY_MB = 4096


@dataclass(frozen=True)
class WorkloadAdvice:
    resource_type: Literal["vm", "lxc"]
    confidence: Literal["high", "medium", "low"]
    reasons: list[str] = field(default_factory=list)


def _contains_any(text: str, keywords: tuple[str, ...]) -> str | None:
    lowered = text.casefold()
    for keyword in keywords:
        if keyword in lowered:
            return keyword
    return None


def advise(
    *,
    environment_type: str | None,
    os_info: str | None,
    reason: str | None,
    cores: int | None,
    memory: int | None,
    gpu_mapping_id: str | None,
    service_template_slug: str | None,
) -> WorkloadAdvice:
    """依申請內容判斷建議的資源型別（規則先命中先贏）。"""
    os_text = " ".join(filter(None, [os_info, environment_type]))
    workload_text = " ".join(filter(None, [reason, environment_type, os_info]))

    # 1) GPU passthrough 必須完整虛擬化
    if gpu_mapping_id:
        return WorkloadAdvice(
            resource_type="vm",
            confidence="high",
            reasons=["需要 GPU passthrough，必須使用完整虛擬機"],
        )

    # 2) 非 Linux OS 無法以容器執行
    os_hit = _contains_any(os_text, _NON_LINUX_OS_KEYWORDS)
    if os_hit:
        return WorkloadAdvice(
            resource_type="vm",
            confidence="high",
            reasons=[f"作業系統（{os_hit}）非 Linux，容器無法執行，需使用虛擬機"],
        )

    # 3) 需要完整核心權限的工作負載
    kernel_hit = _contains_any(workload_text, _FULL_KERNEL_KEYWORDS)
    if kernel_hit:
        return WorkloadAdvice(
            resource_type="vm",
            confidence="medium",
            reasons=[
                f"工作負載（{kernel_hit}）需要完整核心權限，"
                "LXC 容器內受限，建議使用虛擬機"
            ],
        )

    # 4) 服務範本走 community-scripts 容器部署
    if service_template_slug:
        return WorkloadAdvice(
            resource_type="lxc",
            confidence="high",
            reasons=["服務範本以容器部署，啟動快、資源占用低"],
        )

    # 5) 輕量 Linux 負載：容器密度高（需有實際規格才判斷）
    has_spec = cores is not None or memory is not None
    if (
        has_spec
        and (cores or 0) <= _LIGHT_MAX_CORES
        and (memory or 0) <= _LIGHT_MAX_MEMORY_MB
    ):
        return WorkloadAdvice(
            resource_type="lxc",
            confidence="medium",
            reasons=["輕量工作負載，容器密度高、秒級啟動"],
        )

    # 6) 預設：一般 Linux 負載建議容器
    return WorkloadAdvice(
        resource_type="lxc",
        confidence="low",
        reasons=[
            "一般 Linux 工作負載預設建議容器；"
            "如需完整虛擬化請改用手動模式選擇 VM"
        ],
    )
