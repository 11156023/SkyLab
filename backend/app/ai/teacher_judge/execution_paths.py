"""Execution paths are student-machine data, never paths on the API host."""

from __future__ import annotations

import posixpath
import re
from typing import Any

CWD_LOCATION_GAP = "工作目錄的完整路徑（不可使用相對路徑或 ~；不需要工作目錄時可留空）"


def optional_cwd(value: Any) -> Any:
    """An empty optional field is absent; preserve nonempty literal paths."""
    # Model tool calls sometimes quote JSON null. These cannot be valid absolute
    # directories; a relative file still needs a real location after this step.
    return None if isinstance(value, str) and value.strip().casefold() in {"", "null", "none"} else value


def is_generated_location_gap(value: str) -> bool:
    """Recognize only our deterministic gap text, not arbitrary teacher intent."""
    return value in {
        CWD_LOCATION_GAP,
        "工作目錄的完整路徑（不可使用空白、相對路徑或 ~）",
    } or re.fullmatch(
        r"(?:程式／檔案「[^」]+」的完整路徑，或所在工作目錄與相對路徑"
        r"|檔案「[^」]+」的完整路徑（或提供工作目錄與相對路徑）)", value,
    ) is not None


def absolute_execution_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    path = value.replace("\\", "/")
    return path.startswith("/") or re.match(r"^[A-Za-z]:/", path) is not None


def resolved_file_path(collector: dict[str, Any]) -> str:
    """Join a validated file target without host I/O or shell expansion."""
    path = collector["path"]
    if absolute_execution_path(path):
        return path
    return posixpath.join(collector["cwd"].replace("\\", "/"), path.replace("\\", "/"))
