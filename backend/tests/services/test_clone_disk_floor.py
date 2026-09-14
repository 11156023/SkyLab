"""克隆機的磁碟下限：申請值不得低於來源範本，LXC 克隆也要真的放大 rootfs。"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import CourseEnvironment, CourseEnvironmentNode, VMTemplate
from app.schemas import VMRequestCreate
from app.services import quick_practice
from app.services.proxmox import provisioning_service


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _node(**overrides) -> CourseEnvironmentNode:
    values = {
        "version_id": uuid.uuid4(),
        "node_key": "web",
        "source_type": "custom",
        "custom_image_ref": "9000",
        "name": "Web",
        "role": "操作主機",
        "resource_type": "qemu",
        "cpu": 2,
        "memory_mb": 4096,
        "disk_gb": 20,
        "sort_order": 0,
    }
    values.update(overrides)
    return CourseEnvironmentNode(**values)


# --------------------------------------------------------------------------
# _node_disk_gb：配額與申請單都要用「實際會開出來的大小」
# --------------------------------------------------------------------------


def test_registered_template_raises_disk_to_template_size(db: Session) -> None:
    template = VMTemplate(
        name="ubuntu-22",
        pve_vmid=9000,
        node="pve1",
        resource_type="qemu",
        default_disk=40,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    node = _node(source_type="template", source_template_id=template.id, disk_gb=20)

    assert quick_practice._node_disk_gb(db, node) == 40


def test_request_larger_than_template_is_kept(db: Session) -> None:
    template = VMTemplate(
        name="debian-12",
        pve_vmid=9001,
        node="pve1",
        resource_type="lxc",
        default_disk=8,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    node = _node(
        source_type="template",
        source_template_id=template.id,
        resource_type="lxc",
        disk_gb=20,
    )

    assert quick_practice._node_disk_gb(db, node) == 20


def test_custom_vm_falls_back_to_pve_template_size(monkeypatch) -> None:
    monkeypatch.setattr(
        provisioning_service, "template_disk_floor_gb", lambda template_id: 60
    )

    assert quick_practice._node_disk_gb(Mock(), _node(disk_gb=20)) == 60


def test_custom_lxc_image_has_no_template_floor() -> None:
    node = _node(
        resource_type="lxc",
        custom_image_ref="local:vztmpl/debian.tar.zst",
        disk_gb=8,
    )

    assert quick_practice._node_disk_gb(Mock(), node) == 8


def test_machine_request_carries_the_floored_disk(db: Session, monkeypatch) -> None:
    monkeypatch.setattr(
        provisioning_service, "template_disk_floor_gb", lambda template_id: 60
    )
    now = datetime.now(UTC)
    environment = CourseEnvironment(
        owner_id=uuid.uuid4(), name="練習", usage_scope="quick_practice"
    )

    request = quick_practice._machine_request(
        session=db,
        node=_node(disk_gb=20),
        environment=environment,
        practice_session_id=uuid.uuid4(),
        now=now,
        expires_at=now + timedelta(hours=3),
    )

    assert request.disk_size == 60


# --------------------------------------------------------------------------
# LXC 克隆的 rootfs：只放大，下限不明時不動
# --------------------------------------------------------------------------


def test_lxc_rootfs_resize_grows_beyond_template(monkeypatch) -> None:
    resize = Mock()
    monkeypatch.setattr(
        provisioning_service, "proxmox_service", SimpleNamespace(resize_disk=resize)
    )

    provisioning_service._resize_clone_rootfs_if_needed("pve1", 101, 20, 8)

    resize.assert_called_once_with("pve1", 101, "lxc", "rootfs", "20G")


def test_lxc_rootfs_resize_skipped_when_not_larger(monkeypatch) -> None:
    resize = Mock()
    monkeypatch.setattr(
        provisioning_service, "proxmox_service", SimpleNamespace(resize_disk=resize)
    )

    provisioning_service._resize_clone_rootfs_if_needed("pve1", 101, 8, 8)

    resize.assert_not_called()


def test_lxc_rootfs_resize_reads_clone_size_when_template_unknown(monkeypatch) -> None:
    resize = Mock()
    monkeypatch.setattr(
        provisioning_service,
        "proxmox_service",
        SimpleNamespace(
            resize_disk=resize,
            get_config=lambda *_args, **_kwargs: {
                "rootfs": "local-lvm:vm-101-disk-0,size=16G"
            },
        ),
    )

    provisioning_service._resize_clone_rootfs_if_needed("pve1", 101, 12, 0)
    resize.assert_not_called()

    provisioning_service._resize_clone_rootfs_if_needed("pve1", 101, 32, 0)
    resize.assert_called_once_with("pve1", 101, "lxc", "rootfs", "32G")


def test_lxc_rootfs_resize_skipped_when_size_undetectable(monkeypatch) -> None:
    resize = Mock()
    monkeypatch.setattr(
        provisioning_service,
        "proxmox_service",
        SimpleNamespace(
            resize_disk=resize,
            get_config=lambda *_args, **_kwargs: {"rootfs": "local-lvm:vm-101-disk-0"},
        ),
    )

    provisioning_service._resize_clone_rootfs_if_needed("pve1", 101, 32, 0)

    resize.assert_not_called()


# --------------------------------------------------------------------------
# VMRequestCreate：規格欄位原本完全沒有上下界
# --------------------------------------------------------------------------


def _request_payload(**overrides) -> dict:
    payload = {
        "reason": "課程需要一台測試用的實驗機器",
        "resource_type": "vm",
        "hostname": "lab01",
        "password": "correct-horse",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "field,value",
    [
        ("cores", 0),
        ("cores", -4),
        ("cores", 128),
        ("memory", 64),
        ("memory", 262144),
        ("disk_size", 0),
        ("rootfs_size", 0),
        ("disk_size", 4000),
    ],
)
def test_vm_request_rejects_out_of_range_specs(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        VMRequestCreate(**_request_payload(**{field: value}))


def test_vm_request_accepts_the_course_environment_range() -> None:
    request = VMRequestCreate(
        **_request_payload(cores=64, memory=131072, disk_size=2000)
    )

    assert (request.cores, request.memory, request.disk_size) == (64, 131072, 2000)
