"""Teacher-managed, versioned per-student course environments."""

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlmodel import col, delete, func, select

from app.api.deps import InstructorUser, SessionDep
from app.core.authorizers import require_teaching_access
from app.core.i18n import t
from app.core.permissions import is_admin
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    CourseEnvironment,
    CourseEnvironmentAudience,
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
from app.models.base import get_datetime_utc
from app.repositories import vm_template as vm_template_repo
from app.services.proxmox import proxmox_service

router = APIRouter(prefix="/course-environments", tags=["course-environments"])

# 與班級任務檔同一套做法：檔案落在 data/ 底下，資料庫只存 storage_key
ENVIRONMENT_FILE_ROOT = (
    Path(__file__).resolve().parents[3] / "data" / "course-environment-files"
)
MAX_ENVIRONMENT_FILE_BYTES = 50 * 1024 * 1024


class EnvironmentNodeIn(BaseModel):
    node_key: str = Field(min_length=1, max_length=80)
    source_type: Literal["template", "custom"] = "template"
    source_template_id: uuid.UUID | None = None
    custom_image_ref: str | None = Field(default=None, max_length=500)
    custom_username: str | None = Field(default=None, max_length=32)
    custom_unprivileged: bool = True
    name: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=120)
    resource_type: str = Field(pattern="^(qemu|lxc)$")
    cpu: int = Field(ge=1, le=64)
    memory_mb: int = Field(ge=128, le=131072)
    disk_gb: int = Field(ge=1, le=2000)
    network: str = Field(default="lab-net", min_length=1, max_length=255)
    position_x: float = Field(default=80.0, ge=-5000, le=5000)
    position_y: float = Field(default=120.0, ge=-5000, le=5000)

    @model_validator(mode="after")
    def validate_source(self) -> "EnvironmentNodeIn":
        if self.source_type == "template":
            if self.source_template_id is None:
                raise ValueError(t("course_env.node_template_required"))
            self.custom_image_ref = None
        else:
            if not (self.custom_image_ref or "").strip():
                raise ValueError(t("course_env.node_image_required"))
            self.source_template_id = None
            if self.resource_type == "qemu":
                try:
                    if int(self.custom_image_ref or "0") <= 0:
                        raise ValueError
                except ValueError as exc:
                    raise ValueError(t("course_env.node_invalid_vmid")) from exc
        return self


class EnvironmentEdgeIn(BaseModel):
    source_node_key: str = Field(min_length=1, max_length=80)
    target_node_key: str = Field(min_length=1, max_length=80)
    direction: Literal["one_way", "bidirectional"] = "one_way"
    protocol: Literal["any", "tcp", "udp", "icmp", "icmpv6", "sctp"] = "tcp"
    port: int | None = Field(default=22, ge=1, le=65535)

    @model_validator(mode="after")
    def validate_edge(self) -> "EnvironmentEdgeIn":
        if self.source_node_key == self.target_node_key:
            raise ValueError(t("course_env.edge_same_node"))
        if self.protocol == "any":
            self.port = None
        elif self.port is None:
            raise ValueError(t("course_env.edge_port_required"))
        return self


