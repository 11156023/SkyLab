"""Server-side classification for the template test command confirmation gate."""

from __future__ import annotations

import re

_COMMON_READ_COMMANDS = {
    "cat /etc/os-release",
    "df -h",
    "free -m",
    "ps aux",
    "ss -lntp",
    "uname -a",
    "python3 --version",
    "python3 -V",
    "python3 -m pip list",
}
# 每個 pattern 都必須是「完整、固定形狀」的指令：不可有 .+ / .* / \S+ 這種
# 開放式尾巴，否則 `pg_isready ; curl … | sh` 也會被當成唯讀指令自動放行。
_N8N_READ_PATTERNS = (
    re.compile(r"^ss\s+-lntp\s*\|\s*grep\s+['\"]?:?5678['\"]?$"),
    re.compile(r"^curl\s+-I\s+--max-time\s+5\s+http://127\.0\.0\.1:5678$"),
    re.compile(r"^ps\s+aux\s+\|\s+grep\s+-i\s+n8n$"),
    re.compile(
        r"^docker\s+ps\s+--format\s+['\"]?\{\{\.Names\}\}(?:\s+\{\{\.Status\}\})?['\"]?"
        r"\s*\|\s*grep\s+-i\s+n8n$"
    ),
)
_PYTHON_READ_PATTERNS = (
    re.compile(r"^ps\s+aux\s+\|\s+grep\s+-E\s+['\"]python\|uvicorn\|flask\|django['\"]$"),
)
_POSTGRESQL_READ_PATTERNS = (
    re.compile(r"^(?:systemctl\s+status|service)\s+postgres(?:ql)?(?:@[A-Za-z0-9._-]+)?$"),
    re.compile(r"^pg_isready(?:\s+-h\s+(?:127\.0\.0\.1|localhost))?(?:\s+-p\s+\d{2,5})?$"),
    re.compile(r"^psql\s+--version$"),
)

# 任何 shell 控制字元都直接否決：白名單只描述單一指令，不描述指令串
_SHELL_CONTROL = re.compile(r"[;&`$<>]|\|\||\n|\r")


def is_known_read_command(template_key: str | None, command: str) -> bool:
    """Whether a command is a catalogue-like, read-only smoke command.

    This is only an auto-confirm convenience for the test harness.  The SSH
    guard still runs for every command, and all commands outside this small
    list remain pending for human approval.
    """
    if _SHELL_CONTROL.search(command):
        return False
    normalized = " ".join(command.strip().split())
    if normalized in _COMMON_READ_COMMANDS:
        return True
    if template_key == "n8n":
        return any(pattern.fullmatch(normalized) for pattern in _N8N_READ_PATTERNS)
    if template_key == "python":
        return any(pattern.fullmatch(normalized) for pattern in _PYTHON_READ_PATTERNS)
    if template_key == "postgresql":
        return any(
            pattern.fullmatch(normalized) for pattern in _POSTGRESQL_READ_PATTERNS
        )
    return False
