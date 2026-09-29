"""啟動前 GPU 檢查必須讀整張卡的實際用量，而不是 launcher 自己的配置。"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from utils import health_utils

_GIB = 1024 ** 3


def _fake_psutil() -> ModuleType:
    module = ModuleType("psutil")
    module.cpu_percent = lambda interval=None: 10.0
    module.virtual_memory = lambda: SimpleNamespace(percent=20.0, available=64 * _GIB)
    module.disk_usage = lambda path: SimpleNamespace(percent=30.0)
    return module


def _fake_pynvml(calls: list[str]) -> ModuleType:
    module = ModuleType("pynvml")
    module.nvmlInit = lambda: calls.append("init")
    module.nvmlShutdown = lambda: calls.append("shutdown")
    module.nvmlDeviceGetCount = lambda: 1
    module.nvmlDeviceGetHandleByIndex = lambda index: f"gpu{index}"
    module.nvmlDeviceGetMemoryInfo = lambda handle: SimpleNamespace(used=78 * _GIB, total=80 * _GIB)
    module.nvmlDeviceGetUtilizationRates = lambda handle: SimpleNamespace(gpu=87)
    return module


def _torch_must_not_be_used() -> ModuleType:
    module = ModuleType("torch")

    def fail(*args, **kwargs):
        raise AssertionError("NVML 可用時不應透過 torch 建立 CUDA context")

    module.cuda = SimpleNamespace(
        is_available=fail, device_count=fail, memory_allocated=fail, mem_get_info=fail
    )
    return module


def test_gpu_memory_comes_from_nvml_device_totals(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil())
    monkeypatch.setitem(sys.modules, "pynvml", _fake_pynvml(calls))
    monkeypatch.setitem(sys.modules, "torch", _torch_must_not_be_used())

    health = health_utils.check_system_health()

    assert health.gpu_count == 1
    assert health.gpu_memory_used_gb == [78.0]
    assert health.gpu_memory_total_gb == [80.0]
    assert health.gpu_utilization == [87.0]
    assert any("GPU 0 記憶體使用率過高" in warning for warning in health.get_warnings())
    assert calls == ["init", "shutdown"]


def test_falls_back_to_torch_totals_when_nvml_is_missing(monkeypatch) -> None:
    broken_nvml = ModuleType("pynvml")

    def init_fails() -> None:
        raise RuntimeError("NVML Shared Library Not Found")

    broken_nvml.nvmlInit = init_fails
    torch = ModuleType("torch")
    torch.cuda = SimpleNamespace(
        is_available=lambda: True,
        device_count=lambda: 2,
        get_device_properties=lambda index: SimpleNamespace(total_memory=48 * _GIB),
    )
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil())
    monkeypatch.setitem(sys.modules, "pynvml", broken_nvml)
    monkeypatch.setitem(sys.modules, "torch", torch)

    health = health_utils.check_system_health()

    assert health.gpu_count == 2
    assert health.gpu_memory_total_gb == [48.0, 48.0]
    assert health.gpu_memory_used_gb == [0.0, 0.0]
