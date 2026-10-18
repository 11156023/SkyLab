"""補齊 app/services/proxmox/gpu_service.py 的分支覆蓋（PVE API 一律以假物件替換）。"""

import pytest

from app.exceptions import NotFoundError, ProxmoxError
from app.schemas.gpu import GPUDeviceMap, GPUUsageInfo
from app.services import proxmox as proxmox_pkg
from app.services.proxmox import gpu_service
from app.services.proxmox.gpu_service import MdevProfile


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    monkeypatch.setattr(gpu_service, "_mdev_profile_catalog", {})
    monkeypatch.setattr(gpu_service, "_mdev_types_cache", {})
    monkeypatch.setattr(gpu_service, "_gpu_node_counts_cache", {})
    monkeypatch.setattr(gpu_service._usage_map_cache, "entry", None)


def _clients(*keys):
    return lambda: iter([(k, f"client-{k}") for k in keys])


# ---------------------------------------------------------------------------
# package __getattr__
# ---------------------------------------------------------------------------


def test_package_getattr_unknown_name_raises() -> None:
    with pytest.raises(AttributeError):
        proxmox_pkg.__getattr__("does_not_exist")
    assert proxmox_pkg.__getattr__("gpu_service") is gpu_service


# ---------------------------------------------------------------------------
# _get_managed_vmids
# ---------------------------------------------------------------------------


class _FakeExec:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, _engine):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def exec(self, _stmt):
        return _FakeExec([101, None, "202"])


def test_get_managed_vmids_success(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "Session", _FakeSession)
    assert gpu_service._get_managed_vmids() == {101, 202}


def test_get_managed_vmids_db_error_returns_empty(monkeypatch) -> None:
    class _Boom:
        def __init__(self, _engine):
            raise RuntimeError("db down")

    monkeypatch.setattr(gpu_service, "Session", _Boom)
    assert gpu_service._get_managed_vmids() == set()


# ---------------------------------------------------------------------------
# 純解析函式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "vram", "vram_mb"),
    [
        ("Tesla 512MB", "512 MB", 512),
        ("Card 8192MB", "8 GB", 8192),
        ("Card 1536MiB", "1.5 GB", 1536),
        ("RTX 24GB", "24 GB", 24 * 1024),
        ("no memory", "", 0),
    ],
)
def test_extract_gpu_info_units(text, vram, vram_mb) -> None:
    model, got_vram, got_mb = gpu_service._extract_gpu_info(text, "id")
    assert model == text
    assert got_vram == vram
    assert got_mb == vram_mb


def test_count_physical_gpus_skips_blank_paths_and_empty_list() -> None:
    assert gpu_service._count_physical_gpus([]) == (0, False)
    maps = [
        GPUDeviceMap(node="n", path="  "),
        GPUDeviceMap(node="n", path="0000:15:00.1"),
        GPUDeviceMap(node="n", path="0000:15:00.2"),
    ]
    assert gpu_service._count_physical_gpus(maps) == (1, True)
    only_blank = [GPUDeviceMap(node="n", path="")]
    assert gpu_service._count_physical_gpus(only_blank) == (1, False)


def test_parse_vram_from_profile_text_gb_fallback() -> None:
    assert gpu_service._parse_vram_from_profile_text("card with 8 GB") == 8 * 1024
    assert gpu_service._parse_vram_from_profile_text("nothing") == 0


def test_parse_mdev_entry_bad_max_instances() -> None:
    profile = gpu_service._parse_mdev_entry(
        {"name": "X-2Q", "description": "max-instances=lots\nframebuffer-size=2048"}
    )
    assert profile.max_instances == 0
    assert profile.vram_mb == 2048


# ---------------------------------------------------------------------------
# mdev 探測
# ---------------------------------------------------------------------------


def test_fetch_mdev_types_success_updates_catalog(monkeypatch) -> None:
    monkeypatch.setattr(
        gpu_service,
        "list_pci_mdev_types",
        lambda node, path: [
            {"type": "nvidia-1", "name": "A-4C", "description": "max-instances=8"},
            {"type": "", "name": "ignored"},
        ],
    )
    result = gpu_service._fetch_mdev_types("pve1", "0000:01:00.0")
    assert set(result) == {"nvidia-1"}
    assert result["nvidia-1"].vram_mb == 4096
    assert "nvidia-1" in gpu_service._mdev_profile_catalog


