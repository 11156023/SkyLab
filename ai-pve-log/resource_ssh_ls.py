"""List directories on a VM/LXC via SkyLab backend API + SSH key login.

Usage example:
  python resource_ssh_ls.py --vmid 101 --ssh-user ubuntu --path /home/ubuntu

Environment variable fallbacks:
  skylab_API_BASE      (default: http://localhost:8000/api/v1)
  skylab_API_USER
  skylab_API_PASSWORD
"""

from __future__ import annotations

import argparse
import getpass
import io
import shlex
import socket
import sys
from dataclasses import dataclass
from typing import Any

import paramiko
import requests


DEFAULT_API_BASE = "http://localhost:8000/api/v1"


class ScriptError(RuntimeError):
    """Domain error for clear script failures."""


@dataclass(slots=True)
class ScriptConfig:
    api_base: str
    api_user: str
    api_password: str
    vmid: int
    ssh_user: str
    ssh_port: int
    path: str
    command: str | None
    timeout: int
    insecure_host_key: bool


def parse_args() -> ScriptConfig:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch SSH key from SkyLab API and list a directory on a VM/LXC."
        )
    )
    parser.add_argument(
        "--api-base",
        default=None,
        help="Backend API base URL, e.g. http://localhost:8000/api/v1",
    )
    parser.add_argument("--api-user", default=None, help="SkyLab login email")
    parser.add_argument(
        "--api-password",
        default=None,
        help="SkyLab login password (omit to prompt securely)",
    )
    parser.add_argument("--vmid", type=int, required=True, help="Target VM/LXC VMID")
    parser.add_argument(
        "--ssh-user",
        required=True,
        help="Linux account name on the target machine",
    )
    parser.add_argument("--ssh-port", type=int, default=22, help="SSH port")
    parser.add_argument(
        "--path",
        default="/",
        help="Directory path to list on remote machine",
    )
    parser.add_argument(
        "--command",
        default=None,
        help="Custom remote command to execute (overrides --path listing mode)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP and SSH timeout seconds",
    )
    parser.add_argument(
        "--insecure-host-key",
        action="store_true",
        help=(
            "Disable strict host key check (AutoAddPolicy). "
            "Use only in trusted internal environments."
        ),
    )

    args = parser.parse_args()

    api_base = args.api_base or _get_env("skylab_API_BASE") or DEFAULT_API_BASE
    api_user = args.api_user or _get_env("skylab_API_USER")
    api_password = args.api_password or _get_env("skylab_API_PASSWORD")

    if not api_user:
        api_user = input("API user email: ").strip()
    if not api_user:
        raise ScriptError("API user is required.")

    if not api_password:
        api_password = getpass.getpass("API password: ")
    if not api_password:
        raise ScriptError("API password is required.")

    return ScriptConfig(
        api_base=api_base.rstrip("/"),
        api_user=api_user,
        api_password=api_password,
        vmid=args.vmid,
        ssh_user=args.ssh_user,
        ssh_port=args.ssh_port,
        path=args.path,
        command=args.command,
        timeout=args.timeout,
        insecure_host_key=args.insecure_host_key,
    )


def _get_env(name: str) -> str | None:
    import os

    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def login_access_token(cfg: ScriptConfig) -> str:
    url = f"{cfg.api_base}/login/access-token"
    resp = requests.post(
        url,
        data={"username": cfg.api_user, "password": cfg.api_password},
        timeout=cfg.timeout,
    )
    _raise_http(resp, "Login failed")
    data = resp.json()
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise ScriptError("Login succeeded but access_token is missing.")
    return token


def get_resource(token: str, cfg: ScriptConfig) -> dict[str, Any]:
    url = f"{cfg.api_base}/resources/{cfg.vmid}"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=cfg.timeout,
    )
    _raise_http(resp, "Failed to get resource details")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise ScriptError("Resource API returned unexpected data type.")
    return payload


