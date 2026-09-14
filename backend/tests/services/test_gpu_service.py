"""GPU service 的 vGPU/SR-IOV 容量計算測試。

H200_MDEV 是 pve205 上 H200 SR-IOV VF 的真實 PVE 回傳格式
（GET /nodes/{node}/hardware/pci/{path}/mdev）。
"""

import threading
import time

from app.schemas.gpu import GPUDeviceMap, GPUUsageInfo
from app.services.proxmox import gpu_service

H200_MDEV = {
    "available": 1,
    "name": "NVIDIA H200-4C",
    "description": (
        "class=Compute\nmax-instances=32\nmax-instances-per-vm=16\n"
        "framebuffer-size=4096MiB\nnum-heads=1\nmax-resolution=4096x2400\n"
        "license=NVIDIA-vComputeServer,9.0"
    ),
    "type": "nvidia-1436",
}


def _h200_vf_maps(count: int = 32, *, is_mdev: bool = False) -> list[GPUDeviceMap]:
    """同一張卡（bus 0000:15）上的多個 VF。

    真實 PVE 的 map entry 不帶 mdev 旗標，預設 is_mdev=False 模擬實況。
    """
    return [
        GPUDeviceMap(
            node="pve205",
            path=f"0000:15:{i // 8:02d}.{i % 8}",
            id="10de:233b",
            is_mdev=is_mdev,
        )
        for i in range(count)
    ]


class TestParseMdevEntry:
    def test_h200_keyvalue_description(self) -> None:
        profile = gpu_service._parse_mdev_entry(H200_MDEV)
        assert profile.name == "NVIDIA H200-4C"
        assert profile.vram_mb == 4096
        assert profile.max_instances == 32

    def test_legacy_grid_profile_in_description(self) -> None:
        profile = gpu_service._parse_mdev_entry(
            {"type": "nvidia-1", "name": "", "description": "GRID A100-40C"}
        )
        assert profile.vram_mb == 40 * 1024
        assert profile.max_instances == 0

    def test_profile_name_suffix_fallback(self) -> None:
        profile = gpu_service._parse_mdev_entry(
            {"type": "x", "name": "NVIDIA H200 NVL-16Q", "description": ""}
        )
        assert profile.vram_mb == 16 * 1024

    def test_gib_framebuffer_unit(self) -> None:
        profile = gpu_service._parse_mdev_entry(
            {"type": "x", "name": "", "description": "framebuffer-size=4GiB"}
        )
        assert profile.vram_mb == 4096

    def test_unparseable_returns_zero(self) -> None:
        profile = gpu_service._parse_mdev_entry(
            {"type": "x", "name": "mystery", "description": "no vram info"}
        )
        assert profile.vram_mb == 0
        assert profile.max_instances == 0


