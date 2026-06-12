from __future__ import annotations

from app.ai.system_config import system_ai_config


class PlacementSettings:
    @property
    def section(self):
        # Keep the old pve_advisor section as a compatibility source for
        # placement tuning until deployment config grows a dedicated section.
        return system_ai_config.pve_advisor

    @property
    def source_cache_ttl_seconds(self) -> int:
        return int(self.section.source_cache_ttl_seconds)

    @property
    def parsed_backend_node_gpu_map(self) -> dict[str, int]:
        parsed: dict[str, int] = {}
        for key, value in self.section.backend_node_gpu_map.items():
            try:
                parsed[str(key)] = max(int(value), 0)
            except (TypeError, ValueError):
                continue
        return parsed

    @property
    def guest_pressure_threshold(self) -> float:
        return float(self.section.guest_pressure_threshold)

    @property
    def guest_per_core_limit(self) -> float:
        return float(self.section.guest_per_core_limit)

    @property
    def placement_headroom_ratio(self) -> float:
        return float(self.section.placement_headroom_ratio)

    @property
    def placement_weight_cpu(self) -> float:
        return float(self.section.placement_weight_cpu)

    @property
    def placement_weight_memory(self) -> float:
        return float(self.section.placement_weight_memory)

    @property
    def placement_weight_disk(self) -> float:
        return float(self.section.placement_weight_disk)

    @property
    def placement_weight_guest(self) -> float:
        return float(self.section.placement_weight_guest)


settings = PlacementSettings()