class EnvironmentPublicationIn(BaseModel):
    """一條「外網 → 機器」的宣告。

    網域與對外 port 都是全域唯一的資源，而每位學生都會拿到一份自己的環境，
    所以模板上只能填主機名樣板、或只說「要一個對外 port」；實際網址與 port
    在開課／開練習時逐人組出來、配出來。
    """

    node_key: str = Field(min_length=1, max_length=80)
    mode: Literal["domain", "port_forward"] = "domain"
    port: int = Field(ge=1, le=65535)
    protocol: Literal["tcp", "udp"] = "tcp"
    hostname_prefix: str | None = Field(default=None, max_length=120)
    zone_id: str | None = Field(default=None, max_length=64)
    enable_https: bool = True

    @model_validator(mode="after")
    def validate_mode(self) -> "EnvironmentPublicationIn":
        if self.mode != "domain":
            self.hostname_prefix = None
            self.zone_id = None
            return self
        if self.protocol != "tcp":
            raise ValueError(t("course_env.publication_domain_tcp_only"))
        if not (self.zone_id or "").strip():
            raise ValueError(t("course_env.publication_zone_required"))
        prefix = (self.hostname_prefix or "").strip().lower()
        if "{student}" not in prefix:
            # 少了它，全班會搶同一個網址，只有第一位學生拿得到
            raise ValueError(t("course_env.publication_student_placeholder"))
        self.hostname_prefix = prefix
        return self


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    usage_scope: Literal["course", "quick_practice", "both"] = "course"
    audience: Literal["owner", "class", "campus"] = "class"
    max_concurrent_sessions: int | None = Field(default=None, ge=1, le=500)
    audience_class_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    nodes: list[EnvironmentNodeIn] = Field(min_length=1, max_length=3)
    edges: list[EnvironmentEdgeIn] = Field(default_factory=list, max_length=6)
    publications: list[EnvironmentPublicationIn] = Field(
        default_factory=list, max_length=6
    )
    # explicit：只開畫出的連線，沒畫就隔離；segment：同網段全部互通（舊行為）
    peer_policy: Literal["explicit", "segment"] = "explicit"

    @model_validator(mode="after")
    def validate_audience(self) -> "EnvironmentCreate":
        """Audience only gates the student quick-practice list.

        A course-only environment never reaches that list, so an empty class
        allow-list is fine there; once the environment is offered as practice
        the teacher must say which classes may see it.
        """
        if self.audience != "class":
            self.audience_class_ids = []
            return self
        self.audience_class_ids = list(dict.fromkeys(self.audience_class_ids))
        if not self.audience_class_ids and self.usage_scope in {
            "quick_practice",
            "both",
        }:
            raise ValueError(t("course_env.audience_class_required"))
        return self


class EnvironmentUpdate(EnvironmentCreate):
    pass


class EnvironmentBasicsIn(BaseModel):
    """Name, purpose and offering — editable at any version status.

    These live on the environment row rather than on a version. Publication
    freezes the machine configuration, not what the environment is called or
    who it is offered to, so the teacher can keep them current without cutting
    a new version.
    """

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    usage_scope: Literal["course", "quick_practice", "both"]


class EnvironmentDraftIn(BaseModel):
    # The editor may contain empty fields or unfinished numeric input. Only
    # publication turns this into a validated, deployable configuration.
    configuration: dict[str, Any]
    editor: dict[str, Any]
    # 只用來找「要續寫哪一份既有草稿」，永遠不會變成新資料列的 id：
    # 讓 client 指定 primary key，等於可以先佔走一個還沒被用到的 id。
    draft_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_size(self) -> "EnvironmentDraftIn":
        if len(self.model_dump_json().encode()) > 262144:
            raise ValueError("Draft exceeds 256 KiB")
        return self


def _get_environment(
    session: SessionDep, current_user: User, environment_id: uuid.UUID
) -> CourseEnvironment:
    item = session.get(CourseEnvironment, environment_id)
    if item is None:
        raise NotFoundError(t("course_env.not_found"))
    require_teaching_access(current_user, item.owner_id)
    return item


def _versions(
    session: SessionDep, environment_id: uuid.UUID
) -> list[CourseEnvironmentVersion]:
    return list(
        session.exec(
            select(CourseEnvironmentVersion)
            .where(CourseEnvironmentVersion.environment_id == environment_id)
            .order_by(col(CourseEnvironmentVersion.version).desc())
        ).all()
    )


def _nodes(session: SessionDep, version_id: uuid.UUID) -> list[CourseEnvironmentNode]:
    return list(
        session.exec(
            select(CourseEnvironmentNode)
            .where(CourseEnvironmentNode.version_id == version_id)
            .order_by(col(CourseEnvironmentNode.sort_order))
        ).all()
    )


