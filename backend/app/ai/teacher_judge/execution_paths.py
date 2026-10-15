"""Execution paths are student-machine data, never paths on the API host."""

from __future__ import annotations

import re
from typing import Any


def is_generated_location_gap(value: str) -> bool:
    """Recognize only our deterministic gap text, not arbitrary teacher intent."""
    return value in {
        "工作目錄的完整路徑（不可使用相對路徑或 ~；不需要工作目錄時可留空）",
        "工作目錄的完整路徑（不可使用空白、相對路徑或 ~）",
    } or re.fullmatch(
        r"(?:(?:程式／檔案|檔案)「[^」]+」的絕對路徑"
        r"|程式／檔案「[^」]+」的完整路徑，或所在工作目錄與相對路徑"
        r"|檔案「[^」]+」的完整路徑（或提供工作目錄與相對路徑）)", value,
    ) is not None


def absolute_execution_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    path = value.replace("\\", "/")
    return path.startswith("/") or re.match(r"^[A-Za-z]:/", path) is not None
