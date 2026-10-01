"""Proxmox 備份操作：送給 PVE 的 API 路徑與參數。"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.infrastructure.proxmox import operations


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


@pytest.fixture()
def pve(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    api = MagicMock()
    waits: list[tuple[str, str, float | None]] = []
    invalidated: list[bool] = []
    monkeypatch.setattr(operations, "get_proxmox_api_for_node", lambda node: api)
    monkeypatch.setattr(
        operations,
        "basic_blocking_task_status",
        lambda node, task, timeout_seconds=None, **kw: waits.append(
            (node, task, timeout_seconds)
        ),
    )
    monkeypatch.setattr(
        operations, "invalidate_cluster_resources_cache", lambda: invalidated.append(True)
    )
    return {"api": api, "waits": waits, "invalidated": invalidated}


def test_list_backups_filters_by_vmid_on_the_storage(pve: dict[str, Any]) -> None:
    content = pve["api"].nodes.return_value.storage.return_value.content
    content.get.return_value = [{"volid": "s:backup/x", "vmid": 101}]

    assert operations.list_backups("pve1", "pbs-main", 101) == [
        {"volid": "s:backup/x", "vmid": 101}
    ]
    pve["api"].nodes.assert_called_with("pve1")
    pve["api"].nodes.return_value.storage.assert_called_with("pbs-main")
    content.get.assert_called_once_with(content="backup", vmid=101)


def test_list_backups_tolerates_empty_response(pve: dict[str, Any]) -> None:
    pve["api"].nodes.return_value.storage.return_value.content.get.return_value = None
    assert operations.list_backups("pve1", "pbs-main", 101) == []


def test_create_backup_is_protected_and_does_not_prune(pve: dict[str, Any]) -> None:
    """備份 storage 常與機構排程共用：不保護會被 prune 清掉，remove=1 會刪到別的備份。"""
    vzdump = pve["api"].nodes.return_value.vzdump
    vzdump.post.return_value = "UPID:backup"

    task = operations.create_backup(
        "pve1", 101, "pbs-main", mode="stop", notes="skylab-backup:1", wait_timeout_seconds=60
    )

    assert task == "UPID:backup"
    vzdump.post.assert_called_once_with(
        **{
            "vmid": "101",
            "storage": "pbs-main",
            "mode": "stop",
            "compress": "zstd",
            "protected": 1,
            "remove": 0,
            "notes-template": "skylab-backup:1",
        }
    )
    assert pve["waits"] == [("pve1", "UPID:backup", 60)]


def test_restore_vm_overwrites_in_place_without_storage_override(pve: dict[str, Any]) -> None:
    qemu = pve["api"].nodes.return_value.qemu
    qemu.post.return_value = "UPID:restore"

    operations.restore_backup("pve1", 101, "qemu", "s:backup/vzdump-qemu-101.vma.zst")

    qemu.post.assert_called_once_with(
        vmid=101, archive="s:backup/vzdump-qemu-101.vma.zst", force=1
    )
    pve["api"].nodes.return_value.lxc.post.assert_not_called()
    assert pve["invalidated"] == [True]


def test_restore_lxc_passes_target_storage(pve: dict[str, Any]) -> None:
    lxc = pve["api"].nodes.return_value.lxc
    lxc.post.return_value = "UPID:restore"

    operations.restore_backup(
        "pve1",
        101,
        "lxc",
        "s:backup/ct/101/x",
        storage="lvm-data",
        unprivileged=True,
        wait_timeout_seconds=30,
    )

    lxc.post.assert_called_once_with(
        vmid=101,
        ostemplate="s:backup/ct/101/x",
        restore=1,
        force=1,
        storage="lvm-data",
        unprivileged=1,
    )
    assert pve["waits"] == [("pve1", "UPID:restore", 30)]


def test_restore_lxc_keeps_privileged_container_privileged(pve: dict[str, Any]) -> None:
    lxc = pve["api"].nodes.return_value.lxc

    operations.restore_backup("pve1", 101, "lxc", "s:backup/ct/101/x", unprivileged=False)

    lxc.post.assert_called_once_with(
        vmid=101, ostemplate="s:backup/ct/101/x", restore=1, force=1, unprivileged=0
    )


def test_restore_invalidates_cluster_cache_even_when_it_fails(pve: dict[str, Any]) -> None:
    pve["api"].nodes.return_value.qemu.post.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        operations.restore_backup("pve1", 101, "qemu", "s:backup/x")
    assert pve["invalidated"] == [True]


def test_delete_backup_clears_protection_then_deletes_and_waits(pve: dict[str, Any]) -> None:
    content = pve["api"].nodes.return_value.storage.return_value.content
    volume = content.return_value
    volume.delete.return_value = "UPID:imgdel"

    operations.delete_backup("pve1", "pbs-main", "pbs-main:backup/ct/101/x")

    content.assert_called_once_with("pbs-main:backup/ct/101/x")
    volume.put.assert_called_once_with(protected=0)
    volume.delete.assert_called_once_with()
    assert pve["waits"] == [("pve1", "UPID:imgdel", 300.0)]


def test_delete_backup_still_deletes_when_unprotect_fails(pve: dict[str, Any]) -> None:
    volume = pve["api"].nodes.return_value.storage.return_value.content.return_value
    volume.put.side_effect = RuntimeError("protected flag unsupported")
    volume.delete.return_value = None

    operations.delete_backup("pve1", "local", "local:backup/vzdump-lxc-101.tar.zst")

    volume.delete.assert_called_once_with()
    assert pve["waits"] == []


@pytest.mark.parametrize(
    ("storages", "expected"),
    [
        ([{"storage": "pbs-main", "content": "backup", "active": 1, "enabled": 1}], True),
        ([{"storage": "pbs-main", "content": "images,backup", "active": 1}], True),
        ([{"storage": "pbs-main", "content": "backup", "active": 0, "enabled": 1}], False),
        ([{"storage": "pbs-main", "content": "backup", "active": 1, "enabled": 0}], False),
        ([{"storage": "pbs-main", "content": "images,rootdir", "active": 1}], False),
        ([{"storage": "pbs-main", "active": 1}], False),
        ([{"storage": "other", "content": "backup", "active": 1}], False),
        ([], False),
    ],
)
def test_storage_accepts_backups(
    monkeypatch: pytest.MonkeyPatch, storages: list[dict], expected: bool
) -> None:
    monkeypatch.setattr(operations, "list_node_storages", lambda node: storages)
    assert operations.storage_accepts_backups("pve1", "pbs-main") is expected
