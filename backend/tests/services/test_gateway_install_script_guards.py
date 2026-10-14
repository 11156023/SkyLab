"""gateway/install.sh 的防呆：只支援 Debian 13、exporter 失敗只警告、pipefail 下的 ufw 判斷。

直接執行安裝腳本裡的真實片段，搭配假的 ufw／systemctl／apt-get，不碰主機。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[3] / "gateway/install.sh"

_HELPERS = r"""set -euo pipefail
info()  { echo "INFO: $*"; }
warn()  { echo "WARN: $*"; }
error() { echo "ERROR: $*"; exit 1; }
"""


def _bash() -> str:
    if os.name == "nt":
        candidate = (
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        )
        if candidate.exists():
            return str(candidate)
        pytest.skip("Git Bash required on Windows")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash required")
    return bash


def _installer() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def _between(start: str, end: str) -> str:
    text = _installer()
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_bash(), "-c", _HELPERS + script],
        capture_output=True,
        text=True,
        check=False,
    )


# ── 只支援 Debian 13 ─────────────────────────────────────────────────────────

_DEBIAN_13 = 'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nID=debian\nVERSION_ID="13"\nVERSION_CODENAME=trixie\n'
_DEBIAN_TESTING_TRIXIE = 'PRETTY_NAME="Debian GNU/Linux trixie/sid"\nID=debian\nVERSION_CODENAME=trixie\n'
_DEBIAN_12 = 'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\nVERSION_ID="12"\nVERSION_CODENAME=bookworm\n'
_DEBIAN_TESTING_FORKY = 'PRETTY_NAME="Debian GNU/Linux forky/sid"\nID=debian\nVERSION_CODENAME=forky\n'
_UBUNTU = 'PRETTY_NAME="Ubuntu 24.04.1 LTS"\nID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\n'


def _run_os_check(tmp_path: Path, os_release: str | None) -> subprocess.CompletedProcess[str]:
    release = tmp_path / "os-release"
    if os_release is not None:
        release.write_text(os_release, encoding="utf-8")
    block = _between("# BEGIN supported OS check", "# END supported OS check")
    assert block.count("/etc/os-release") >= 2
    block = block.replace("/etc/os-release", str(release))
    # os-release 的 NAME／ID 不可蓋掉安裝腳本自己的變數
    return _run('ID=keep\nNAME=keep\n' + block + '\necho "PASS $ID $NAME"\n')


@pytest.mark.parametrize("os_release", [_DEBIAN_13, _DEBIAN_TESTING_TRIXIE])
def test_os_check_accepts_debian_13(tmp_path: Path, os_release: str) -> None:
    result = _run_os_check(tmp_path, os_release)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PASS keep keep"


@pytest.mark.parametrize(
    ("os_release", "detected"),
    [
        (_DEBIAN_12, "Debian GNU/Linux 12 (bookworm)"),
        (_DEBIAN_TESTING_FORKY, "Debian GNU/Linux forky/sid"),
        (_UBUNTU, "Ubuntu 24.04.1 LTS"),
        (None, "未知系統"),
    ],
)
def test_os_check_rejects_everything_else(
    tmp_path: Path, os_release: str | None, detected: str
) -> None:
    result = _run_os_check(tmp_path, os_release)

    assert result.returncode == 1
    assert "PASS" not in result.stdout
    assert "只支援 Debian 13" in result.stdout
    assert f"偵測到：{detected}" in result.stdout


def test_os_check_runs_before_any_change() -> None:
    text = _installer()
    check = text.index("# BEGIN supported OS check")

    assert check < text.index("apt-get update")
    assert check < text.index('install -d -m 700 "$backup"')
    assert "Debian 12 / 13" not in text


# ── exporter 失敗只警告 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("failing", ["restart", "is-active"])
def test_exporter_start_failure_only_warns(tmp_path: Path, failing: str) -> None:
    defaults = tmp_path / "prometheus-nginx-exporter"
    block = _between("EXPORTERS_READY=false", "# 3. WireGuard")
    assert "/etc/default/prometheus-nginx-exporter" in block
    block = block.replace("/etc/default/prometheus-nginx-exporter", str(defaults))
    mocks = f"""NGINX_STATUS_PORT=9180
