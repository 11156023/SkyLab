"""app.services.course_environment.environment_service 的分支覆蓋測試（記憶體 SQLite）。"""

import io
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import UploadFile
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    CourseEnvironment,
    CourseEnvironmentEdge,
    CourseEnvironmentFile,
    CourseEnvironmentNode,
    CourseEnvironmentPublication,
    CourseEnvironmentVersion,
    CourseEnvironmentVersionStatus,
    QuickPracticeSession,
    TeachingClass,
    User,
    VMTemplate,
    VMTemplateStatus,
)
from app.models.vm_template import VMTemplateVisibility
from app.schemas.course_environment import (
    EnvironmentCreate,
    EnvironmentDraftIn,
    EnvironmentEdgeIn,
    EnvironmentNodeIn,
    EnvironmentPublicationIn,
)
from app.services.course_environment import environment_service as svc

TEACHER = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="teacher")
OTHER = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="teacher")
ADMIN = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="admin")

_TABLES = [
    User.__table__,  # type: ignore[attr-defined]
    VMTemplate.__table__,  # type: ignore[attr-defined]
    CourseEnvironment.__table__,  # type: ignore[attr-defined]
    CourseEnvironmentVersion.__table__,  # type: ignore[attr-defined]
    CourseEnvironmentNode.__table__,  # type: ignore[attr-defined]
    CourseEnvironmentEdge.__table__,  # type: ignore[attr-defined]
    CourseEnvironmentPublication.__table__,  # type: ignore[attr-defined]
    CourseEnvironmentFile.__table__,  # type: ignore[attr-defined]
    TeachingClass.__table__,  # type: ignore[attr-defined]
    QuickPracticeSession.__table__,  # type: ignore[attr-defined]
]


@pytest.fixture
def db():
    # add_file 把寫入丟到 worker thread：in-memory SQLite 要共用同一條連線
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    with Session(engine) as session:
        yield session


@pytest.fixture
def file_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(svc, "ENVIRONMENT_FILE_ROOT", tmp_path)
    return tmp_path


def _template(
    db: Session,
    *,
    owner_id: uuid.UUID = TEACHER.id,
    resource_type: str = "qemu",
    status: VMTemplateStatus = VMTemplateStatus.ready,
    pve_vmid: int | None = None,
) -> VMTemplate:
    template = VMTemplate(
        pve_vmid=pve_vmid or 9000 + len(db.exec(select(VMTemplate)).all()),
        name="Ubuntu",
        node="pve",
        owner_id=owner_id,
        visibility=VMTemplateVisibility.private,
        status=status,
        resource_type=resource_type,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


def _node(template_id: uuid.UUID | None = None, **overrides: Any) -> EnvironmentNodeIn:
    data: dict[str, Any] = {
        "node_key": "web",
        "source_type": "template",
        "source_template_id": template_id,
        "name": "Web",
        "role": "target",
        "resource_type": "qemu",
        "cpu": 2,
        "memory_mb": 2048,
        "disk_gb": 20,
        "network": "lab-net,dmz",
    }
    data.update(overrides)
    return EnvironmentNodeIn(**data)


def _edge(**overrides: Any) -> EnvironmentEdgeIn:
    data: dict[str, Any] = {"source_node_key": "web", "target_node_key": "db"}
    data.update(overrides)
    return EnvironmentEdgeIn(**data)


def _publication(**overrides: Any) -> EnvironmentPublicationIn:
    data: dict[str, Any] = {
        "node_key": "web",
        "port": 80,
        "zone_id": "zone-1",
        "hostname_prefix": "web",
    }
    data.update(overrides)
    return EnvironmentPublicationIn(**data)


def _body(db: Session, **overrides: Any) -> EnvironmentCreate:
    template = _template(db)
    data: dict[str, Any] = {
        "name": "  Linux Lab  ",
        "description": "desc",
        "usage_scope": "both",
        "nodes": [
            _node(template.id),
            _node(template.id, node_key="db", name="DB", network="lab-net"),
        ],
        "edges": [_edge(port=5432)],
        "publications": [_publication()],
    }
    data.update(overrides)
    return EnvironmentCreate(**data)


def _environment(
    db: Session,
    *,
    owner_id: uuid.UUID = TEACHER.id,
    status: CourseEnvironmentVersionStatus = CourseEnvironmentVersionStatus.draft,
    usage_scope: str = "course",
    draft_data: dict[str, Any] | None = None,
) -> tuple[CourseEnvironment, CourseEnvironmentVersion]:
    environment = CourseEnvironment(
        owner_id=owner_id, name="Env", usage_scope=usage_scope
    )
    version = CourseEnvironmentVersion(
        environment_id=environment.id,
        version=1,
        status=status,
        draft_data=draft_data,
    )
    db.add(environment)
    db.add(version)
    db.commit()
    db.refresh(environment)
    db.refresh(version)
    return environment, version


def _teaching_class(db: Session, version_id: uuid.UUID) -> None:
    db.add(
        TeachingClass(
            owner_id=TEACHER.id,
            name="Linux",
            code=f"CS-{uuid.uuid4().hex[:8]}",
            term="115-1",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 31),
            weekday=1,
            start_time=time(13, 10),
            end_time=time(16, 0),
            course_version_id=version_id,
        )
    )
    db.commit()


