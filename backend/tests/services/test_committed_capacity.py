"""通用排程的容量改看「已分配規格」：關機的機器也算，CPU／RAM 各套超賣倍率。

個人申請、快速練習與可用時段查詢都走 build_live_node_capacities，原本只看
此刻的 CPU 使用率與記憶體用量：機器關機就像不存在，CPU 超賣倍率套在使用率
上也形同沒擋住。
"""

from __future__ import annotations

import pytest

from app.domain.placement.schemas import NodeSnapshot, ResourceSnapshot
from app.services.vm import placement_support

GIB = 1024**3


@pytest.fixture(autouse=True)
def _no_gpu_or_templates(monkeypatch):
    monkeypatch.setattr(placement_support, "gpu_used_slots", lambda: {})
    monkeypatch.setattr(placement_support, "template_guest_vmids", lambda *a, **k: set())


def _node(*, cores=8, memory_gb=16, used_memory_gb=2, cpu_usage=0.05):
    return NodeSnapshot(
        node="pve1",
        status="online",
        cpu_ratio=cpu_usage,
        maxcpu=cores,
        mem_bytes=used_memory_gb * GIB,
        maxmem_bytes=memory_gb * GIB,
        disk_bytes=10 * GIB,
        maxdisk_bytes=100 * GIB,
    )


def _guest(vmid=201, *, cores=8, memory_gb=12, status="stopped"):
    return ResourceSnapshot(
        vmid=vmid,
        node="pve1",
        resource_type="qemu",
        status=status,
        maxcpu=cores,
        maxmem_bytes=memory_gb * GIB,
    )


def _capacity(**kwargs):
    nodes = kwargs.pop("nodes", [_node()])
    resources = kwargs.pop("resources", [_guest()])
    (capacity,) = placement_support.build_live_node_capacities(
        nodes=nodes, resources=resources, **kwargs
    )
    return capacity


def test_stopped_machines_count_against_memory():
    # 即時看還剩 12.4 GB，但關機的機器已經分配掉 12 GB：16 − 12 − 10% 餘裕
    capacity = _capacity()
    assert capacity.allocatable_memory_bytes == pytest.approx(2.4 * GIB, rel=1e-3)


def test_cpu_overcommit_applies_to_allocated_cores():
    # 8 核 ×2 = 16 vCPU，已分配 8，扣 10% 餘裕後剩 6.4（即時值會是 14）
    capacity = _capacity(cpu_overcommit_ratio=2.0)
    assert capacity.allocatable_cpu_cores == pytest.approx(6.4)


def test_memory_overcommit_raises_the_allocation_ceiling():
    # 16 GB ×1.5 = 24 GB 可分配，已分配 12，扣 10% 實體餘裕 → 10.4 GB
    capacity = _capacity(memory_overcommit_ratio=1.5)
    assert capacity.allocatable_memory_bytes == pytest.approx(10.4 * GIB, rel=1e-3)


def test_memory_overcommit_never_exceeds_live_free_memory():
    # 倍率放很寬也不能超過此刻實體記憶體剩下的量（16 − 14 − 1.6 = 0.4 GB）
    capacity = _capacity(
        nodes=[_node(used_memory_gb=14)],
        resources=[],
        memory_overcommit_ratio=2.0,
    )
    assert capacity.allocatable_memory_bytes == pytest.approx(0.4 * GIB, rel=1e-3)


def test_platform_template_guests_are_ignored(monkeypatch):
    monkeypatch.setattr(placement_support, "template_guest_vmids", lambda *a, **k: {201})
    capacity = _capacity()
    assert capacity.allocatable_memory_bytes == pytest.approx(12.4 * GIB, rel=1e-3)


def test_fully_allocated_node_is_not_a_candidate():
    capacity = _capacity(resources=[_guest(memory_gb=16)])
    assert capacity.allocatable_memory_bytes == 0
    assert capacity.candidate is False


def test_applying_twice_gives_the_same_result():
    capacity = _capacity(cpu_overcommit_ratio=2.0)
    before = (capacity.allocatable_cpu_cores, capacity.allocatable_memory_bytes)
    placement_support.apply_committed_allocations(
        {"pve1": capacity},
        nodes=[_node()],
        resources=[_guest()],
        cpu_overcommit_ratio=2.0,
        excluded_vmids=set(),
    )
    assert (capacity.allocatable_cpu_cores, capacity.allocatable_memory_bytes) == before
