from __future__ import annotations

from typing import Any

from app.ai.template_recommendation.config import settings
from app.ai.template_recommendation.schemas import DeviceNode
from app.services.proxmox import provisioning_service, proxmox_service


def load_live_device_nodes() -> list[DeviceNode]:
    gpu_map = settings.parsed_backend_node_gpu_map
    nodes: list[DeviceNode] = []
    for item in proxmox_service.list_nodes():
        maxmem = int(item.get("maxmem") or 0)
        mem = int(item.get("mem") or 0)
        node_name = str(item.get("node") or "unknown")
        nodes.append(
            DeviceNode(
                node=node_name,
                maxcpu=int(item.get("maxcpu") or 0),
                cpu_usage_ratio=float(item.get("cpu") or 0.0),
                maxmem_gb=round(maxmem / (1024**3), 2) if maxmem else 0.0,
                mem_usage_ratio=(mem / maxmem) if maxmem else 0.0,
                gpu_count=gpu_map.get(node_name, 0),
            )
        )
    return nodes


def summarize_device_nodes(nodes: list[DeviceNode]) -> dict[str, Any]:
    total_cpu = 0
    total_memory_gb = 0.0
    total_gpu = 0
    summarized_nodes: list[dict[str, Any]] = []
    for node in nodes:
        free_cpu = max(int(round(node.maxcpu * (1 - node.cpu_usage_ratio))), 0)
        free_memory_gb = round(max(node.maxmem_gb * (1 - node.mem_usage_ratio), 0.0), 2)
        summarized_nodes.append(
            {
                "node": node.node,
                "free_cpu": free_cpu,
                "free_memory_gb": free_memory_gb,
                "gpu_count": node.gpu_count,
            }
        )
        total_cpu += free_cpu
        total_memory_gb += free_memory_gb
        total_gpu += node.gpu_count

    return {
        "nodes": summarized_nodes,
        "free_cpu": total_cpu,
        "free_memory_gb": round(total_memory_gb, 2),
        "gpu_count": total_gpu,
    }


def build_resource_option_bundle() -> dict[str, Any]:
    """LXC／VM 範本清單；GPU 選項依使用者權限另外填入，這裡一律給空清單。"""
    lxc_os_images = [
        {
            "value": template.volid,
            "label": template.volid.split("/")[-1].replace(".tar.zst", ""),
        }
        for template in provisioning_service.get_lxc_templates()
    ]
    vm_operating_systems = [
        {
            "template_id": template.vmid,
            "label": template.name,
            "node": template.node,
        }
        for template in provisioning_service.get_vm_templates()
    ]
    return {
        "lxc_os_images": lxc_os_images,
        "vm_operating_systems": vm_operating_systems,
        "gpu_options": [],
    }
