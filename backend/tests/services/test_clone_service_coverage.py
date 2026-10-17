"""app/services/template/clone_service.py 剩餘分支的單元測試（in-memory SQLite，mock PVE／網路）。"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import NotFoundError
from app.models import TaskRecord, VMTemplate, VMTemplateStatus
from app.services.proxmox import provisioning_service
from app.services.template import clone_service as cs

TEMPLATE_VMID = 800
NEW_VMID = 4321
NET_CFG = {"prefix_len": 24, "gateway": "10.0.0.1", "dns_servers": "1.1.1.1"}


def _raise(exc: Exception):
    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise exc

    return _boom


@pytest.fixture()
def eng(monkeypatch: pytest.MonkeyPatch) -> Engine:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(cs, "engine", engine)
    monkeypatch.setattr(cs, "report_progress", lambda *a: None)
    return engine


def _template(engine: Engine, **overrides: Any) -> VMTemplate:
    values: dict[str, Any] = {
        "pve_vmid": TEMPLATE_VMID,
        "name": "ubuntu-lab",
        "node": "pve1",
        "resource_type": "qemu",
        "status": VMTemplateStatus.ready,
        "default_cores": 2,
        "default_memory": 2048,
        "default_disk": 20,
    }
    values.update(overrides)
    with Session(engine) as session:
        template = VMTemplate(**values)
        session.add(template)
        session.commit()
        session.refresh(template)
        return template


# ---------------------------------------------------------------------------
# clone_with_fallback／重配置／LXC 輔助
# ---------------------------------------------------------------------------


def test_clone_with_fallback_tolerates_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def clone_vm(node: str, vmid: int, *, full: int, **kw: Any) -> None:
        calls.append(full)
        if full == 0:
            raise RuntimeError("linked clone unsupported on this storage")

    monkeypatch.setattr(
        cs, "get_proxmox_settings_for_node", lambda node: SimpleNamespace(pool_name="p")
    )
    monkeypatch.setattr(cs.proxmox_ops, "clone_vm", clone_vm)
    monkeypatch.setattr(
        cs.proxmox_ops, "find_resource", lambda vmid: {"vmid": vmid, "template": 1}
    )
    monkeypatch.setattr(
        provisioning_service,
        "cleanup_provisioned_resource",
        _raise(RuntimeError("cleanup failed")),
    )
    mode = cs.clone_with_fallback(
        node="pve1",
        template_vmid=TEMPLATE_VMID,
        new_vmid=NEW_VMID,
        hostname="h",
        resource_type="qemu",
    )
    assert mode == "full"
    assert calls == [0, 1]


def test_reconfigure_qemu_sets_dns_and_grows_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[dict[str, Any]] = []
    grows: list[dict[str, Any]] = []
    monkeypatch.setattr(cs.nic_config, "qemu_net0", lambda cfg: "virtio,bridge=vmbr0")
    monkeypatch.setattr(
        cs.proxmox_ops,
        "update_config",
        lambda node, vmid, rtype, **kw: updates.append(kw),
    )
    monkeypatch.setattr(cs, "_grow_qemu_boot_disk", lambda **kw: grows.append(kw))
    cs._reconfigure_qemu(
        node="pve1",
        vmid=NEW_VMID,
        hostname="h",
        cores=None,
        memory=None,
        disk=40,
        public_key="ssh-ed25519 AAAA",
        login_password=None,
        net_cfg=NET_CFG,
        allocated_ip="10.0.0.5",
    )
    assert updates[0]["nameserver"] == "1.1.1.1"
    assert "cipassword" not in updates[0]
    assert "cores" not in updates[0]
    assert grows == [{"node": "pve1", "vmid": NEW_VMID, "disk_gb": 40}]


def test_grow_qemu_boot_disk_without_boot_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cs.proxmox_ops, "get_config", lambda *a: {"net0": "virtio"})
    monkeypatch.setattr(
        cs.proxmox_ops, "resize_disk", _raise(AssertionError("must not resize"))
    )
    cs._grow_qemu_boot_disk(node="pve1", vmid=NEW_VMID, disk_gb=40)


def test_reconfigure_lxc(monkeypatch: pytest.MonkeyPatch) -> None:
    updates: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        cs.nic_config, "lxc_net0", lambda cfg, ip: f"name=eth0,ip={ip}/24"
    )
    monkeypatch.setattr(
        cs.proxmox_ops,
        "update_config",
        lambda node, vmid, rtype, **kw: updates.append((rtype, kw)),
    )
    cs._reconfigure_lxc(
        node="pve1",
        vmid=NEW_VMID,
        hostname="ct",
        cores=2,
        memory=1024,
        net_cfg=NET_CFG,
        allocated_ip="10.0.0.6",
    )
    cs._reconfigure_lxc(
        node="pve1",
        vmid=NEW_VMID,
        hostname="ct",
        cores=None,
        memory=None,
        net_cfg={},
        allocated_ip="10.0.0.6",
    )
    assert updates[0] == (
        "lxc",
        {
            "hostname": "ct",
            "net0": "name=eth0,ip=10.0.0.6/24",
            "cores": 2,
            "memory": 1024,
            "nameserver": "1.1.1.1",
        },
    )
    assert updates[1] == (
        "lxc",
        {"hostname": "ct", "net0": "name=eth0,ip=10.0.0.6/24"},
    )


def test_inject_lxc_platform_key_blank_key() -> None:
    assert cs._inject_lxc_platform_key("pve1", NEW_VMID, "   ") is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("", None), ("2026-12-31", date(2026, 12, 31)), ("nope", None)],
)
def test_parse_expiry(raw: Any, expected: date | None) -> None:
    assert cs._parse_expiry(raw) == expected


# ---------------------------------------------------------------------------
# run_clone_task
# ---------------------------------------------------------------------------


@contextmanager
def _no_lock():
    yield


@pytest.fixture()
def stubs(eng: Engine, monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    calls: dict[str, list] = {
        "control": [],
        "update_config": [],
        "reconfigure_qemu": [],
        "reconfigure_lxc": [],
        "set_password": [],
        "inject_key": [],
        "schedule": [],
        "create_resource": [],
        "link_ip": [],
        "release_ip": [],
        "delete_rule": [],
        "cleanup": [],
        "scrub": [],
    }
    monkeypatch.setattr(cs.proxmox_ops, "vmid_allocation_lock", _no_lock)
    monkeypatch.setattr(
        provisioning_service, "allocate_free_vmid", lambda session: NEW_VMID
    )
    monkeypatch.setattr(
        provisioning_service,
        "_build_gpu_hostpci",
        lambda mapping, profile: f"mapping={mapping},mdev={profile}",
    )
    monkeypatch.setattr(
        provisioning_service,
        "cleanup_provisioned_resource",
        lambda vmid: calls["cleanup"].append(vmid),
    )
    ip = cs.ip_management_service
    monkeypatch.setattr(ip, "get_network_config_for_vm", lambda session: NET_CFG)
    monkeypatch.setattr(
        ip,
        "allocate_ip",
        lambda session, vmid, purpose, reservation_key=None: "10.0.0.9",
    )
    monkeypatch.setattr(
        ip,
        "link_ip_to_resource",
        lambda session, vmid, reservation_key=None: calls["link_ip"].append(vmid),
    )
    monkeypatch.setattr(
        ip,
        "release_ip",
        lambda session, vmid, **kw: calls["release_ip"].append((vmid, kw)),
    )
    monkeypatch.setattr(cs, "clone_with_fallback", lambda **kw: "linked")
    monkeypatch.setattr(
        cs, "generate_ed25519_keypair", lambda: ("PRIVATE-PEM", "ssh-ed25519 AAAA")
    )
    monkeypatch.setattr(cs, "encrypt_value", lambda value: f"enc:{value}")
    monkeypatch.setattr(cs, "decrypt_value", lambda value: value.removeprefix("enc:"))
    monkeypatch.setattr(cs, "hash_login_password", lambda value: f"$6$hash-{value}")
    monkeypatch.setattr(cs, "generate_login_password", lambda: "Random123")
    monkeypatch.setattr(
        cs, "_reconfigure_qemu", lambda **kw: calls["reconfigure_qemu"].append(kw)
    )
    monkeypatch.setattr(
        cs, "_reconfigure_lxc", lambda **kw: calls["reconfigure_lxc"].append(kw)
    )
    monkeypatch.setattr(
        cs.proxmox_ops,
        "update_config",
        lambda node, vmid, rtype, **kw: calls["update_config"].append(kw),
    )
    monkeypatch.setattr(
        cs.proxmox_ops,
        "control",
        lambda node, vmid, rtype, action: calls["control"].append((rtype, action)),
    )
    monkeypatch.setattr(
        cs.firewall_service, "setup_default_rules", lambda node, vmid, rtype: None
    )
    monkeypatch.setattr(
        cs.firewall_service,
        "get_vm_firewall_rules",
        lambda node, vmid, rtype: [],
    )
    monkeypatch.setattr(
        cs.firewall_service,
        "delete_rule_by_pos",
        lambda node, vmid, rtype, pos: calls["delete_rule"].append(pos),
    )
    monkeypatch.setattr(
        cs.guest_ssh_login,
        "schedule_after_start",
        lambda node, vmid, rtype: calls["schedule"].append(vmid),
    )

    def set_password(node: str, vmid: int, pw: str, *, hashed: bool = False) -> bool:
        calls["set_password"].append((pw, hashed))
        return True

    monkeypatch.setattr(cs, "_set_lxc_root_password", set_password)
    monkeypatch.setattr(
        cs,
        "_inject_lxc_platform_key",
        lambda node, vmid, key: calls["inject_key"].append(key) or True,
    )
    monkeypatch.setattr(cs, "get_connection_id_for_node", lambda node: 3)
    monkeypatch.setattr(
        cs.resource_repo,
        "create_resource",
        lambda **kw: calls["create_resource"].append(kw),
    )
    return calls


def _payload(template: VMTemplate, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "template_id": str(template.id),
        "user_id": str(uuid.uuid4()),
        "hostname": "lab-01",
    }
    payload.update(overrides)
    return payload


def test_run_clone_task_template_missing(eng: Engine) -> None:
    with pytest.raises(NotFoundError):
        cs.run_clone_task(
            uuid.uuid4(),
            {
                "template_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
                "hostname": "x",
            },
        )


def test_run_clone_task_qemu_custom_hash_with_gpu(
    eng: Engine, stubs: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(eng)
    scrubbed: list[uuid.UUID] = []
    monkeypatch.setattr(cs, "_scrub_task_password", scrubbed.append)
    batch_id = uuid.uuid4()
    task_id = uuid.uuid4()

    result = cs.run_clone_task(
        task_id,
        _payload(
            template,
            login_password_hash="$6$abc$def",
            gpu_mapping_id="gpu-a",
            gpu_mdev_profile="nvidia-1",
            batch_job_id=str(batch_id),
            environment_type="課程",
            expiry_date="2026-12-31",
        ),
    )

    assert result == {
        "vmid": NEW_VMID,
        "clone_mode": "linked",
        "ip": "10.0.0.9",
        "hostname": "lab-01",
        "login_password_set": True,
    }
    reconfigure = stubs["reconfigure_qemu"][0]
    assert reconfigure["login_password"] == "$6$abc$def"
    assert (reconfigure["cores"], reconfigure["memory"], reconfigure["disk"]) == (
        2,
        2048,
        20,
    )
    assert stubs["update_config"] == [{"hostpci0": "mapping=gpu-a,mdev=nvidia-1"}]
    assert stubs["control"] == [("qemu", "start")]
    assert stubs["schedule"] == [NEW_VMID]
    created = stubs["create_resource"][0]
    assert created["login_password_hash"] == "$6$abc$def"
    assert created["login_password_encrypted"] is None
    assert created["login_password_pending_hash"] is None
    assert created["batch_job_id"] == batch_id
    assert created["environment_type"] == "課程"
    assert created["expiry_date"] == date(2026, 12, 31)
    assert created["connection_id"] == 3
    assert created["ssh_private_key_encrypted"] == "enc:PRIVATE-PEM"
    assert stubs["link_ip"] == [NEW_VMID]
    assert scrubbed == [task_id]


def test_run_clone_task_qemu_windows_plaintext_not_started(
    eng: Engine, stubs: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(eng)
    gpu_without_profile: list[Any] = []
    monkeypatch.setattr(
        provisioning_service,
        "_build_gpu_hostpci",
        lambda mapping, profile: gpu_without_profile.append(profile) or "m",
    )
    monkeypatch.setattr(cs, "_scrub_task_password", lambda task_id: None)

    result = cs.run_clone_task(
        uuid.uuid4(),
        _payload(
            template,
            login_password_enc="enc:Win-Pass1",
            start=False,
            cores=4,
            gpu_mapping_id="gpu-a",
        ),
    )

    assert result["login_password_set"] is True
    assert stubs["reconfigure_qemu"][0]["login_password"] == "Win-Pass1"
    assert stubs["reconfigure_qemu"][0]["cores"] == 4
    assert gpu_without_profile == [None]
    assert stubs["control"] == []
    created = stubs["create_resource"][0]
    assert created["login_password_hash"] == "$6$hash-Win-Pass1"
    assert created["environment_type"] == "範本 ubuntu-lab"


def test_run_clone_task_lxc_generated_password(
    eng: Engine, stubs: dict[str, list]
) -> None:
    template = _template(eng, resource_type="lxc")

    result = cs.run_clone_task(uuid.uuid4(), _payload(template))

    assert result["login_password_set"] is True
    assert stubs["reconfigure_lxc"][0]["hostname"] == "lab-01"
    assert stubs["control"] == [("lxc", "start")]
    assert stubs["set_password"] == [("Random123", False)]
    assert stubs["inject_key"] == ["ssh-ed25519 AAAA"]
    created = stubs["create_resource"][0]
    assert created["login_password_encrypted"] == "enc:Random123"
    assert created["login_password_pending_encrypted"] is None


def test_run_clone_task_lxc_not_started_keeps_password_pending(
    eng: Engine, stubs: dict[str, list]
) -> None:
    template = _template(eng, resource_type="lxc")

    result = cs.run_clone_task(uuid.uuid4(), _payload(template, start=False))

    assert result["login_password_set"] is False
    assert stubs["control"] == []
    assert stubs["set_password"] == []
    created = stubs["create_resource"][0]
    assert created["login_password_encrypted"] is None
    assert created["login_password_pending_encrypted"] == "enc:Random123"


def test_run_clone_task_rolls_back_after_partial_setup(
    eng: Engine, stubs: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(eng)

    def setup_default_rules(node: str, vmid: int, rtype: str) -> None:
        raise RuntimeError("firewall api down")

    def delete_rule(node: str, vmid: int, rtype: str, pos: int) -> None:
        stubs["delete_rule"].append(pos)
        if pos == 2:
            raise RuntimeError("rule locked")

    monkeypatch.setattr(cs.firewall_service, "setup_default_rules", setup_default_rules)
    monkeypatch.setattr(
        cs.firewall_service,
        "get_vm_firewall_rules",
        lambda node, vmid, rtype: [{"pos": 1}, {"pos": 2}, {"comment": "no pos"}],
    )
    monkeypatch.setattr(cs.firewall_service, "delete_rule_by_pos", delete_rule)

    with pytest.raises(RuntimeError, match="firewall api down"):
        cs.run_clone_task(
            uuid.uuid4(), _payload(template, ip_reservation_key="class:1")
        )

    assert stubs["release_ip"] == [
        (NEW_VMID, {"restore_reservation": True, "reservation_key": "class:1"})
    ]
    assert stubs["delete_rule"] == [2, 1]
    assert stubs["cleanup"] == [NEW_VMID]
    assert stubs["create_resource"] == []


def test_run_clone_task_rollback_failures_are_logged(
    eng: Engine, stubs: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    template = _template(eng)
    monkeypatch.setattr(
        cs.firewall_service,
        "setup_default_rules",
        _raise(RuntimeError("firewall api down")),
    )
    monkeypatch.setattr(
        cs.ip_management_service, "release_ip", _raise(RuntimeError("db gone"))
    )
    monkeypatch.setattr(
        cs.firewall_service,
        "get_vm_firewall_rules",
        _raise(RuntimeError("list failed")),
    )
    monkeypatch.setattr(
        provisioning_service,
        "cleanup_provisioned_resource",
        _raise(RuntimeError("pve gone")),
    )

    with pytest.raises(RuntimeError, match="firewall api down"):
        cs.run_clone_task(uuid.uuid4(), _payload(template))


# ---------------------------------------------------------------------------
# _scrub_task_password
# ---------------------------------------------------------------------------


def test_scrub_task_password_clears_secrets(eng: Engine) -> None:
    with Session(eng) as session:
        record = TaskRecord(
            task_type=cs.TASK_CLONE,
            user_id=uuid.uuid4(),
            payload={
                "hostname": "lab-01",
                "login_password_enc": "enc:x",
                "login_password_hash": "$6$y",
            },
        )
        session.add(record)
        session.commit()
        task_id = record.id

    cs._scrub_task_password(task_id)
    cs._scrub_task_password(uuid.uuid4())  # 紀錄不存在直接略過

    with Session(eng) as session:
        payload = session.get(TaskRecord, task_id).payload
    assert payload == {
        "hostname": "lab-01",
        "login_password_enc": None,
        "login_password_hash": None,
    }


def test_scrub_task_password_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(cs, "Session", _raise(RuntimeError("db down")))
    cs._scrub_task_password(uuid.uuid4())
    assert "Failed to scrub login password" in caplog.text