class TestResolveVramForMapping:
    def test_h200_small_profile_capacity_matches_vf_count(self, monkeypatch) -> None:
        """4C profile：max-instances=32 = VF 數，容量不受額外限制。"""
        monkeypatch.setattr(
            gpu_service,
            "_get_mdev_types",
            lambda node, path: {"nvidia-1436": gpu_service._parse_mdev_entry(H200_MDEV)},
        )
        maps = _h200_vf_maps()
        used = [
            GPUUsageInfo(vmid=100 + i, mdev_type="nvidia-1436", status="running")
            for i in range(5)
        ]
        info = gpu_service._resolve_vram_for_mapping(maps, 1, "", "H200", used)

        assert info.has_mdev is True
        assert info.per_instance_vram_mb == 4096
        assert info.instance_capacity == 32
        assert info.total_vram_mb == 4096 * 32   # 128 GB 可切
        assert info.used_vram_mb == 4096 * 5
        assert all(u.allocated_vram_mb == 4096 for u in used)
        assert gpu_service._effective_capacity(len(maps), info) == 32

    def test_large_profile_caps_below_vf_count(self, monkeypatch) -> None:
        """35GB profile：framebuffer 只能切 4 份，32 個 VF 也只能開 4 台。"""
        big = {
            "type": "nvidia-9999",
            "name": "NVIDIA H200-35C",
            "description": "class=Compute\nmax-instances=4\nframebuffer-size=35840MiB",
        }
        monkeypatch.setattr(
            gpu_service,
            "_get_mdev_types",
            lambda node, path: {"nvidia-9999": gpu_service._parse_mdev_entry(big)},
        )
        maps = _h200_vf_maps()
        info = gpu_service._resolve_vram_for_mapping(maps, 1, "", "H200", [])

        assert info.instance_capacity == 4
        assert gpu_service._effective_capacity(len(maps), info) == 4

    def test_reference_prefers_in_use_profile(self, monkeypatch) -> None:
        """已有 VM 掛載時，容量以該 profile 為準（同卡 profile 必須一致）。"""
        small = gpu_service._parse_mdev_entry(H200_MDEV)
        big = gpu_service._parse_mdev_entry(
            {
                "type": "nvidia-9999",
                "name": "NVIDIA H200-35C",
                "description": "max-instances=4\nframebuffer-size=35840MiB",
            }
        )
        monkeypatch.setattr(
            gpu_service,
            "_get_mdev_types",
            lambda node, path: {"nvidia-1436": small, "nvidia-9999": big},
        )
        maps = _h200_vf_maps()
        used = [GPUUsageInfo(vmid=100, mdev_type="nvidia-1436")]
        info = gpu_service._resolve_vram_for_mapping(maps, 1, "", "H200", used)

        assert info.mdev_profile == "NVIDIA H200-4C"
        assert info.instance_capacity == 32

    def test_passthrough_unchanged(self, monkeypatch) -> None:
        monkeypatch.setattr(gpu_service, "_get_mdev_types", lambda node, path: {})
        maps = [
            GPUDeviceMap(node="pve", path="0000:85:00.0"),
            GPUDeviceMap(node="pve", path="0000:86:00.0"),
        ]
        used = [GPUUsageInfo(vmid=101, status="running")]
        info = gpu_service._resolve_vram_for_mapping(
            maps, 2, "NVIDIA Tesla M60 8G", "TeslaM60", used
        )
        assert info.has_mdev is False
        assert info.total_vram_mb == 8 * 1024 * 2
        assert info.used_vram_mb == 8 * 1024
        assert info.instance_capacity == 0
        assert gpu_service._effective_capacity(len(maps), info) == 2

    def test_mdev_without_profile_info_falls_back_to_vf_count(self, monkeypatch) -> None:
        monkeypatch.setattr(gpu_service, "_get_mdev_types", lambda node, path: None)
        maps = _h200_vf_maps(is_mdev=True)
        info = gpu_service._resolve_vram_for_mapping(maps, 1, "", "H200", [])
        assert info.has_mdev is True
        assert info.instance_capacity == 0
        assert gpu_service._effective_capacity(len(maps), info) == 32

    def test_mdev_detected_by_probe_without_entry_flag(self, monkeypatch) -> None:
        """真實 PVE：map entry 沒有 mdev 旗標，靠 mdev endpoint 探測判定 vGPU。"""
        monkeypatch.setattr(
            gpu_service,
            "_get_mdev_types",
            lambda node, path: {"nvidia-1436": gpu_service._parse_mdev_entry(H200_MDEV)},
        )
        maps = _h200_vf_maps()  # is_mdev=False（實況）
        info = gpu_service._resolve_vram_for_mapping(maps, 1, "", "H200", [])
        assert info.has_mdev is True
        assert info.instance_capacity == 32
        assert info.total_vram_mb == 4096 * 32

    def test_mdev_detected_by_vm_usage_when_probe_fails(self, monkeypatch) -> None:
        """探測失敗（None）但已有 VM 帶 mdev type：仍視為 vGPU，退回 VF 數容量。"""
        monkeypatch.setattr(gpu_service, "_get_mdev_types", lambda node, path: None)
        maps = _h200_vf_maps()
        used = [GPUUsageInfo(vmid=100, mdev_type="nvidia-1436")]
        info = gpu_service._resolve_vram_for_mapping(maps, 1, "", "H200", used)
        assert info.has_mdev is True
        assert info.instance_capacity == 0  # 未知 → _effective_capacity 退回 VF 數
        assert gpu_service._effective_capacity(len(maps), info) == 32

    def test_memory_full_when_probe_returns_empty(self, monkeypatch) -> None:
        """查詢成功但無任何 creatable profile：記憶體已滿，不可再指派。"""
        monkeypatch.setattr(gpu_service, "_get_mdev_types", lambda node, path: {})
        maps = _h200_vf_maps()
        used = [
            GPUUsageInfo(vmid=100 + i, mdev_type=f"nvidia-14{40 + i}")
            for i in range(5)
        ]
        info = gpu_service._resolve_vram_for_mapping(maps, 1, "", "H200", used)
        assert info.has_mdev is True
        assert info.instance_capacity == 5
        capacity = gpu_service._effective_capacity(len(maps), info)
        assert capacity == 5
        assert max(0, capacity - len(used)) == 0  # available = 0

    def test_unknown_used_profiles_flagged(self, monkeypatch) -> None:
        """混合 profile：已佔用的型號查不到規格時 used 標為未知（低估）。"""
        monkeypatch.setattr(
            gpu_service,
            "_get_mdev_types",
            lambda node, path: {"nvidia-1436": gpu_service._parse_mdev_entry(H200_MDEV)},
        )
        maps = _h200_vf_maps()
        used = [
            GPUUsageInfo(vmid=100, mdev_type="nvidia-1436", status="running"),
            # 大 profile，查不到規格
            GPUUsageInfo(vmid=101, mdev_type="nvidia-1443", status="running"),
        ]
        info = gpu_service._resolve_vram_for_mapping(maps, 1, "", "H200", used)
        assert info.used_vram_known is False
        assert info.used_vram_mb == 4096  # 只累計已知部分

    def test_mixed_profiles_memory_based_availability(self, monkeypatch) -> None:
        """混合 profile（實測 H200 情境）：used 只算 running，
        可用數 = min(空 VF, 剩餘 VRAM ÷ 最小可建 profile)。"""
        catalog = {
            "nvidia-1436": gpu_service._parse_mdev_entry(H200_MDEV),  # 4C, mi=32
            "nvidia-1440": gpu_service.MdevProfile("NVIDIA H200-17C", 17408, 8),
            "nvidia-1442": gpu_service.MdevProfile("NVIDIA H200-35C", 35840, 4),
            "nvidia-1443": gpu_service.MdevProfile("NVIDIA H200-70C", 71680, 2),
        }
        monkeypatch.setattr(gpu_service, "_get_mdev_types", lambda node, path: dict(catalog))
        maps = _h200_vf_maps()
        used = [
            GPUUsageInfo(vmid=377, mdev_type="nvidia-1440", status="running"),
            GPUUsageInfo(vmid=458, mdev_type="nvidia-1442", status="stopped"),
            GPUUsageInfo(vmid=459, mdev_type="nvidia-1443", status="stopped"),
            GPUUsageInfo(vmid=476, mdev_type="nvidia-1442", status="running"),
        ]
        info = gpu_service._resolve_vram_for_mapping(maps, 1, "", "H200", used)

        # 總量 = max(fb × mi) = 70GB×2 = 140GB
        assert info.total_vram_mb == 71680 * 2
        # used 只算 running：17408 + 35840
        assert info.used_vram_mb == 17408 + 35840
        assert info.used_vram_known is True
        # 混合 profile：不顯示單一規格
        assert info.mdev_profile == ""
        assert info.per_instance_vram_mb == 0
        # 可用 = min(32-4 空 VF, (143360-53248)//4096=22) = 22
        assert info.available_override == 22

    def test_catalog_remembers_hidden_profiles(self, monkeypatch) -> None:
        """累積目錄：大 profile 從 creatable 清單消失後仍算得出 used。"""
        monkeypatch.setattr(
            gpu_service,
            "_mdev_profile_catalog",
            {"nvidia-1443": gpu_service.MdevProfile("NVIDIA H200-70C", 71680, 2)},
        )
        monkeypatch.setattr(
            gpu_service,
            "_get_mdev_types",
            lambda node, path: {"nvidia-1436": gpu_service._parse_mdev_entry(H200_MDEV)},
        )
        maps = _h200_vf_maps()
        used = [GPUUsageInfo(vmid=459, mdev_type="nvidia-1443", status="running")]
        info = gpu_service._resolve_vram_for_mapping(maps, 1, "", "H200", used)
        assert info.used_vram_mb == 71680
        assert info.used_vram_known is True


