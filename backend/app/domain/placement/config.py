from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

BACKEND_ROOT = Path(__file__).resolve().parents[3]
CONFIG_FILE = BACKEND_ROOT / "config" / "placement.json"


class PlacementConfig(BaseModel):
    source_cache_ttl_seconds: int = 20
    guest_pressure_threshold: float = 0.85
    guest_per_core_limit: float = 2.0
    placement_headroom_ratio: float = 0.1


def load_placement_config() -> PlacementConfig:
    if not CONFIG_FILE.exists():
        return PlacementConfig()

    payload = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("placement.json must be a JSON object")

    return PlacementConfig.model_validate(payload)


# pydantic 已把各欄位轉成宣告的型別，直接當成設定物件使用
settings = load_placement_config()
