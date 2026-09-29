"""a TOFU save must not resurrect host keys removed by forget_host_key."""

from __future__ import annotations

import io

import paramiko
import pytest

from app.infrastructure.ssh import client as ssh_client_module
from app.infrastructure.ssh import forget_host_key, generate_ed25519_keypair


def _public_key() -> paramiko.PKey:
    pem, _ = generate_ed25519_keypair()
    return paramiko.Ed25519Key.from_private_key(io.StringIO(pem))


def test_tofu_save_keeps_concurrently_forgotten_host_removed(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "known_hosts"
    monkeypatch.setenv("SSH_KNOWN_HOSTS_FILE", str(path))

    old_key = _public_key()
    seeded = paramiko.HostKeys()
    seeded.add("10.10.0.5", old_key.get_name(), old_key)
    seeded.save(str(path))

    # A connection starts and loads the file (which still has 10.10.0.5) ...
    client = paramiko.SSHClient()
    ssh_client_module._configure_host_key_verification(client)
    assert client.get_host_keys().lookup("10.10.0.5") is not None

    # ... meanwhile the VM is deleted and its IP released.
    forget_host_key("10.10.0.5")

    # The in-flight client then meets a first-contact host and saves it.
    new_key = _public_key()
    policy = client._policy
    policy.missing_host_key(client, "10.10.0.9", new_key)

    on_disk = paramiko.HostKeys(str(path))
    assert on_disk.lookup("10.10.0.9") is not None
    assert on_disk.lookup("10.10.0.5") is None
    # The current connection still knows the new host.
    assert client.get_host_keys().lookup("10.10.0.9") is not None