class TestGpuNodeCounts:
    def test_sriov_counts_clamped_by_max_instances(self, monkeypatch) -> None:
        gpu_service._gpu_node_counts_cache.clear()
        big = {
            "type": "nvidia-9999",
            "name": "NVIDIA H200-35C",
            "description": "max-instances=4\nframebuffer-size=35840MiB",
        }
        monkeypatch.setattr(
            gpu_service,
            "_get_mdev_types",
            lambda node, path: {"nvidia-9999": gpu_service._parse_mdev_entry(big)},
        )

        # 實況：map entry 不帶 mdev 旗標
        entries = [
            f"id=10de:233b,node=pve205,path=0000:15:{i // 8:02d}.{i % 8}"
            for i in range(32)
        ]

        class _FakePci:
            def get(self):
                return [{"id": "H200", "map": entries}]

        class _FakeMapping:
            pci = _FakePci()

        class _FakeCluster:
            mapping = _FakeMapping()

        class _FakeProxmox:
            cluster = _FakeCluster()

        monkeypatch.setattr(
            gpu_service, "iter_connection_clients", lambda: [(None, _FakeProxmox())]
        )

        assert gpu_service.get_gpu_node_counts() == {"pve205": 4}
        gpu_service._gpu_node_counts_cache.clear()