def test_prefetch_mdev_types_parallel_and_single(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(
        gpu_service, "_get_mdev_types", lambda node, path: seen.append((node, path))
    )
    # 單一目標直接返回，不送任何請求
    gpu_service._prefetch_mdev_types([[GPUDeviceMap(node="n", path="p1")], []])
    assert seen == []
    gpu_service._prefetch_mdev_types(
        [
            [GPUDeviceMap(node="n", path="p1")],
            [GPUDeviceMap(node="n", path="p2")],
            [GPUDeviceMap(node="", path="p3")],
        ]
    )
    assert sorted(seen) == [("n", "p1"), ("n", "p2")]


# ---------------------------------------------------------------------------
# _resolve_vram_for_mapping 剩餘分支
# ---------------------------------------------------------------------------


def _maps(count=4):
    return [GPUDeviceMap(node="pve1", path=f"0000:15:00.{i}") for i in range(count)]


def test_resolve_single_used_type_without_vram_drops_display(monkeypatch) -> None:
    monkeypatch.setattr(
        gpu_service, "_get_mdev_types", lambda n, p: {"x": MdevProfile("X", 0, 0)}
    )
    used = [GPUUsageInfo(vmid=1, mdev_type="x", status="running")]
    info = gpu_service._resolve_vram_for_mapping(_maps(), 1, "", "gpu", used)
    assert info.mdev_profile == ""
    assert info.used_vram_known is False
    assert info.total_vram_mb == 0
    assert info.instance_capacity == 0


def test_resolve_display_profile_without_max_instances(monkeypatch) -> None:
    monkeypatch.setattr(
        gpu_service, "_get_mdev_types", lambda n, p: {"x": MdevProfile("X", 2048, 0)}
    )
    info = gpu_service._resolve_vram_for_mapping(_maps(), 2, "", "gpu", [])
    assert info.total_vram_mb == 4096
    assert info.per_instance_vram_mb == 2048
    assert info.instance_capacity == 0


def test_resolve_mdev_flag_uses_description_vram(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "_get_mdev_types", lambda n, p: None)
    maps = [GPUDeviceMap(node="pve1", path="0000:15:00.0", is_mdev=True)]
    info = gpu_service._resolve_vram_for_mapping(maps, 1, "A10 24GB", "gpu", [])
    assert info.has_mdev is True
    assert info.total_vram_mb == 24 * 1024
    assert info.instance_capacity == 0


# ---------------------------------------------------------------------------
# list_gpu_mappings / _build_mapping_detail
# ---------------------------------------------------------------------------


def test_list_gpu_mappings_no_reachable_connection(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "iter_connection_clients", _clients("a"))

    def boom(proxmox):
        raise RuntimeError("down")

    monkeypatch.setattr(gpu_service, "list_pci_mappings", boom)
    with pytest.raises(ProxmoxError):
        gpu_service.list_gpu_mappings()


def test_list_gpu_mappings_builds_details(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "iter_connection_clients", _clients("a", "b"))

    def list_maps(proxmox):
        if proxmox == "client-b":
            raise RuntimeError("down")
        return [
            {
                "id": "mixed",
                "description": "H200",
                "map": [f"node=pve1,path=0000:15:00.{i}" for i in range(8)],
            },
            {
                "id": "pass",
                "description": "RTX 24GB",
                "map": ["node=pve2,path=0000:01:00.0", "node=pve2,path=0000:02:00.0"],
            },
        ]

    small = MdevProfile("H200-4C", 4096, 32)
    big = MdevProfile("H200-8C", 8192, 16)

    def mdev_types(node, path):
        return {"s": small, "b": big} if node == "pve1" else {}

    monkeypatch.setattr(gpu_service, "list_pci_mappings", list_maps)
    monkeypatch.setattr(gpu_service, "_get_mdev_types", mdev_types)
    monkeypatch.setattr(
        gpu_service,
        "_build_usage_map",
        lambda: {
            "mixed": [
                GPUUsageInfo(vmid=1, mdev_type="s", status="running"),
                GPUUsageInfo(vmid=2, mdev_type="b", status="running"),
            ],
            "pass": [GPUUsageInfo(vmid=3, status="running")],
        },
    )
    monkeypatch.setattr(gpu_service, "_get_managed_vmids", lambda: {1, 3})

    details = {d.id: d for d in gpu_service.list_gpu_mappings()}

    mixed = details["mixed"]
    assert mixed.has_mdev is True
    # 混合 profile：available 由剩餘 VRAM ÷ 最小 profile 推得，且受空 VF 數限制
    assert mixed.available_count == 6
    assert mixed.capacity_count == 8
    assert [u.vmid for u in mixed.used_by] == [1]

    passthrough = details["pass"]
    assert passthrough.has_mdev is False
    assert passthrough.capacity_count == 2
    assert passthrough.available_count == 1
    assert passthrough.total_vram_mb == 2 * 24 * 1024


# ---------------------------------------------------------------------------
# get_gpu_node_counts / get_gpu_used_slots_by_node
# ---------------------------------------------------------------------------


def test_get_gpu_node_counts_skips_failures_and_blank_nodes(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "iter_connection_clients", _clients("a", "b"))

    def list_maps(proxmox):
        if proxmox == "client-a":
            raise RuntimeError("down")
        return [
            {
                "id": "g",
                "map": [
                    "node=pve1,path=0000:01:00.0",
                    "path=0000:02:00.0",
                ],
            }
        ]

    monkeypatch.setattr(gpu_service, "list_pci_mappings", list_maps)
    monkeypatch.setattr(gpu_service, "_get_mdev_types", lambda n, p: None)
    assert gpu_service.get_gpu_node_counts() == {"pve1": 1}


def test_get_gpu_used_slots_by_node(monkeypatch) -> None:
    monkeypatch.setattr(
        gpu_service,
        "_build_usage_map",
        lambda: {
            "g1": [
                GPUUsageInfo(vmid=1, node="pve1"),
                GPUUsageInfo(vmid=2, node="pve1"),
                GPUUsageInfo(vmid=3, node=""),
            ],
            "g2": [GPUUsageInfo(vmid=4, node="pve2")],
        },
    )
    assert gpu_service.get_gpu_used_slots_by_node() == {"pve1": 2, "pve2": 1}
    assert gpu_service.get_gpu_used_slots_by_node("g2") == {"pve2": 1}


def test_get_gpu_used_slots_by_node_scan_failure(monkeypatch) -> None:
    def boom():
        raise RuntimeError("scan failed")

    monkeypatch.setattr(gpu_service, "_build_usage_map", boom)
    assert gpu_service.get_gpu_used_slots_by_node() == {}


# ---------------------------------------------------------------------------
# get_gpu_mapping
# ---------------------------------------------------------------------------


def test_get_gpu_mapping_not_found_anywhere(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "iter_connection_clients", _clients("a"))

    def boom(proxmox, mid):
        raise RuntimeError("404")

    monkeypatch.setattr(gpu_service, "get_pci_mapping", boom)
    with pytest.raises(NotFoundError):
        gpu_service.get_gpu_mapping("gpu0")


def test_get_gpu_mapping_found_on_second_connection(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "iter_connection_clients", _clients("a", "b"))

    def get_mapping(proxmox, mid):
        if proxmox == "client-a":
            raise RuntimeError("404")
        return {
            "id": mid,
            "description": "RTX 12GB",
            "map": ["node=pve1,path=0000:01:00.0"],
        }

    monkeypatch.setattr(gpu_service, "get_pci_mapping", get_mapping)
    monkeypatch.setattr(gpu_service, "_get_mdev_types", lambda n, p: {})
    monkeypatch.setattr(
        gpu_service,
        "_build_usage_map",
        lambda: {"gpu0": [GPUUsageInfo(vmid=7, node="pve1")]},
    )
    monkeypatch.setattr(gpu_service, "_get_managed_vmids", lambda: {7})

    detail = gpu_service.get_gpu_mapping("gpu0")
    assert detail.id == "gpu0"
    assert detail.used_count == 1
    assert detail.available_count == 0
    assert [u.vmid for u in detail.used_by] == [7]


# ---------------------------------------------------------------------------
# create / delete
# ---------------------------------------------------------------------------


def test_create_gpu_mapping_without_node_uses_first_connection(monkeypatch) -> None:
    created = {}
    monkeypatch.setattr(gpu_service, "iter_connection_clients", _clients("a", "b"))
    monkeypatch.setattr(
        gpu_service,
        "create_pci_mapping",
        lambda proxmox, **kw: created.update(proxmox=proxmox),
    )
    gpu_service.create_gpu_mapping(mapping_id="gpu0", map_entries=["path=0000:01:00.0"])
    assert created["proxmox"] == "client-a"


def test_create_gpu_mapping_failure_wrapped(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "get_proxmox_api_for_node", lambda node: "api")

    def boom(proxmox, **kw):
        raise RuntimeError("pve says no")

    monkeypatch.setattr(gpu_service, "create_pci_mapping", boom)
    with pytest.raises(ProxmoxError, match="pve says no"):
        gpu_service.create_gpu_mapping(
            mapping_id="gpu0", map_entries=["node=pve1,path=0000:01:00.0"]
        )


def test_delete_gpu_mapping_invalid_id() -> None:
    with pytest.raises(NotFoundError):
        gpu_service.delete_gpu_mapping("bad/id")


def test_delete_gpu_mapping_success_and_failure(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "iter_connection_clients", _clients("a", "b"))

    def get_mapping(proxmox, mid):
        if proxmox == "client-a":
            raise RuntimeError("404")
        return {"id": mid}

    deleted = []
    monkeypatch.setattr(gpu_service, "get_pci_mapping", get_mapping)
    monkeypatch.setattr(
        gpu_service, "delete_pci_mapping", lambda proxmox, mid: deleted.append(proxmox)
    )
    gpu_service.delete_gpu_mapping("gpu0")
    assert deleted == ["client-b"]

    def boom(proxmox, mid):
        raise RuntimeError("locked")

    monkeypatch.setattr(gpu_service, "delete_pci_mapping", boom)
    with pytest.raises(ProxmoxError, match="locked"):
        gpu_service.delete_gpu_mapping("gpu0")


# ---------------------------------------------------------------------------
# _scan_usage_map / _build_usage_map
# ---------------------------------------------------------------------------


def test_scan_usage_map_no_resources(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "iter_connection_clients", _clients("a"))

    def boom(proxmox):
        raise RuntimeError("down")

    monkeypatch.setattr(gpu_service, "list_cluster_vm_resources", boom)
    assert gpu_service._scan_usage_map() == {}


def test_scan_usage_map_no_qemu_targets(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "iter_connection_clients", _clients("a"))
    monkeypatch.setattr(
        gpu_service,
        "list_cluster_vm_resources",
        lambda proxmox: [
            {"type": "lxc", "vmid": 1, "node": "pve1"},
            {"type": "qemu", "vmid": 2, "node": ""},
            {"type": "qemu", "vmid": None, "node": "pve1"},
        ],
    )
    assert gpu_service._scan_usage_map() == {}


def test_scan_usage_map_collects_hostpci(monkeypatch) -> None:
    monkeypatch.setattr(gpu_service, "iter_connection_clients", _clients("a"))
    monkeypatch.setattr(
        gpu_service,
        "list_cluster_vm_resources",
        lambda proxmox: [
            {
                "type": "qemu",
                "vmid": 100,
                "node": "pve1",
                "name": "vm100",
                "status": "running",
            },
            {"type": "qemu", "vmid": 101, "node": "pve1"},
            {"type": "qemu", "vmid": 102, "node": "pve1"},
        ],
    )

    def config(proxmox, node, vmid):
        if vmid == 101:
            raise RuntimeError("config unavailable")
        if vmid == 102:
            return {}
        return {
            "hostpci0": "mapping=gpu0,mdev=nvidia-1436,pcie=1",
            "hostpci1": "0000:01:00.0",
            "hostpci2": "mapping=gpu1",
        }

    monkeypatch.setattr(gpu_service, "get_qemu_config_via", config)
    usage = gpu_service._scan_usage_map()
    assert set(usage) == {"gpu0", "gpu1"}
    assert usage["gpu0"][0].mdev_type == "nvidia-1436"
    assert usage["gpu0"][0].vm_name == "vm100"
    assert usage["gpu1"][0].mdev_type == ""

    # _build_usage_map 走快取並回傳複本
    monkeypatch.setattr(gpu_service, "_scan_usage_map", lambda: usage)
    first = gpu_service._build_usage_map()
    first["gpu0"][0].allocated_vram_mb = 999
    second = gpu_service._build_usage_map()
    assert second["gpu0"][0].allocated_vram_mb == 0