def _publications(
    session: SessionDep, version_id: uuid.UUID
) -> list[CourseEnvironmentPublication]:
    return list(
        session.exec(
            select(CourseEnvironmentPublication)
            .where(CourseEnvironmentPublication.version_id == version_id)
            .order_by(col(CourseEnvironmentPublication.sort_order))
        ).all()
    )


def _edges(session: SessionDep, version_id: uuid.UUID) -> list[CourseEnvironmentEdge]:
    return list(
        session.exec(
            select(CourseEnvironmentEdge).where(
                CourseEnvironmentEdge.version_id == version_id
            )
        ).all()
    )


def _validate_custom_source(
    session: SessionDep, node: EnvironmentNodeIn, owner: User
) -> None:
    """自訂來源一樣要驗。

    以前 ``source_type="custom"`` 直接跳過檢查，等於老師可以填任何一個
    VMID 或任何一份 LXC 範本樣板，把別人的機器（含別人上傳的映像）
    當成課程來源整班複製出去。
    """
    reference = (node.custom_image_ref or "").strip()
    if node.resource_type == "qemu":
        template = vm_template_repo.get_template_by_pve_vmid(
            session=session, pve_vmid=int(reference)
        )
        if (
            template is None
            or template.status != VMTemplateStatus.ready
            or not (
                is_admin(owner)
                or vm_template_repo.is_template_visible_to_user(
                    template=template, user_id=owner.id
                )
            )
        ):
            raise BadRequestError(t("course_env.template_not_ready", name=node.name))
        return
    if reference not in proxmox_service.get_lxc_template_node_map():
        raise BadRequestError(t("course_env.lxc_image_not_found", name=node.name))


def _validate_configuration(
    session: SessionDep,
    nodes: list[EnvironmentNodeIn],
    edges: list[EnvironmentEdgeIn],
    publications: list[EnvironmentPublicationIn] | None = None,
    *,
    owner: User,
) -> None:
    if len({node.node_key for node in nodes}) != len(nodes):
        raise BadRequestError(t("course_env.duplicate_node_key"))
    for node in nodes:
        if node.source_type == "custom":
            _validate_custom_source(session, node, owner)
            continue
        template = session.get(VMTemplate, node.source_template_id)
        if template is None or template.status != VMTemplateStatus.ready:
            raise BadRequestError(t("course_env.template_not_ready", name=node.name))
        expected = "lxc" if template.resource_type.lower() == "lxc" else "qemu"
        if node.resource_type != expected:
            raise BadRequestError(t("course_env.type_mismatch", name=node.name))
    node_keys = {node.node_key for node in nodes}
    # 每條連線實際授予的方向：單向一個，雙向兩個。以此比對才抓得到
    # 「A→B 單向」被「A↔B 雙向」涵蓋、或「A↔B」與「B↔A」互為同一件事。
    granted: dict[tuple[str, str], list[tuple[str, int | None]]] = {}
    for edge in edges:
        if (
            edge.source_node_key not in node_keys
            or edge.target_node_key not in node_keys
        ):
            raise BadRequestError(t("course_env.edge_unknown_node"))
        pairs = [(edge.source_node_key, edge.target_node_key)]
        if edge.direction == "bidirectional":
            pairs.append((edge.target_node_key, edge.source_node_key))
        for pair in pairs:
            for protocol, port in granted.get(pair, []):
                # 舊資料的 "any" 不分協定與 port，與同一組機器的任何規則重疊
                if (
                    protocol == "any"
                    or edge.protocol == "any"
                    or (protocol, port) == (edge.protocol, edge.port)
                ):
                    raise BadRequestError(
                        t(
                            "course_env.overlapping_edge",
                            source=pair[0],
                            target=pair[1],
                        )
                    )
            granted.setdefault(pair, []).append((edge.protocol, edge.port))

    seen_publications: set[tuple[str, int, str]] = set()
    # 一個網域只能指向一個目標，所以整份環境裡的主機名樣板必須各不相同，
    # 否則第二條之後在開課時才會撞上「網域已被占用」。
    seen_hostnames: set[tuple[str, str]] = set()
    for publication in publications or []:
        if publication.node_key not in node_keys:
            raise BadRequestError(t("course_env.publication_unknown_node"))
        signature = (publication.node_key, publication.port, publication.protocol)
        if signature in seen_publications:
            raise BadRequestError(
                t("course_env.duplicate_publication", port=publication.port)
            )
        seen_publications.add(signature)
        if publication.mode != "domain":
            continue
        hostname = (
            str(publication.zone_id or ""),
            str(publication.hostname_prefix or ""),
        )
        if hostname in seen_hostnames:
            raise BadRequestError(
                t(
                    "course_env.duplicate_publication_hostname",
                    hostname=publication.hostname_prefix,
                )
            )
        seen_hostnames.add(hostname)


