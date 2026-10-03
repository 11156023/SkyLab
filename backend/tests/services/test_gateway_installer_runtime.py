from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _bash() -> str:
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.exists():
            return str(candidate)
        pytest.skip("Git Bash required on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash required")
    return bash


@pytest.mark.parametrize("initially_active", [True, False])
def test_ufw_reinstall_removes_legacy_duplicates_and_preserves_other_rules(
    initially_active: bool,
) -> None:
    installer = (Path(__file__).resolve().parents[3] / "gateway/install.sh").read_text(
        encoding="utf-8"
    )
    block = installer.split("# BEGIN managed WireGuard UFW reconciliation\n")[1].split(
        "# END managed WireGuard UFW reconciliation"
    )[0]
    mock = r'''set -eu
WG_INTERFACE=wg0
WG_VM_INTERFACE=ens20
WG_INGRESS_INTERFACE=ens19
WG_LISTEN_PORT=51821
WG_CLIENT_SUBNET=10.250.0.0/16
WG_VM_SUBNET=192.168.60.0/24
WG_UFW_FORWARD_COMMENT="SkyLab WireGuard routed traffic after nft ACL"
ufw_was_active=INITIAL_ACTIVE
ufw_active=$ufw_was_active
rules=(
    '22/tcp # SSH'
    'old-in # Campus Cloud WireGuard'
    'old-in-v6 # Campus Cloud WireGuard'
    'old-route # Campus Cloud WireGuard routed traffic after nft ACL'
    'duplicate # SkyLab WireGuard routed traffic after nft ACL'
    'custom # SkyLab WireGuard notes'
)
ufw() {
    case "$1" in
        status)
            if [[ "$ufw_active" == false ]]; then echo 'Status: inactive'; return; fi
            for ((i=0;i<${#rules[@]};i++)); do
                printf '[%2d] %s\n' "$((i+1))" "${rules[i]}"
            done ;;
        --force)
            if [[ "$2" == enable ]]; then ufw_active=true; return; fi
            unset 'rules[$3-1]'
            rules=("${rules[@]}") ;;
        allow)
            rules+=('new-in # SkyLab WireGuard' 'new-in-v6 # SkyLab WireGuard') ;;
        route)
            rules+=('new-route # SkyLab WireGuard routed traffic after nft ACL') ;;
        *) return 1 ;;
    esac
}
'''.replace("INITIAL_ACTIVE", "true" if initially_active else "false")
    result = subprocess.run(
        [_bash(), "-c", mock + block + block + '\nprintf "%s\\n" "${rules[@]}"'],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "22/tcp # SSH", "custom # SkyLab WireGuard notes",
        "new-in # SkyLab WireGuard", "new-in-v6 # SkyLab WireGuard",
        "new-route # SkyLab WireGuard routed traffic after nft ACL",
    ]


@pytest.mark.parametrize("modern", [True, False])
def test_backend_selects_new_or_legacy_gateway_policy(modern):
    from app.services.network.wireguard_service import _gateway_runtime_script

    script = (
        f"systemctl() {{ return {0 if modern else 3}; }}\n"
        + _gateway_runtime_script()
        + 'printf "%s %s" "$SKYLAB_WG_TABLE" "$SKYLAB_WG_FIREWALL_UNIT"'
    )
    result = subprocess.run(
        [_bash(), "-eu", "-c", script], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "skylab_wg skylab-wg-firewall.service" if modern
        else "campus_cloud_wg campus-cloud-wg-firewall.service"
    )


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
    start = installer.index("systemctl enable skylab-wg-firewall.service")
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
    unit = "skylab-wg-firewall.service"
    assert result.stdout.splitlines() == [
        f"enable {unit}",
        f"is-active --quiet {unit}",
        f"{'reload' if active else 'start'} {unit}",
    ]