class TestBuildGpuHostpci:
    """provisioning 的 GPU 掛載字串：vGPU 未指定規格時自動配最小可建者。"""

    @staticmethod
    def _detail(profiles, available=5):
        from app.schemas.gpu import GPUMappingDetail, GPUProfileOption

        return GPUMappingDetail(
            id="H200",
            available_count=available,
            capacity_count=32,
            used_count=32 - available,
            profiles=[GPUProfileOption(**p) for p in profiles],
        )

    def _patch(self, monkeypatch, detail):
        from app.services.proxmox import gpu_service

        monkeypatch.setattr(gpu_service, "get_gpu_mapping", lambda _mid: detail)

    def test_auto_picks_smallest_creatable(self, monkeypatch) -> None:
        from app.services.proxmox.provisioning_service import _build_gpu_hostpci

        detail = self._detail([
            {"mdev_type": "nvidia-1443", "name": "NVIDIA H200-70C", "vram_mb": 71680, "creatable": False},
            {"mdev_type": "nvidia-1437", "name": "NVIDIA H200-7C", "vram_mb": 7168, "creatable": True},
            {"mdev_type": "nvidia-1436", "name": "NVIDIA H200-4C", "vram_mb": 4096, "creatable": True},
        ])
        self._patch(monkeypatch, detail)
        assert _build_gpu_hostpci("H200", None) == "mapping=H200,mdev=nvidia-1436"

    def test_explicit_profile_kept(self, monkeypatch) -> None:
        from app.services.proxmox.provisioning_service import _build_gpu_hostpci

        detail = self._detail([
            {"mdev_type": "nvidia-1442", "name": "NVIDIA H200-35C", "vram_mb": 35840, "creatable": True},
        ])
        self._patch(monkeypatch, detail)
        assert _build_gpu_hostpci("H200", "nvidia-1442") == "mapping=H200,mdev=nvidia-1442"

    def test_uncreatable_profile_rejected(self, monkeypatch) -> None:
        import pytest

        from app.exceptions import ProxmoxError
        from app.services.proxmox.provisioning_service import _build_gpu_hostpci

        detail = self._detail([
            {"mdev_type": "nvidia-1443", "name": "NVIDIA H200-70C", "vram_mb": 71680, "creatable": False},
        ])
        self._patch(monkeypatch, detail)
        with pytest.raises(ProxmoxError, match="記憶體不足"):
            _build_gpu_hostpci("H200", "nvidia-1443")

    def test_vgpu_full_without_profile_rejected(self, monkeypatch) -> None:
        import pytest

        from app.exceptions import ProxmoxError
        from app.services.proxmox.provisioning_service import _build_gpu_hostpci

        detail = self._detail([
            {"mdev_type": "nvidia-1443", "name": "NVIDIA H200-70C", "vram_mb": 71680, "creatable": False},
        ])
        self._patch(monkeypatch, detail)
        with pytest.raises(ProxmoxError, match="記憶體已滿"):
            _build_gpu_hostpci("H200", None)

    def test_passthrough_without_profiles_unchanged(self, monkeypatch) -> None:
        from app.services.proxmox.provisioning_service import _build_gpu_hostpci

        self._patch(monkeypatch, self._detail([], available=1))
        assert _build_gpu_hostpci("TeslaM60", None) == "mapping=TeslaM60"


