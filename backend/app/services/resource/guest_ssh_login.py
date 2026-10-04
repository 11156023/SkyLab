"""讓平台發的登入密碼能直接 SSH 登入 guest。

PVE 產生的 cloud-init user-data 只寫帳密與金鑰、不帶 ``ssh_pwauth``，
Ubuntu／Debian／RHEL 系 cloud image 的 sshd 預設 ``PasswordAuthentication no``；
Debian／Ubuntu 的 LXC 範本則是 ``PermitRootLogin prohibit-password``。
兩者都讓憑證卡片上的密碼只能從 VNC／終端機用，SSH 一律被拒。

處理方式是在 guest 內放一份 sshd drop-in（沒有 ``Include sshd_config.d``
的舊發行版改成插在主設定檔最前面——sshd 同一參數取第一次出現的值），
再 reload sshd：

- LXC：併進每次受管開機都會跑的平台公鑰同步（``pct exec``），見
  ``clone_service._inject_lxc_platform_key``。
- QEMU：開機後在背景等 guest agent 與 cloud-init 跑完再透過 agent 執行；
  Windows、未啟用 agent 的 VM 直接略過。RHEL 系 qemu-guest-agent 預設封鎖
  ``guest-exec``，這類 VM 只會記 warning。

只做一次：寫完留下標記檔，之後使用者在 guest 內改回 ``no`` 或刪掉
drop-in 都不會被平台蓋回去；快照回滾到更早的狀態時標記一起消失，下次開機再補。
"""

from __future__ import annotations

import logging
import shlex
import threading
import time

from app.infrastructure.proxmox import guest
from app.infrastructure.proxmox import operations as proxmox_ops

logger = logging.getLogger(__name__)

_MARKER = "/etc/ssh/.skylab-ssh-login"
_DROP_IN = "/etc/ssh/sshd_config.d/00-skylab-login.conf"
_SETTINGS = (
    "# Added by SkyLab so the login password works over SSH.",
    "# Edit or delete this file to change it; SkyLab will not rewrite it.",
    "PermitRootLogin yes",
    "PasswordAuthentication yes",
)

_RELOAD_SSHD = (
    "if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then "
    "systemctl try-reload-or-restart ssh.service sshd.service >/dev/null 2>&1; "
    "elif command -v rc-service >/dev/null 2>&1; then "
    "rc-service sshd reload >/dev/null 2>&1; "
    "elif command -v service >/dev/null 2>&1; then "
    "service ssh reload >/dev/null 2>&1 || service sshd reload >/dev/null 2>&1; "
    "fi"
)

_QEMU_AGENT_WAIT_SECONDS = 600.0
_QEMU_POLL_SECONDS = 5.0
# 腳本內等 cloud-init 最多 300 秒，agent exec 的逾時要比它長
_QEMU_EXEC_TIMEOUT_SECONDS = 360.0
_CLOUD_INIT_WAIT = (
    "if command -v cloud-init >/dev/null 2>&1; then "
    "timeout 300 cloud-init status --wait >/dev/null 2>&1; "
    "fi; "
)

_pending: set[tuple[str, int]] = set()
_pending_lock = threading.Lock()


def ssh_login_script(*, root: str = "", reload: bool = True) -> str:
    """冪等的 sshd 設定腳本（POSIX sh，busybox 也能跑）；一律以 0 結束。

    ``root`` 只給測試把路徑換到暫存目錄，``reload=False`` 讓測試不碰主機的 sshd。
    """
    q = shlex.quote
    config = q(f"{root}/etc/ssh/sshd_config")
    tmp = q(f"{root}/etc/ssh/sshd_config.skylab")
    drop_in = q(f"{root}{_DROP_IN}")
    drop_in_dir = q(f"{root}/etc/ssh/sshd_config.d")
    marker = q(f"{root}{_MARKER}")
    lines = " ".join(q(line) for line in _SETTINGS)
    return (
        f"if [ -f {config} ] && [ ! -e {marker} ]; then "
        f"if grep -Eqi '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\\.d/' {config}; then "
        f"mkdir -p {drop_in_dir} && printf '%s\\n' {lines} > {drop_in}; "
        f"else "
        f"{{ printf '%s\\n' {lines}; cat {config}; }} > {tmp} && cat {tmp} > {config}; "
        f"rc=$?; rm -f {tmp}; [ $rc -eq 0 ]; "
        f"fi && touch {marker}"
        + (f" && {{ {_RELOAD_SSHD}; }}" if reload else "")
        + "; fi; true"
    )


def _agent_enabled(node: str, vmid: int) -> bool:
    raw = str(proxmox_ops.get_config(node, vmid, "qemu").get("agent") or "")
    first = raw.split(",", 1)[0].strip()
    return first in {"1", "enabled=1"}


def _wait_for_agent(node: str, vmid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if guest.ping_qemu_agent(node, vmid):
            return True
        time.sleep(_QEMU_POLL_SECONDS)
    return False


def enable_qemu_ssh_login(node: str, vmid: int) -> bool:
    """等 VM 的 guest agent 起來後套用 sshd 設定；回傳是否有成功執行。"""
    try:
        if not _agent_enabled(node, vmid):
            logger.info("VM %s has no guest agent; SSH login setup skipped", vmid)
            return False
        if not _wait_for_agent(node, vmid, _QEMU_AGENT_WAIT_SECONDS):
            logger.warning(
                "Guest agent of VM %s did not respond within %ds; "
                "SSH login setup skipped",
                vmid,
                int(_QEMU_AGENT_WAIT_SECONDS),
            )
            return False
        osinfo = guest.get_osinfo_qemu(node, vmid) or {}
        if str(osinfo.get("id") or "").lower() == "mswindows":
            return False
        code, _out, err = guest.exec_qemu(
            node,
            vmid,
            ["/bin/sh", "-c", _CLOUD_INIT_WAIT + ssh_login_script()],
            timeout=_QEMU_EXEC_TIMEOUT_SECONDS,
        )
        if code != 0:
            logger.warning(
                "SSH login setup failed in VM %s (exit %s): %s",
                vmid,
                code,
                (err or "").strip()[:300],
            )
            return False
        return True
    except Exception as exc:
        logger.warning("SSH login setup skipped for VM %s: %s", vmid, exc)
        return False


def _run_qemu(node: str, vmid: int) -> None:
    try:
        enable_qemu_ssh_login(node, vmid)
    finally:
        with _pending_lock:
            _pending.discard((node, vmid))


def schedule_after_start(node: str, vmid: int, resource_type: str) -> None:
    """機器剛開機後呼叫；QEMU 在背景執行緒等開機完成再套用，不卡住呼叫端。

    LXC 不在這裡處理（已併進平台公鑰同步）。同一台已在等待中就不重複排。
    """
    if resource_type != "qemu" or not node:
        return
    key = (node, int(vmid))
    with _pending_lock:
        if key in _pending:
            return
        _pending.add(key)
    try:
        threading.Thread(
            target=_run_qemu,
            args=key,
            name=f"ssh-login-{vmid}",
            daemon=True,
        ).start()
    except Exception:
        with _pending_lock:
            _pending.discard(key)
        logger.warning("Could not schedule SSH login setup for VM %s", vmid, exc_info=True)
