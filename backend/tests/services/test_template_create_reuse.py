"""建立範本時對同 VMID 舊紀錄的處理（in-memory SQLite，mock PVE 與隊列）。

背景：範本刪除是軟刪除（status=deleted 保留紀錄），而 pve_vmid 有
unique 約束；PVE 會回收重用 VMID，因此軟刪除紀錄不能永久擋住同
VMID 重新註冊——應復用該筆紀錄重新開始生命週期。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import ConflictError, PermissionDeniedError
from app.models import Resource, VMTemplate, VMTemplateStatus, VMTemplateVisibility
from app.repositories import vm_template as template_repo
from app.schemas.template import VMTemplateCreate
from app.services.template import template_service


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def fake_pve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        template_service.proxmox_ops,
        "find_resource",
        lambda vmid: {"vmid": vmid, "node": "pve1", "type": "qemu", "template": 0},
    )

    async def fake_enqueue(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(id=uuid.uuid4(), payload=kwargs.get("payload"))

    monkeypatch.setattr(template_service, "enqueue_task", fake_enqueue)


def make_user(role: str = "teacher") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_superuser=False)


def seed_owned_resource(session: Session, *, vmid: int, user_id: uuid.UUID) -> None:
    """把 VM 登記為該使用者的平台資源（非 admin 只能轉換自己登記的 VM）。"""
    session.add(
        Resource(
            vmid=vmid,
            user_id=user_id,
            environment_type="test",
            created_at=datetime.now(UTC),
        )
    )
    session.commit()


def seed_template(
    session: Session,
    *,
    pve_vmid: int,
    status: VMTemplateStatus,
    name: str = "old-template",
    version: int = 3,
) -> VMTemplate:
    template = VMTemplate(
        pve_vmid=pve_vmid,
        name=name,
        owner_id=uuid.uuid4(),
        node="pve1",
        resource_type="qemu",
        status=status,
        visibility=VMTemplateVisibility.global_,
        version=version,
        error_message="previous failure",
    )
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


# ---------------------------------------------------------------------------
# repo：get_template_by_pve_vmid 對 deleted 的行為
# ---------------------------------------------------------------------------


def test_get_template_by_pve_vmid_excludes_deleted_by_default(
    db: Session,
) -> None:
    seed_template(db, pve_vmid=102, status=VMTemplateStatus.deleted)

    assert (
        template_repo.get_template_by_pve_vmid(session=db, pve_vmid=102)
        is None
    )
    found = template_repo.get_template_by_pve_vmid(
        session=db, pve_vmid=102, include_deleted=True
    )
    assert found is not None
    assert found.pve_vmid == 102


# ---------------------------------------------------------------------------
# service：軟刪除紀錄應被復用，活躍紀錄仍要 409
# ---------------------------------------------------------------------------


async def test_create_template_reuses_soft_deleted_record(
    db: Session, fake_pve: None
) -> None:
    old = seed_template(db, pve_vmid=102, status=VMTemplateStatus.deleted)
    user = make_user("teacher")
    seed_owned_resource(db, vmid=102, user_id=user.id)

    public, _record = await template_service.create_template(
        session=db,
        user=user,
        data=VMTemplateCreate(
            source_vmid=102,
            name="fresh-template",
            description="rebuilt",
            visibility=VMTemplateVisibility.private,
        ),
    )

    rows = db.exec(
        select(VMTemplate).where(VMTemplate.pve_vmid == 102)
    ).all()
    assert len(rows) == 1  # 復用同一筆，不違反 unique 約束
    row = rows[0]
    assert row.id == old.id
    assert row.status == VMTemplateStatus.creating
    assert row.name == "fresh-template"
    assert row.description == "rebuilt"
    assert row.owner_id == user.id
    assert row.visibility == VMTemplateVisibility.private
    assert row.version == 1  # 全新生命週期，版本歸零重計
    assert row.error_message is None
    assert public.name == "fresh-template"


async def test_create_template_conflicts_on_active_record(
    db: Session, fake_pve: None
) -> None:
    seed_template(db, pve_vmid=102, status=VMTemplateStatus.failed)
    user = make_user("teacher")
    seed_owned_resource(db, vmid=102, user_id=user.id)

    with pytest.raises(ConflictError):
        await template_service.create_template(
            session=db,
            user=user,
            data=VMTemplateCreate(source_vmid=102, name="dup"),
        )


async def test_create_template_rejects_unregistered_vm_for_non_admin(
    db: Session, fake_pve: None
) -> None:
    """未登記在平台的 pool 內 VM（孤兒 / 基礎設施 VM）只有 admin 能轉換。"""
    with pytest.raises(PermissionDeniedError):
        await template_service.create_template(
            session=db,
            user=make_user("teacher"),
            data=VMTemplateCreate(source_vmid=102, name="orphan"),
        )


async def test_create_template_allows_unregistered_vm_for_admin(
    db: Session, fake_pve: None
) -> None:
    public, _record = await template_service.create_template(
        session=db,
        user=make_user("admin"),
        data=VMTemplateCreate(source_vmid=102, name="infra-template"),
    )
    assert public.name == "infra-template"