NODE_EXPORTER_PORT=9100
NGINX_EXPORTER_PORT=9113
apt-get() {{ return 0; }}
systemctl() {{
    case "$1" in
        {failing}) return 1 ;;
        *) return 0 ;;
    esac
}}
"""

    result = _run(mocks + block + '\necho "READY=$EXPORTERS_READY"\n')

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARN: exporter 啟動失敗" in result.stdout
    assert result.stdout.strip().endswith("READY=false")
    assert "9180/stub_status" in defaults.read_text(encoding="utf-8")


def test_exporter_ready_when_services_start(tmp_path: Path) -> None:
    block = _between("EXPORTERS_READY=false", "# 3. WireGuard").replace(
        "/etc/default/prometheus-nginx-exporter", str(tmp_path / "exporter")
    )
    mocks = """NGINX_STATUS_PORT=9180
NODE_EXPORTER_PORT=9100
NGINX_EXPORTER_PORT=9113
apt-get() { return 0; }
systemctl() { return 0; }
"""

    result = _run(mocks + block + '\necho "READY=$EXPORTERS_READY"\n')

    assert result.returncode == 0, result.stderr
    assert "WARN" not in result.stdout
    assert result.stdout.strip().endswith("READY=true")


# ── pipefail 下判斷 ufw status ───────────────────────────────────────────────

# 第一行就比對到、後面還有大量輸出：舊寫法 `ufw status | grep -q` 會讓 ufw 吃到
# SIGPIPE，pipefail 把整條管線當失敗，於是誤判成「UFW 尚未啟用」。
_UFW_MOCK = r"""
calls=()
ufw() {
    if [[ "$1" == status ]]; then
        printf '%s\n' "$UFW_FIRST_LINE"
        for ((i = 0; i < 20000; i++)); do
            printf '%s\n' "10.0.0.$((i % 250))/32 ALLOW IN Anywhere # padding $i"
        done
        return 0
    fi
    calls+=("$*")
}
"""


def test_active_ufw_detected_despite_long_status_output() -> None:
    block = _between("ufw_was_active=false", "# BEGIN managed WireGuard UFW reconciliation")
    script = (
        'UFW_FIRST_LINE="Status: active"\n'
        + _UFW_MOCK
        + block
        + '\necho "ACTIVE=$ufw_was_active CALLS=${#calls[@]}"\n'
    )

    result = _run(script)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("ACTIVE=true CALLS=0")


def test_inactive_ufw_gets_bootstrap_rules() -> None:
    block = _between("ufw_was_active=false", "# BEGIN managed WireGuard UFW reconciliation")
    script = (
        'UFW_FIRST_LINE="Status: inactive"\n'
        + "sshd() { echo 'port 2222'; }\n"
        + _UFW_MOCK
        + block
        + '\necho "ACTIVE=$ufw_was_active"; printf "CALL %s\\n" "${calls[@]}"\n'
    )

    result = _run(script)

    assert result.returncode == 0, result.stderr
    assert "ACTIVE=false" in result.stdout
    assert "CALL allow 2222/tcp comment SSH" in result.stdout
    assert "CALL default deny incoming" in result.stdout


def test_forward_range_rule_detected_despite_long_status_output() -> None:
    block = _between(
        "# Port 轉發配號池", "# 監控 exporter：只開給 Prometheus 所在主機"
    )
    script = (
        "FORWARD_PORT_RANGE=30000:39999\n"
        'UFW_FIRST_LINE="30000:39999/tcp ALLOW IN Anywhere # SkyLab port forwarding"\n'
        + _UFW_MOCK
        + block
        + '\nprintf "CALL %s\\n" "${calls[@]}"\n'
    )

    result = _run(script)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "CALL allow 30000:39999/udp comment SkyLab port forwarding"
    ]