def _files(session: SessionDep, environment_id: uuid.UUID) -> list[CourseEnvironmentFile]:
    return list(
        session.exec(
            select(CourseEnvironmentFile)
            .where(CourseEnvironmentFile.environment_id == environment_id)
            .order_by(col(CourseEnvironmentFile.created_at))
        ).all()
    )


def _audience_class_ids(
    session: SessionDep, environment_id: uuid.UUID
) -> list[uuid.UUID]:
    return list(
        session.exec(
            select(CourseEnvironmentAudience.class_id).where(
                CourseEnvironmentAudience.environment_id == environment_id
            )
        ).all()
    )


def _replace_audience(
    session: SessionDep,
    *,
    environment: CourseEnvironment,
    owner_id: uuid.UUID | None,
    class_ids: list[uuid.UUID],
) -> None:
    """Rewrite the class allow-list.

    A teacher may only open an environment to their own classes; ``owner_id``
    is None for admins, who curate other people's environments too.
    """
    for class_id in class_ids:
        teaching_class = session.get(TeachingClass, class_id)
        if teaching_class is None:
            raise BadRequestError(t("course_env.class_not_found"))
        if owner_id is not None and teaching_class.owner_id != owner_id:
            raise BadRequestError(
                t("course_env.class_not_owned", name=teaching_class.name)
            )
    session.exec(
        delete(CourseEnvironmentAudience).where(
            col(CourseEnvironmentAudience.environment_id) == environment.id
        )
    )
    for class_id in class_ids:
        session.add(
            CourseEnvironmentAudience(environment_id=environment.id, class_id=class_id)
        )


def _replace_nodes(
    session: SessionDep,
    version: CourseEnvironmentVersion,
    nodes: list[EnvironmentNodeIn],
    edges: list[EnvironmentEdgeIn],
    publications: list[EnvironmentPublicationIn] | None = None,
    *,
    owner: User,
) -> None:
    _validate_configuration(session, nodes, edges, publications, owner=owner)
    session.exec(
        delete(CourseEnvironmentPublication).where(
            col(CourseEnvironmentPublication.version_id) == version.id
        )
    )
    session.exec(
        delete(CourseEnvironmentEdge).where(
            col(CourseEnvironmentEdge.version_id) == version.id
        )
    )
    session.exec(
        delete(CourseEnvironmentNode).where(
            col(CourseEnvironmentNode.version_id) == version.id
        )
    )
    for index, node in enumerate(nodes):
        session.add(
            CourseEnvironmentNode(
                version_id=version.id,
                sort_order=index,
                **node.model_dump(),
            )
        )
    for edge in edges:
        session.add(CourseEnvironmentEdge(version_id=version.id, **edge.model_dump()))
    for index, publication in enumerate(publications or []):
        session.add(
            CourseEnvironmentPublication(
                version_id=version.id,
                sort_order=index,
                **publication.model_dump(),
            )
        )