def get_ssh_key(token: str, cfg: ScriptConfig) -> dict[str, Any]:
    url = f"{cfg.api_base}/resources/{cfg.vmid}/ssh-key"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=cfg.timeout,
    )
    if not resp.ok:
        detail = _extract_http_detail(resp)
        if (
            resp.status_code in {404, 502}
            and "Resource not found in database" in detail
        ):
            raise ScriptError(
                "Failed to get SSH key: VMID "
                f"{cfg.vmid} is not registered in backend resources table. "
                "This VM/LXC may exist on Proxmox but has no stored SSH key in SkyLab DB."
            )
    _raise_http(resp, "Failed to get SSH key")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise ScriptError("SSH key API returned unexpected data type.")
    return payload


def _extract_http_detail(resp: requests.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])
        return str(body)
    except Exception:
        return resp.text[:300] if resp.text else ""


def _raise_http(resp: requests.Response, prefix: str) -> None:
    if resp.ok:
        return
    detail_text = _extract_http_detail(resp)
    detail = f": {detail_text}" if detail_text else ""
    raise ScriptError(f"{prefix} (HTTP {resp.status_code}){detail}")


def ssh_list_directory(
    *,
    host: str,
    port: int,
    username: str,
    private_key_pem: str,
    path: str,
    command: str | None,
    timeout: int,
    insecure_host_key: bool,
) -> tuple[int, str, str]:
    pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key_pem))
    client = paramiko.SSHClient()

    if insecure_host_key:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    client.connect(
        hostname=host,
        port=port,
        username=username,
        pkey=pkey,
        timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )

    remote_command = command or f"ls -al {shlex.quote(path)}"
    try:
        _, stdout, stderr = client.exec_command(remote_command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out_text = stdout.read().decode(errors="replace")
        err_text = stderr.read().decode(errors="replace")
        return exit_code, out_text, err_text
    finally:
        client.close()


def main() -> int:
    cfg: ScriptConfig | None = None
    host_for_error = "unknown"
    try:
        cfg = parse_args()

        token = login_access_token(cfg)
        resource = get_resource(token, cfg)
        ssh_key_data = get_ssh_key(token, cfg)

        host = resource.get("ip_address")
        vm_type = str(resource.get("type", "unknown"))
        if not isinstance(host, str) or not host:
            raise ScriptError(
                "Resource has no ip_address. Ensure the VM/LXC is running and has a reachable IP."
            )
        host_for_error = host

        private_key = ssh_key_data.get("ssh_private_key")
        if not isinstance(private_key, str) or not private_key.strip():
            raise ScriptError(
                "SSH private key is missing for this VMID."
            )

        code, stdout_text, stderr_text = ssh_list_directory(
            host=host,
            port=cfg.ssh_port,
            username=cfg.ssh_user,
            private_key_pem=private_key,
            path=cfg.path,
            command=cfg.command,
            timeout=cfg.timeout,
            insecure_host_key=cfg.insecure_host_key,
        )

        print(f"Resource VMID: {cfg.vmid}")
        print(f"Resource Type: {vm_type}")
        print(f"Resource Host: {host}")
        print(f"SSH User: {cfg.ssh_user}")
        if cfg.command:
            print(f"Remote Command: {cfg.command}")
        else:
            print(f"Directory Path: {cfg.path}")
        print(f"Command Exit Code: {code}")
        print("--- STDOUT ---")
        print(stdout_text.rstrip())
        if stderr_text.strip():
            print("--- STDERR ---")
            print(stderr_text.rstrip())

        return code
    except (socket.timeout, TimeoutError, OSError) as exc:
        user_text = cfg.ssh_user if cfg else "<unknown-user>"
        port_text = cfg.ssh_port if cfg else "<unknown-port>"
        print(
            f"ERROR: SSH connection failed to {host_for_error}:{port_text} "
            f"as {user_text}: {exc}",
            file=sys.stderr,
        )
        return 1
    except (requests.RequestException, paramiko.SSHException, ScriptError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