# --- 查詢與權限 ---------------------------------------------------------------


def test_get_environment_found_and_missing(db: Session) -> None:
    environment, _ = _environment(db)
    assert svc.get_environment(db, TEACHER, environment.id) is environment
    with pytest.raises(NotFoundError):
        svc.get_environment(db, TEACHER, uuid.uuid4())


def test_latest_version_missing(db: Session) -> None:
    environment = CourseEnvironment(owner_id=TEACHER.id, name="Empty")
    db.add(environment)
    db.commit()
    with pytest.raises(NotFoundError):
        svc.latest_version(db, environment)


# --- 自訂來源與設定驗證 -------------------------------------------------------


def test_custom_qemu_source_requires_visible_ready_template(db: Session) -> None:
    _template(db, pve_vmid=7001)
    _template(db, pve_vmid=7002, owner_id=OTHER.id)
    node = _node(source_type="custom", custom_image_ref="7001")

    svc.validate_custom_source(db, node, TEACHER)
    # 別人的私有範本對這位老師不可見，管理員則不受限
    other = _node(source_type="custom", custom_image_ref="7002")
    with pytest.raises(BadRequestError):
        svc.validate_custom_source(db, other, TEACHER)
    svc.validate_custom_source(db, other, ADMIN)
    with pytest.raises(BadRequestError):
        svc.validate_custom_source(
            db, _node(source_type="custom", custom_image_ref="7999"), TEACHER
        )


def test_custom_lxc_source_must_exist_on_cluster(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        svc.proxmox_service,
        "get_lxc_template_node_map",
        lambda: {"local:vztmpl/debian.tar.zst": "pve1"},
    )
    good = _node(
        source_type="custom",
        resource_type="lxc",
        custom_image_ref="local:vztmpl/debian.tar.zst",
    )
    bad = _node(
        source_type="custom", resource_type="lxc", custom_image_ref="local:vztmpl/x"
    )

    svc.validate_configuration(db, [good], [], owner=TEACHER)
    with pytest.raises(BadRequestError):
        svc.validate_configuration(db, [bad], [], owner=TEACHER)


def test_duplicate_node_keys_are_rejected(db: Session) -> None:
    template = _template(db)
    with pytest.raises(BadRequestError):
        svc.validate_configuration(
            db, [_node(template.id), _node(template.id)], [], owner=TEACHER
        )


def test_template_type_mismatch_is_rejected(db: Session) -> None:
    lxc_template = _template(db, resource_type="LXC")
    with pytest.raises(BadRequestError):
        svc.validate_configuration(db, [_node(lxc_template.id)], [], owner=TEACHER)


# --- 建立、列表、草稿與編輯 ---------------------------------------------------


