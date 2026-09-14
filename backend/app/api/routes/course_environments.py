"""Teacher-managed, versioned per-student course environments."""

import hashlib
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator
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

router = APIRouter(prefix="/course-environments", tags=["course-environments"])


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

    網域是全域唯一的資源，而每位學生都會拿到一份自己的環境，所以模板上
    只能填主機名樣板；實際網址在開課／開練習時逐人組出來。
    """

    node_key: str = Field(min_length=1, max_length=80)
    mode: Literal["domain", "firewall_only"] = "domain"
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


def _validate_configuration(
    session: SessionDep,
    nodes: list[EnvironmentNodeIn],
    edges: list[EnvironmentEdgeIn],
    publications: list[EnvironmentPublicationIn] | None = None,
) -> None:
    if len({node.node_key for node in nodes}) != len(nodes):
        raise BadRequestError(t("course_env.duplicate_node_key"))
    for node in nodes:
        if node.source_type == "custom":
            continue
        template = session.get(VMTemplate, node.source_template_id)
        if template is None or template.status != VMTemplateStatus.ready:
            raise BadRequestError(
                t("course_env.template_not_ready", name=node.name)
            )
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
        hostname = (str(publication.zone_id or ""), str(publication.hostname_prefix or ""))
        if hostname in seen_hostnames:
            raise BadRequestError(
                t(
                    "course_env.duplicate_publication_hostname",
                    hostname=publication.hostname_prefix,
                )
            )
        seen_hostnames.add(hostname)


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
            CourseEnvironmentAudience(
                environment_id=environment.id, class_id=class_id
            )
        )


def _replace_nodes(
    session: SessionDep,
    version: CourseEnvironmentVersion,
    nodes: list[EnvironmentNodeIn],
    edges: list[EnvironmentEdgeIn],
    publications: list[EnvironmentPublicationIn] | None = None,
) -> None:
    _validate_configuration(session, nodes, edges, publications)
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
        "version": version.version,
        "status": version.status,
        "configuration_hash": version.configuration_hash,
        "created_at": environment.created_at,
        "updated_at": environment.updated_at,
        "published_at": version.published_at,
        "classes": int(class_count or 0),
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
    _replace_nodes(session, version, body.nodes, body.edges, body.publications)
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
    _replace_nodes(session, version, body.nodes, body.edges, body.publications)
    session.add(environment)
    session.commit()
    return _serialize_version(session, environment, version)


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
    )
    payload: dict[str, Any] = {
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


@router.post("/{environment_id}/versions", status_code=201)
def create_environment_version(
    environment_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    environment = _get_environment(session, current_user, environment_id)
    latest = _latest(session, environment)
    if latest.status == CourseEnvironmentVersionStatus.draft:
        raise BadRequestError(t("course_env.draft_exists"))
    version = CourseEnvironmentVersion(
        environment_id=environment.id,
        version=latest.version + 1,
    )
    session.add(version)
    session.flush()
    for node in _nodes(session, latest.id):
        values = node.model_dump(
            exclude={"id", "version_id"},
        )
        session.add(CourseEnvironmentNode(version_id=version.id, **values))
    for edge in _edges(session, latest.id):
        values = edge.model_dump(exclude={"id", "version_id"})
        session.add(CourseEnvironmentEdge(version_id=version.id, **values))
    for publication in _publications(session, latest.id):
        values = publication.model_dump(exclude={"id", "version_id"})
        session.add(CourseEnvironmentPublication(version_id=version.id, **values))
    environment.updated_at = get_datetime_utc()
    session.add(environment)
    session.commit()
    return _serialize_version(session, environment, version)


@router.post("/{environment_id}/retire")
def retire_environment(
    environment_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> dict[str, Any]:
    """下架：停止新的啟動與套用，既有 Session 與班級不受影響。

    退役是版本層的動作，因為班級與練習 Session 都鎖在某一個版本上；把已發布
    版本改為 `retired` 之後，它就不再出現在學生清單與班級可選清單，但既有
    Session 仍照自己的期限走完。
    """
    environment = _get_environment(session, current_user, environment_id)
    versions = _versions(session, environment.id)
    published = [
        version
        for version in versions
        if version.status == CourseEnvironmentVersionStatus.published
    ]
    if not published:
        raise BadRequestError(t("course_env.no_published_version"))
    for version in published:
        version.status = CourseEnvironmentVersionStatus.retired
        session.add(version)
    environment.updated_at = get_datetime_utc()
    session.add(environment)
    session.commit()
    return _serialize_version(session, environment, _latest(session, environment))


def _environment_references(
    session: SessionDep, environment_id: uuid.UUID
) -> list[str]:
    """刪除前的引用盤點：有引用就不能硬刪，只能下架。"""
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
        reasons.append(
            t("course_env.reason_classes_using", count=int(class_count))
        )
    session_count = session.exec(
        select(func.count(col(QuickPracticeSession.id))).where(
            col(QuickPracticeSession.environment_version_id).in_(version_ids)
        )
    ).one()
    if int(session_count or 0):
        reasons.append(
            t("course_env.reason_sessions_using", count=int(session_count))
        )
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