def _serialize_version(
    session: SessionDep,
    environment: CourseEnvironment,
    version: CourseEnvironmentVersion,
) -> dict[str, Any]:
    nodes = _nodes(session, version.id)
    edges = _edges(session, version.id)
    publications = _publications(session, version.id)
    class_count = session.exec(
        select(func.count(col(TeachingClass.id))).where(
            col(TeachingClass.course_version_id) == version.id
        )
    ).one()
    return {
        "id": environment.id,
        "version_id": version.id,
        "owner_id": environment.owner_id,
        "name": environment.name,
        "description": environment.description,
        "usage_scope": environment.usage_scope,
        "audience": environment.audience,
        "max_concurrent_sessions": environment.max_concurrent_sessions,
        "audience_class_ids": _audience_class_ids(session, environment.id),
        "files": [
            {
                "id": item.id,
                "filename": item.filename,
                "size_bytes": item.size_bytes,
                "created_at": item.created_at,
            }
            for item in _files(session, environment.id)
        ],
        "version": version.version,
        "status": version.status,
        "configuration_hash": version.configuration_hash,
        "draft_data": json.loads(version.draft_data) if version.draft_data else None,
        "created_at": environment.created_at,
        "updated_at": environment.updated_at,
        "published_at": version.published_at,
        "classes": int(class_count or 0),
        "peer_policy": version.peer_policy,
        "nodes": [node.model_dump() for node in nodes],
        "edges": [edge.model_dump() for edge in edges],
        "publications": [item.model_dump() for item in publications],
        "per_student": {
            "machines": len(nodes),
            "cpu_cores": sum(node.cpu for node in nodes),
            "memory_mb": sum(node.memory_mb for node in nodes),
            "disk_gb": sum(node.disk_gb for node in nodes),
            "ip_count": len(nodes),
            "network_count": len(
                {
                    name.strip()
                    for node in nodes
                    for name in node.network.split(",")
                    if name.strip()
                }
            ),
        },
    }


def _latest(
    session: SessionDep, environment: CourseEnvironment
) -> CourseEnvironmentVersion:
    versions = _versions(session, environment.id)
    if not versions:
        raise NotFoundError(t("course_env.version_not_found"))
    return versions[0]


@router.get("")
def list_environments(
    session: SessionDep, current_user: InstructorUser
) -> list[dict[str, Any]]:
    query = select(CourseEnvironment).order_by(col(CourseEnvironment.updated_at).desc())
    if not current_user.is_superuser and current_user.role != "admin":
        query = query.where(CourseEnvironment.owner_id == current_user.id)
    result = []
    for environment in session.exec(query).all():
        result.append(
            _serialize_version(session, environment, _latest(session, environment))
        )
    return result


@router.get("/published")
def list_published_environments(
    session: SessionDep, current_user: InstructorUser
) -> list[dict[str, Any]]:
    result = []
    query = (
        select(CourseEnvironment)
        .where(CourseEnvironment.usage_scope.in_(["course", "both"]))
        .order_by(col(CourseEnvironment.updated_at).desc())
    )
    if not current_user.is_superuser and current_user.role != "admin":
        query = query.where(CourseEnvironment.owner_id == current_user.id)
    for environment in session.exec(query).all():
        versions = _versions(session, environment.id)
        published = next(
            (
                version
                for version in versions
                if version.status == CourseEnvironmentVersionStatus.published
            ),
            None,
        )
        if published:
            result.append(_serialize_version(session, environment, published))
    return result


@router.post("/drafts", status_code=201)
def create_environment_draft(
    body: EnvironmentDraftIn, session: SessionDep, current_user: InstructorUser
) -> dict[str, Any]:
    if body.draft_id is not None:
        existing = session.get(CourseEnvironment, body.draft_id)
        if existing is not None:
            # 續寫既有草稿：權限由 save_environment_draft 的 _get_environment 把關
            return save_environment_draft(existing.id, body, session, current_user)
    # 新資料列的 id 一律由伺服器產生
    environment = CourseEnvironment(
        id=uuid.uuid4(), owner_id=current_user.id, name=""
    )
    version = CourseEnvironmentVersion(
        environment_id=environment.id, version=1, draft_data=body.model_dump_json()
    )
    session.add(environment)
    session.add(version)
    session.commit()
    return _serialize_version(session, environment, version)