def test_create_environment_persists_configuration(db: Session) -> None:
    result = svc.create_environment(db, TEACHER, _body(db))

    assert result["name"] == "Linux Lab"
    assert [node["node_key"] for node in result["nodes"]] == ["web", "db"]
    assert len(result["edges"]) == 1
    assert len(result["publications"]) == 1
    assert result["per_student"] == {
        "machines": 2,
        "cpu_cores": 4,
        "memory_mb": 4096,
        "disk_gb": 40,
        "ip_count": 2,
        "network_count": 2,
    }
    assert result["classes"] == 0
    assert result["draft_data"] is None

    # 再寫一次會先清掉舊的機器／連線／對外服務
    environment = db.get(CourseEnvironment, result["id"])
    assert environment is not None
    version = svc.latest_version(db, environment)
    svc.replace_nodes(db, version, [result_node(db)], [], owner=TEACHER)
    db.flush()
    assert len(svc.list_edges(db, version.id)) == 0


def result_node(db: Session) -> EnvironmentNodeIn:
    return _node(_template(db).id, node_key="solo")


def test_list_environments_scopes_by_owner(db: Session) -> None:
    mine, _ = _environment(db)
    _environment(db, owner_id=OTHER.id)

    assert [row["id"] for row in svc.list_environments(db, TEACHER)] == [mine.id]
    assert len(svc.list_environments(db, ADMIN)) == 2


def test_list_published_environments_only_returns_published(db: Session) -> None:
    published, version = _environment(
        db, status=CourseEnvironmentVersionStatus.published
    )
    _environment(db)  # 只有草稿
    _environment(db, status=CourseEnvironmentVersionStatus.published, owner_id=OTHER.id)
    _environment(
        db,
        status=CourseEnvironmentVersionStatus.published,
        usage_scope="quick_practice",
    )

    rows = svc.list_published_environments(db, TEACHER)
    assert [(row["id"], row["version_id"]) for row in rows] == [
        (published.id, version.id)
    ]
    assert len(svc.list_published_environments(db, ADMIN)) == 2


def _draft(configuration: dict[str, Any] | None = None) -> EnvironmentDraftIn:
    return EnvironmentDraftIn(
        configuration=configuration or {"name": "Draft"},
        editor={"name": "Draft"},
    )


def test_create_and_save_draft(db: Session) -> None:
    created = svc.create_draft(db, TEACHER, _draft())
    assert created["version"] == 1
    assert created["draft_data"]["configuration"] == {"name": "Draft"}

    environment = db.get(CourseEnvironment, created["id"])
    assert environment is not None
    saved = svc.save_draft(db, environment, _draft({"name": "Second"}))
    assert saved["draft_data"]["configuration"] == {"name": "Second"}


def test_update_environment(db: Session) -> None:
    environment, version = _environment(db, draft_data={"configuration": {}})
    result = svc.update_environment(db, environment, _body(db), ADMIN)

    assert result["name"] == "Linux Lab"
    assert result["usage_scope"] == "both"
    assert version.draft_data is None
    assert len(result["nodes"]) == 2


def test_update_environment_refuses_published_version(db: Session) -> None:
    environment, _ = _environment(db, status=CourseEnvironmentVersionStatus.published)
    with pytest.raises(BadRequestError):
        svc.update_environment(db, environment, _body(db), TEACHER)


# --- 發布 ---------------------------------------------------------------------


def test_publish_refuses_non_draft(db: Session) -> None:
    environment, _ = _environment(db, status=CourseEnvironmentVersionStatus.published)
    with pytest.raises(BadRequestError):
        svc.publish(db, environment, TEACHER)


@pytest.mark.parametrize(
    "configuration",
    [
        {"name": "Lab"},  # 少了 nodes
        {"name": "   ", "nodes": "__nodes__"},  # 名稱只有空白
    ],
)
def test_publish_rejects_invalid_draft(
    db: Session, configuration: dict[str, Any]
) -> None:
    template = _template(db)
    if configuration.get("nodes") == "__nodes__":
        configuration = {
            **configuration,
            "nodes": [_node(template.id).model_dump(mode="json")],
        }
    environment, _ = _environment(
        db, draft_data=_draft(configuration).model_dump(mode="json")
    )
    with pytest.raises(BadRequestError):
        svc.publish(db, environment, TEACHER)