class TestListGpuOptionsNodeFilter:
    """list_gpu_options(node=...)：GPU 不可跨 PVE 連線，只回範本同叢集的 GPU。"""

    @staticmethod
    def _mapping(mapping_id: str, node: str):
        from app.schemas.gpu import GPUMappingDetail

        return GPUMappingDetail(
            id=mapping_id,
            description="",
            maps=[GPUDeviceMap(node=node, path="0000:15:00.0")],
        )

    def _patch(self, monkeypatch) -> None:
        node_to_conn = {"pve1": 1, "pve2": 1, "pve9": 2}
        conn_to_nodes = {1: {"pve1", "pve2"}, 2: {"pve9"}}
        monkeypatch.setattr(
            gpu_service,
            "list_gpu_mappings",
            lambda: [self._mapping("gpu-a", "pve2"), self._mapping("gpu-b", "pve9")],
        )
        monkeypatch.setattr(
            gpu_service, "get_connection_id_for_node", node_to_conn.get
        )
        monkeypatch.setattr(
            gpu_service,
            "get_nodes_for_connection",
            lambda cid: set(conn_to_nodes.get(cid, set())),
        )

    def test_filters_to_template_connection(self, monkeypatch) -> None:
        self._patch(monkeypatch)
        options = gpu_service.list_gpu_options(node="pve1")
        assert [o.mapping_id for o in options] == ["gpu-a"]

    def test_no_node_returns_all(self, monkeypatch) -> None:
        self._patch(monkeypatch)
        options = gpu_service.list_gpu_options()
        assert [o.mapping_id for o in options] == ["gpu-a", "gpu-b"]

    def test_unregistered_node_fails_open(self, monkeypatch) -> None:
        """節點沒登錄在 proxmox_nodes（舊環境）時不過濾，避免藏光所有 GPU。"""
        self._patch(monkeypatch)
        options = gpu_service.list_gpu_options(node="unknown-node")
        assert [o.mapping_id for o in options] == ["gpu-a", "gpu-b"]


class _FakeConfig:
    """模擬 proxmox.nodes(node).qemu(vmid).config.get()，可記錄併發數。"""

    def __init__(self, tracker, config):
        self._tracker = tracker
        self._config = config

    def get(self):
        self._tracker.enter()
        try:
            time.sleep(0.02)
            return self._config
        finally:
            self._tracker.leave()


class _Tracker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.inflight = 0
        self.max_inflight = 0
        self.calls = 0

    def enter(self) -> None:
        with self.lock:
            self.calls += 1
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)

    def leave(self) -> None:
        with self.lock:
            self.inflight -= 1


class _FakeProxmoxVms:
    def __init__(self, tracker, configs) -> None:
        self._tracker = tracker
        self._configs = configs

        outer = self

        class _Cluster:
            class resources:  # noqa: N801 - mimic proxmoxer attribute style
                @staticmethod
                def get(type=None):  # noqa: A002 - proxmoxer kwarg name
                    return [
                        {"type": "qemu", "vmid": vmid, "node": "pve1",
                         "name": f"vm{vmid}", "status": "running"}
                        for vmid in outer._configs
                    ]

        self.cluster = _Cluster()

    def nodes(self, _node):
        outer = self

        class _Node:
            @staticmethod
            def qemu(vmid):
                class _Qemu:
                    config = _FakeConfig(outer._tracker, outer._configs[vmid])

                return _Qemu()

        return _Node()


class TestBuildUsageMap:
    """掃描每台 VM 的設定是 GPU 管理頁最大的成本：必須併發且短期共用結果。"""

    @staticmethod
    def _patch(monkeypatch, vm_count: int):
        gpu_service._usage_map_cache = None
        tracker = _Tracker()
        configs = {
            1000 + i: {"hostpci0": "mapping=H200,mdev=nvidia-1436,pcie=1"}
            for i in range(vm_count)
        }
        proxmox = _FakeProxmoxVms(tracker, configs)
        monkeypatch.setattr(
            gpu_service, "iter_connection_clients", lambda: [(None, proxmox)]
        )
        return tracker

    def test_configs_are_fetched_concurrently(self, monkeypatch) -> None:
        tracker = self._patch(monkeypatch, 8)
        usage = gpu_service._build_usage_map()
        assert len(usage["H200"]) == 8
        assert tracker.calls == 8
        assert tracker.max_inflight > 1
        gpu_service._usage_map_cache = None

    def test_repeated_calls_reuse_cached_scan(self, monkeypatch) -> None:
        tracker = self._patch(monkeypatch, 4)
        gpu_service._build_usage_map()
        gpu_service._build_usage_map()
        assert tracker.calls == 4
        gpu_service._usage_map_cache = None

    def test_cached_entries_are_not_mutated_by_callers(self, monkeypatch) -> None:
        """_resolve_vram_for_mapping 會寫入 allocated_vram_mb，不可污染快取。"""
        self._patch(monkeypatch, 2)
        first = gpu_service._build_usage_map()
        for entry in first["H200"]:
            entry.allocated_vram_mb = 4096

        second = gpu_service._build_usage_map()
        assert all(entry.allocated_vram_mb == 0 for entry in second["H200"])
        gpu_service._usage_map_cache = None
