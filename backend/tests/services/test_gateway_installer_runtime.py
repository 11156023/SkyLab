from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("active", [True, False])
def test_installer_applies_firewall_policy_for_existing_and_new_services(
    active: bool,
) -> None:
    bash = shutil.which("bash")
    if os.name == "nt":
        # Windows' system32/bash.exe may be a WSL launcher without a distro.
        candidate = (
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "Git/bin/bash.exe"
        )
        bash = str(candidate) if candidate.exists() else None
    if bash is None:
        pytest.skip("A working bash is required")

    installer = (Path(__file__).resolve().parents[3] / "gateway/install.sh").read_text(
        encoding="utf-8"
    )
    start = installer.index("systemctl enable campus-cloud-wg-firewall.service")
    end = installer.index('systemctl enable --now "wg-quick@', start)
    # Execute the real service activation block with a fake systemctl; no host
    # services are touched. Existing policy must reload; fresh installs start.
    script = """set -eu
systemctl() {
    printf '%s\\n' "$*"
    if [ "$1" = is-active ]; then return ACTIVE_STATUS; fi
}
""".replace("ACTIVE_STATUS", "0" if active else "3")
    result = subprocess.run(
        [bash, "-c", script + installer[start:end]],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    unit = "campus-cloud-wg-firewall.service"
    assert result.stdout.splitlines() == [
        f"enable {unit}",
        f"is-active --quiet {unit}",
        f"{'reload' if active else 'start'} {unit}",
    ]
