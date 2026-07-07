"""VM vs LXC 自動判斷規則引擎表驅動測試。"""

import pytest

from app.services.vm.workload_advisor import WorkloadAdvice, advise


def _advise(**overrides: object) -> WorkloadAdvice:
    params: dict = {
        "environment_type": None,
        "os_info": None,
        "reason": None,
        "cores": None,
        "memory": None,
        "gpu_mapping_id": None,
        "service_template_slug": None,
    }
    params.update(overrides)
    return advise(**params)  # type: ignore[arg-type]


CASES: list[tuple[str, dict, str, str]] = [
    # (case, inputs, expected_type, expected_confidence)
    ("gpu_forces_vm", {"gpu_mapping_id": "gpu-0"}, "vm", "high"),
    ("windows_os_forces_vm", {"os_info": "Windows Server 2022"}, "vm", "high"),
    ("windows_env_forces_vm", {"environment_type": "Windows 桌面"}, "vm", "high"),
    ("freebsd_forces_vm", {"os_info": "FreeBSD 14"}, "vm", "high"),
    ("docker_keyword_vm", {"reason": "我需要跑 Docker Compose 專案"}, "vm", "medium"),
    ("k8s_keyword_vm", {"reason": "架設 kubernetes 叢集練習"}, "vm", "medium"),
    ("kernel_keyword_vm", {"reason": "編譯自訂 kernel 模組作業"}, "vm", "medium"),
    ("vpn_keyword_vm", {"reason": "架設 WireGuard VPN 伺服器"}, "vm", "medium"),
    ("nested_keyword_vm", {"reason": "需要嵌套虛擬化測試"}, "vm", "medium"),
    (
        "service_template_lxc",
        {"service_template_slug": "nginx", "cores": 8, "memory": 16384},
        "lxc",
        "high",
    ),
    (
        "light_linux_lxc",
        {"os_info": "Ubuntu 24.04", "cores": 2, "memory": 2048},
        "lxc",
        "medium",
    ),
    (
        "default_linux_lxc",
        {"os_info": "Debian 12", "cores": 8, "memory": 16384},
        "lxc",
        "low",
    ),
    ("all_empty_default_lxc", {}, "lxc", "low"),
    # 優先序：GPU 蓋過 service template；Windows 蓋過輕量規格
    (
        "gpu_beats_service_template",
        {"gpu_mapping_id": "gpu-1", "service_template_slug": "nginx"},
        "vm",
        "high",
    ),
    (
        "windows_beats_light_spec",
        {"os_info": "Windows 11", "cores": 1, "memory": 1024},
        "vm",
        "high",
    ),
    # 關鍵字比對不分大小寫
    ("docker_case_insensitive", {"reason": "DOCKER swarm lab"}, "vm", "medium"),
]


@pytest.mark.parametrize(
    ("case", "inputs", "expected_type", "expected_confidence"),
    CASES,
    ids=[c[0] for c in CASES],
)
def test_advise_rules(
    case: str, inputs: dict, expected_type: str, expected_confidence: str
) -> None:
    advice = _advise(**inputs)
    assert advice.resource_type == expected_type, case
    assert advice.confidence == expected_confidence, case
    assert advice.reasons, "每個判斷都必須附使用者可讀理由"


def test_reasons_are_readable() -> None:
    advice = _advise(gpu_mapping_id="gpu-0")
    assert any("GPU" in r for r in advice.reasons)
