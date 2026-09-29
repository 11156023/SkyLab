"""回歸測試：課程環境的範本可見性檢查，以及硬刪除時一起清掉磁碟上的文件。"""

import uuid
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.routes import course_environments as routes
from app.exceptions import BadRequestError
from app.models import (
    CourseEnvironment,
    CourseEnvironmentFile,
    CourseEnvironmentVersion,
    VMTemplate,
    VMTemplateStatus,
)
from app.models.vm_template import VMTemplateVisibility
from app.schemas.course_environment import EnvironmentNodeIn
from app.services.course_environment import environment_service
from app.services.course_environment.environment_service import (
    validate_configuration,
)

TEACHER_A = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="teacher")
TEACHER_B = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="teacher")
ADMIN = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="admin")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            VMTemplate.__table__,  # type: ignore[arg-type]
            CourseEnvironment.__table__,  # type: ignore[arg-type]
            CourseEnvironmentVersion.__table__,  # type: ignore[arg-type]
            CourseEnvironmentFile.__table__,  # type: ignore[arg-type]
        ],
    )
    with Session(engine) as session:
        yield session


def _template(db: Session, *, owner_id, visibility) -> VMTemplate:
    template = VMTemplate(
        pve_vmid=9000 + len(db.exec(select(VMTemplate)).all()),
        name="Ubuntu",
        node="pve",
        owner_id=owner_id,
        visibility=visibility,
        status=VMTemplateStatus.ready,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


def _template_node(template: VMTemplate) -> EnvironmentNodeIn:
    return EnvironmentNodeIn(
        node_key="web",
        source_type="template",
        source_template_id=template.id,
        name="web",
        role="target",
        resource_type="qemu",
        cpu=1,
        memory_mb=1024,
        disk_gb=10,
    )


# --- 範本來源也要檢查擁有者看不看得到 ----------------------------------


def test_other_teachers_private_template_is_rejected(db: Session) -> None:
    template = _template(
        db, owner_id=TEACHER_A.id, visibility=VMTemplateVisibility.private
    )

    with pytest.raises(BadRequestError):
        validate_configuration(db, [_template_node(template)], [], owner=TEACHER_B)


@pytest.mark.parametrize(
    ("owner", "visibility"),
    [
        (TEACHER_A, VMTemplateVisibility.private),  # 自己的私有範本
        (TEACHER_B, VMTemplateVisibility.global_),  # 全域範本
        (ADMIN, VMTemplateVisibility.private),  # 管理員不受限
    ],
)
def test_visible_templates_are_accepted(db: Session, owner, visibility) -> None:
    template = _template(db, owner_id=TEACHER_A.id, visibility=visibility)

    validate_configuration(db, [_template_node(template)], [], owner=owner)


def test_admin_edits_are_checked_against_the_environment_owner() -> None:
    environment = SimpleNamespace(owner_id=TEACHER_A.id)
    stored_owner = SimpleNamespace(id=TEACHER_A.id, role="teacher")
    session = SimpleNamespace(get=lambda _model, _id: stored_owner)

    owner_for = environment_service.environment_owner
    assert owner_for(session, environment, ADMIN) is stored_owner
    assert owner_for(session, environment, TEACHER_A) is TEACHER_A


# --- 硬刪除環境時一起清掉上傳的文件 -------------------------------------


def test_deleting_an_environment_removes_its_file_blobs(
    db: Session, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(environment_service, "ENVIRONMENT_FILE_ROOT", tmp_path)
    monkeypatch.setattr(
        environment_service, "environment_references", lambda *_args: []
    )
    environment = CourseEnvironment(owner_id=TEACHER_A.id, name="Linux Lab")
    db.add(environment)
    db.commit()
    db.refresh(environment)
    blobs = []
    for index in range(2):
        storage_key = f"{uuid.uuid4().hex}.bin"
        (tmp_path / storage_key).write_bytes(b"handout")
        db.add(
            CourseEnvironmentFile(
                environment_id=environment.id,
                filename=f"handout-{index}.pdf",
                storage_key=storage_key,
            )
        )
        blobs.append(tmp_path / storage_key)
    db.commit()

    result = routes.delete_environment(environment.id, db, TEACHER_A)

    assert result == {"status": "deleted"}
    assert all(not blob.exists() for blob in blobs)
