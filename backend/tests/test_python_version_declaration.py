"""宣告的最低 Python 版本要涵蓋程式實際用到的標準庫（``datetime.UTC`` 從 3.11 才有）。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_UTC_USAGE = re.compile(r"\bdatetime\.UTC\b|^from datetime import [^\n]*\bUTC\b", re.MULTILINE)
_REQUIRES_PYTHON = re.compile(r'^requires-python\s*=\s*"([^"]+)"', re.MULTILINE)


def _requires_python(path: Path) -> str:
    match = _REQUIRES_PYTHON.search(path.read_text(encoding="utf-8"))
    assert match, f"{path} 沒有 requires-python"
    return match.group(1).replace(" ", "")


def test_requires_python_covers_datetime_utc_usage() -> None:
    uses_utc = [
        path
        for path in (ROOT / "backend").rglob("*.py")
        if ".venv" not in path.parts and _UTC_USAGE.search(path.read_text(encoding="utf-8"))
    ]
    lower = re.search(r">=3\.(\d+)", _requires_python(ROOT / "backend/pyproject.toml"))

    assert uses_utc, "找不到 datetime.UTC 的用法；若已全部移除可放寬 requires-python"
    assert lower is not None and int(lower.group(1)) >= 11


def test_lockfile_was_resolved_for_the_declared_python() -> None:
    assert _requires_python(ROOT / "uv.lock") == _requires_python(
        ROOT / "backend/pyproject.toml"
    )
