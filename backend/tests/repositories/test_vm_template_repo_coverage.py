"""app.repositories.vm_template 的查詢與可見範圍分支覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import VMTemplate, VMTemplateStatus, VMTemplateVisibility
from app.repositories import vm_template as repo


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _tpl(
    session: Session,
    pve_vmid: int,
    owner_id: uuid.UUID,
    *,
    name: str = "tpl",
    visibility: VMTemplateVisibility = VMTemplateVisibility.private,
    status: VMTemplateStatus | None = None,
    source_vmid: int | None = None,
    commit: bool = True,
) -> VMTemplate:
    template = repo.create_template(
        session=session,
        pve_vmid=pve_vmid,
        name=name,
        owner_id=owner_id,
        node="pve1",
        resource_type="qemu",
        visibility=visibility,
        source_vmid=source_vmid,
        commit=commit,
    )
    if status is not None:
        template.status = status
        session.add(template)
        session.commit()
    return template


def test_create_without_commit_and_lookups(session: Session) -> None:
    owner = uuid.uuid4()
    flushed = _tpl(session, 9000, owner, commit=False)
    assert flushed.id is not None
    assert repo.get_template(session=session, template_id=flushed.id) is flushed

    updating = _tpl(
        session, 9001, owner, status=VMTemplateStatus.updating, source_vmid=500
    )
    found = repo.get_updating_template_by_source_vmid(session=session, source_vmid=500)
    assert found is not None and found.id == updating.id
    assert (
        repo.get_updating_template_by_source_vmid(session=session, source_vmid=501)
        is None
    )

    deleted = _tpl(session, 9002, owner, status=VMTemplateStatus.deleted)
    assert repo.get_template_by_pve_vmid(session=session, pve_vmid=9002) is None
    assert (
        repo.get_template_by_pve_vmid(
            session=session, pve_vmid=9002, include_deleted=True
        ).id
        == deleted.id
    )

    live = {t.id for t in repo.list_all_templates(session=session)}
    assert deleted.id not in live and updating.id in live
    everything = {
        t.id for t in repo.list_all_templates(session=session, include_deleted=True)
    }
    assert deleted.id in everything
    assert repo.registered_pve_vmids(session=session) == {9000, 9001}


def test_visibility_and_catalog(session: Session) -> None:
    owner = uuid.uuid4()
    other = uuid.uuid4()
    mine = _tpl(session, 9100, owner, status=VMTemplateStatus.creating)
    shared = _tpl(
        session,
        9101,
        other,
        name="b-shared",
        visibility=VMTemplateVisibility.global_,
        status=VMTemplateStatus.ready,
    )
    _tpl(
        session,
        9102,
        other,
        name="a-shared",
        visibility=VMTemplateVisibility.global_,
        status=VMTemplateStatus.ready,
    )
    hidden = _tpl(session, 9103, other, status=VMTemplateStatus.ready)

    visible = {
        t.id for t in repo.list_visible_templates(session=session, user_id=owner)
    }
    assert mine.id in visible and shared.id in visible and hidden.id not in visible
    ready = {
        t.id
        for t in repo.list_visible_templates(
            session=session, user_id=owner, only_ready=True
        )
    }
    assert mine.id not in ready and shared.id in ready

    catalog = repo.list_student_catalog(session=session)
    assert [t.name for t in catalog] == ["a-shared", "b-shared"]

    assert repo.is_template_visible_to_user(template=mine, user_id=owner)
    assert repo.is_template_visible_to_user(template=shared, user_id=owner)
    assert not repo.is_template_visible_to_user(template=hidden, user_id=owner)