def test_publish_existing_configuration_without_draft(db: Session) -> None:
    environment, version = _environment(db)
    svc.replace_configuration(db, version, _body(db), owner=TEACHER)
    db.commit()
    result = svc.publish(db, environment, TEACHER)
    assert result["status"] == CourseEnvironmentVersionStatus.published


# --- 環境文件 -----------------------------------------------------------------


async def test_file_lifecycle(db: Session, file_root: Path) -> None:
    environment, _ = _environment(db)
    upload = UploadFile(file=io.BytesIO(b"hello"), filename="dir/notes.pdf")

    result = await svc.add_file(db, environment, upload, TEACHER)

    assert [(f["filename"], f["size_bytes"]) for f in result["files"]] == [
        ("notes.pdf", 5)
    ]
    file_id = result["files"][0]["id"]
    env, item = svc.get_environment_file(db, TEACHER, environment.id, file_id)
    assert env is environment
    path = svc.stored_file_path(item)
    assert path.read_bytes() == b"hello"

    after = svc.delete_file(db, environment, item)
    assert after["files"] == []
    assert not path.exists()


def test_get_environment_file_missing_or_foreign(db: Session) -> None:
    environment, _ = _environment(db)
    other, _ = _environment(db)
    foreign = CourseEnvironmentFile(
        environment_id=other.id,
        filename="a.txt",
        storage_key="a.bin",
        size_bytes=1,
        uploaded_by=TEACHER.id,
    )
    db.add(foreign)
    db.commit()

    with pytest.raises(NotFoundError):
        svc.get_environment_file(db, TEACHER, environment.id, uuid.uuid4())
    with pytest.raises(NotFoundError):
        svc.get_environment_file(db, TEACHER, environment.id, foreign.id)


@pytest.mark.parametrize("storage_key", ["missing.bin", "../escape.bin"])
def test_stored_file_path_rejects_missing_or_escaping(
    file_root: Path, storage_key: str
) -> None:
    (file_root.parent / "escape.bin").write_bytes(b"x")
    item = CourseEnvironmentFile(
        environment_id=uuid.uuid4(),
        filename="x",
        storage_key=storage_key,
        size_bytes=1,
        uploaded_by=TEACHER.id,
    )
    with pytest.raises(NotFoundError):
        svc.stored_file_path(item)


# --- 引用盤點與刪除 -----------------------------------------------------------


def test_environment_references(db: Session) -> None:
    empty = CourseEnvironment(owner_id=TEACHER.id, name="NoVersion")
    db.add(empty)
    db.commit()
    assert svc.environment_references(db, empty.id) == []

    environment, version = _environment(db)
    assert svc.environment_references(db, environment.id) == []

    _teaching_class(db, version.id)
    db.add(
        QuickPracticeSession(
            user_id=TEACHER.id,
            environment_version_id=version.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db.commit()
    assert len(svc.environment_references(db, environment.id)) == 2


def test_delete_environment_blocked_by_references(db: Session) -> None:
    environment, version = _environment(db)
    _teaching_class(db, version.id)
    with pytest.raises(BadRequestError):
        svc.delete_environment(db, environment)


def test_delete_environment_removes_versions_and_files(
    db: Session, file_root: Path
) -> None:
    environment, version = _environment(db)
    svc.replace_configuration(db, version, _body(db), owner=TEACHER)
    (file_root / "doc.bin").write_bytes(b"x")
    db.add(
        CourseEnvironmentFile(
            environment_id=environment.id,
            filename="doc.pdf",
            storage_key="doc.bin",
            size_bytes=1,
            uploaded_by=TEACHER.id,
        )
    )
    db.commit()
    environment_id = environment.id

    svc.delete_environment(db, environment)

    assert db.get(CourseEnvironment, environment_id) is None
    assert db.exec(select(CourseEnvironmentVersion)).all() == []
    assert db.exec(select(CourseEnvironmentNode)).all() == []
    assert not (file_root / "doc.bin").exists()