@router.put("/{environment_id}/draft")
def save_environment_draft(
    environment_id: uuid.UUID,
    body: EnvironmentDraftIn,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    environment = _get_environment(session, current_user, environment_id)
    version = _latest(session, environment)
    if version.status != CourseEnvironmentVersionStatus.draft:
        raise BadRequestError(t("course_env.published_immutable"))
    version.draft_data = body.model_dump_json()
    environment.updated_at = get_datetime_utc()
    session.add(version)
    session.add(environment)
    session.commit()
    return _serialize_version(session, environment, version)


@router.get("/{environment_id}")
def get_environment(
    environment_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    environment = _get_environment(session, current_user, environment_id)
    return _serialize_version(session, environment, _latest(session, environment))


@router.post("", status_code=201)
def create_environment(
    body: EnvironmentCreate,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    environment = CourseEnvironment(
        owner_id=current_user.id,
        name=body.name.strip(),
        description=body.description,
        usage_scope=body.usage_scope,
        audience=body.audience,
        max_concurrent_sessions=body.max_concurrent_sessions,
    )
    version = CourseEnvironmentVersion(environment_id=environment.id, version=1)
    session.add(environment)
    session.add(version)
    session.flush()
    _replace_audience(
        session,
        environment=environment,
        owner_id=None if is_admin(current_user) else current_user.id,
        class_ids=body.audience_class_ids,
    )
    _replace_nodes(
        session, version, body.nodes, body.edges, body.publications, owner=current_user
    )
    version.peer_policy = body.peer_policy
    session.commit()
    return _serialize_version(session, environment, version)


@router.put("/{environment_id}")
def update_environment(
    environment_id: uuid.UUID,
    body: EnvironmentUpdate,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    environment = _get_environment(session, current_user, environment_id)
    version = _latest(session, environment)
    if version.status != CourseEnvironmentVersionStatus.draft:
        raise BadRequestError(t("course_env.published_immutable"))
    environment.name = body.name.strip()
    environment.description = body.description
    environment.usage_scope = body.usage_scope
    environment.audience = body.audience
    environment.max_concurrent_sessions = body.max_concurrent_sessions
    environment.updated_at = get_datetime_utc()
    _replace_audience(
        session,
        environment=environment,
        owner_id=None if is_admin(current_user) else environment.owner_id,
        class_ids=body.audience_class_ids,
    )
    _replace_nodes(
        session, version, body.nodes, body.edges, body.publications, owner=current_user
    )
    version.peer_policy = body.peer_policy
    version.draft_data = None
    session.add(version)
    session.add(environment)
    session.commit()
    return _serialize_version(session, environment, version)


@router.patch("/{environment_id}/basics")
def update_environment_basics(
    environment_id: uuid.UUID,
    body: EnvironmentBasicsIn,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    """調整名稱、用途與提供方式，不需要開新版本。

    改成不提供給學生只影響「還沒啟動」的人：已經在跑的練習 Session 照自己的
    期限走完。若最新版本還帶著草稿快照，連草稿一起改，免得之後發布又把這次
    的調整蓋回去。
    """
    environment = _get_environment(session, current_user, environment_id)
    environment.name = body.name.strip()
    environment.description = body.description
    environment.usage_scope = body.usage_scope
    version = _latest(session, environment)
    if version.draft_data:
        draft = json.loads(version.draft_data)
        fields = {
            "name": environment.name,
            "description": environment.description,
            "usage_scope": body.usage_scope,
        }
        if isinstance(draft.get("configuration"), dict):
            draft["configuration"].update(fields)
        if isinstance(draft.get("editor"), dict):
            draft["editor"].update(
                {
                    "name": environment.name,
                    "description": environment.description,
                    "usageScope": body.usage_scope,
                }
            )
        version.draft_data = json.dumps(draft)
        session.add(version)
    environment.updated_at = get_datetime_utc()
    session.add(environment)
    session.commit()
    return _serialize_version(session, environment, version)


@router.post("/{environment_id}/files", status_code=201)
async def upload_environment_file(
    environment_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """文件掛在環境身分上，換版本不會讓講義跟著消失。"""
    environment = _get_environment(session, current_user, environment_id)
    filename = (file.filename or "file").replace("\\", "/").split("/")[-1].strip()
    if not filename or filename in {".", ".."}:
        raise BadRequestError(t("course_env.file_name_invalid"))
    if len(filename) > 255:
        raise BadRequestError(t("course_env.file_name_too_long"))

    file_id = uuid.uuid4()
    storage_key = f"{file_id.hex}.bin"
    destination = ENVIRONMENT_FILE_ROOT / storage_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_ENVIRONMENT_FILE_BYTES:
                    raise BadRequestError(t("course_env.file_too_large"))
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    session.add(
        CourseEnvironmentFile(
            id=file_id,
            environment_id=environment.id,
            filename=filename,
            storage_key=storage_key,
            size_bytes=written,
            uploaded_by=current_user.id,
        )
    )
    environment.updated_at = get_datetime_utc()
    session.add(environment)
    session.commit()
    return _serialize_version(session, environment, _latest(session, environment))


def _environment_file(
    session: SessionDep,
    current_user: User,
    environment_id: uuid.UUID,
    file_id: uuid.UUID,
) -> tuple[CourseEnvironment, CourseEnvironmentFile]:
    environment = _get_environment(session, current_user, environment_id)
    item = session.get(CourseEnvironmentFile, file_id)
    if item is None or item.environment_id != environment.id:
        raise NotFoundError(t("course_env.file_not_found"))
    return environment, item


@router.get("/{environment_id}/files/{file_id}", response_class=FileResponse)
def download_environment_file(
    environment_id: uuid.UUID,
    file_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> FileResponse:
    _environment, item = _environment_file(
        session, current_user, environment_id, file_id
    )
    root = ENVIRONMENT_FILE_ROOT.resolve()
    stored = (root / item.storage_key).resolve()
    # storage_key 由伺服器產生，但仍然擋一次路徑跳脫，免得日後有人改成沿用檔名
    if not stored.is_relative_to(root) or not stored.is_file():
        raise NotFoundError(t("course_env.file_not_found"))
    return FileResponse(stored, filename=item.filename)


@router.delete("/{environment_id}/files/{file_id}")
def delete_environment_file(
    environment_id: uuid.UUID,
    file_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    environment, item = _environment_file(
        session, current_user, environment_id, file_id
    )
    storage_key = item.storage_key
    session.delete(item)
    environment.updated_at = get_datetime_utc()
    session.add(environment)
    session.commit()
    root = ENVIRONMENT_FILE_ROOT.resolve()
    stored = (root / storage_key).resolve()
    if stored.is_relative_to(root):
        stored.unlink(missing_ok=True)
    return _serialize_version(session, environment, _latest(session, environment))


@router.post("/{environment_id}/publish")
def publish_environment(
    environment_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    environment = _get_environment(session, current_user, environment_id)
    version = _latest(session, environment)
    if version.status != CourseEnvironmentVersionStatus.draft:
        raise BadRequestError(t("course_env.only_draft_publishable"))
    if version.draft_data:
        draft = EnvironmentDraftIn.model_validate_json(version.draft_data)
        try:
            body = EnvironmentCreate.model_validate(draft.configuration)
            if not body.name.strip():
                raise ValueError("Environment name is required")
        except (ValidationError, ValueError) as exc:
            raise BadRequestError(str(exc)) from exc
        environment.name = body.name.strip()
        environment.description = body.description
        environment.usage_scope = body.usage_scope
        environment.audience = body.audience
        environment.max_concurrent_sessions = body.max_concurrent_sessions
        _replace_audience(
            session,
            environment=environment,
            owner_id=None if is_admin(current_user) else environment.owner_id,
            class_ids=body.audience_class_ids,
        )
        _replace_nodes(
            session,
            version,
            body.nodes,
            body.edges,
            body.publications,
            owner=current_user,
        )
        version.peer_policy = body.peer_policy
        session.flush()
        version.draft_data = None
    nodes = _nodes(session, version.id)
    edges = _edges(session, version.id)
    publications = _publications(session, version.id)
    _validate_configuration(
        session,
        [EnvironmentNodeIn.model_validate(node.model_dump()) for node in nodes],
        [EnvironmentEdgeIn.model_validate(edge.model_dump()) for edge in edges],
        [
            EnvironmentPublicationIn.model_validate(item.model_dump())
            for item in publications
        ],
        owner=current_user,
    )
    payload: dict[str, Any] = {
        "peer_policy": version.peer_policy,
        "nodes": [
            {
                key: value
                for key, value in node.model_dump().items()
                if key not in {"id", "version_id"}
            }
            for node in nodes
        ],
        "edges": [
            {
                key: value
                for key, value in edge.model_dump().items()
                if key not in {"id", "version_id"}
            }
            for edge in edges
        ],
        "publications": [
            {
                key: value
                for key, value in item.model_dump().items()
                if key not in {"id", "version_id"}
            }
            for item in publications
        ],
    }
    version.configuration_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    version.status = CourseEnvironmentVersionStatus.published
    version.published_at = get_datetime_utc()
    environment.updated_at = get_datetime_utc()
    session.add(version)
    session.add(environment)
    session.commit()
    return _serialize_version(session, environment, version)


def _environment_references(
    session: SessionDep, environment_id: uuid.UUID
) -> list[str]:
    """刪除前的引用盤點：有引用就不能硬刪，只能把提供方式收起來。"""
    version_ids = [version.id for version in _versions(session, environment_id)]
    if not version_ids:
        return []
    reasons: list[str] = []
    class_count = session.exec(
        select(func.count(col(TeachingClass.id))).where(
            col(TeachingClass.course_version_id).in_(version_ids)
        )
    ).one()
    if int(class_count or 0):
        reasons.append(t("course_env.reason_classes_using", count=int(class_count)))
    session_count = session.exec(
        select(func.count(col(QuickPracticeSession.id))).where(
            col(QuickPracticeSession.environment_version_id).in_(version_ids)
        )
    ).one()
    if int(session_count or 0):
        reasons.append(t("course_env.reason_sessions_using", count=int(session_count)))
    return reasons


@router.delete("/{environment_id}")
def delete_environment(
    environment_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, str]:
    """硬刪除；只允許沒有任何引用的環境，其餘一律走下架。"""
    environment = _get_environment(session, current_user, environment_id)
    reasons = _environment_references(session, environment.id)
    if reasons:
        raise BadRequestError(
            t("course_env.delete_blocked", reasons="、".join(reasons))
        )
    version_ids = [version.id for version in _versions(session, environment.id)]
    if version_ids:
        session.exec(
            delete(CourseEnvironmentPublication).where(
                col(CourseEnvironmentPublication.version_id).in_(version_ids)
            )
        )
        session.exec(
            delete(CourseEnvironmentEdge).where(
                col(CourseEnvironmentEdge.version_id).in_(version_ids)
            )
        )
        session.exec(
            delete(CourseEnvironmentNode).where(
                col(CourseEnvironmentNode.version_id).in_(version_ids)
            )
        )
        session.exec(
            delete(CourseEnvironmentVersion).where(
                col(CourseEnvironmentVersion.id).in_(version_ids)
            )
        )
    session.exec(
        delete(CourseEnvironmentAudience).where(
            col(CourseEnvironmentAudience.environment_id) == environment.id
        )
    )
    session.delete(environment)
    session.commit()
    return {"status": "deleted"}
