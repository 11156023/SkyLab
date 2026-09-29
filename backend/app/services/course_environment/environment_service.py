"""課程環境（老師管理、有版本、每位學生一份）的業務邏輯。

路由只負責權限與參數，這裡處理設定驗證、機器／連線／對外服務的落地、
序列化、發布雜湊、引用盤點與環境文件。
"""

import copy
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from pydantic import ValidationError
from sqlmodel import Session, col, delete, func, select

from app.core.authorizers import (
    can_bypass_teaching_ownership,
    require_teaching_access,
)
from app.core.i18n import t
from app.core.permissions import is_admin
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
from app.models.base import get_datetime_utc
from app.repositories import vm_template as vm_template_repo
from app.schemas.course_environment import (
    EnvironmentBasicsIn,
    EnvironmentCreate,
    EnvironmentDraftIn,
    EnvironmentEdgeIn,
    EnvironmentNodeIn,
    EnvironmentPublicationIn,
)
from app.services import quick_practice
from app.services.course_environment import upload_store
from app.services.proxmox import proxmox_service
from app.services.teaching import course_publication_service
from app.services.teaching.class_network_service import network_segments

# 與班級任務檔同一套做法：檔案落在 data/ 底下，資料庫只存 storage_key
ENVIRONMENT_FILE_ROOT = (
    Path(__file__).resolve().parents[3] / "data" / "course-environment-files"
)
MAX_ENVIRONMENT_FILE_BYTES = 50 * 1024 * 1024


# --- 查詢與權限 ----------------------------------------------------------------


def get_environment(
    session: Session, current_user: User, environment_id: uuid.UUID
) -> CourseEnvironment:
    item = session.get(CourseEnvironment, environment_id)
    if item is None:
        raise NotFoundError(t("course_env.not_found"))
    require_teaching_access(current_user, item.owner_id)
    return item


def environment_owner(
    session: Session, environment: CourseEnvironment, current_user: User
) -> User:
    """來源可見性一律以環境擁有者判斷。

    管理員代為編輯或發布別人的環境時，不能放進擁有者自己看不到的範本；
    否則擁有者之後開課時就能整班複製一份原本無權使用的範本。
    """
    if environment.owner_id == current_user.id:
        return current_user
    return session.get(User, environment.owner_id) or current_user


def list_versions(
    session: Session, environment_id: uuid.UUID
) -> list[CourseEnvironmentVersion]:
    return list(
        session.exec(
            select(CourseEnvironmentVersion)
            .where(CourseEnvironmentVersion.environment_id == environment_id)
            .order_by(col(CourseEnvironmentVersion.version).desc())
        ).all()
    )


def latest_version(
    session: Session, environment: CourseEnvironment
) -> CourseEnvironmentVersion:
    versions = list_versions(session, environment.id)
    if not versions:
        raise NotFoundError(t("course_env.version_not_found"))
    return versions[0]


def list_edges(session: Session, version_id: uuid.UUID) -> list[CourseEnvironmentEdge]:
    return list(
        session.exec(
            select(CourseEnvironmentEdge).where(
                CourseEnvironmentEdge.version_id == version_id
            )
        ).all()
    )


def list_files(
    session: Session, environment_id: uuid.UUID
) -> list[CourseEnvironmentFile]:
    return list(
        session.exec(
            select(CourseEnvironmentFile)
            .where(CourseEnvironmentFile.environment_id == environment_id)
            .order_by(col(CourseEnvironmentFile.created_at))
        ).all()
    )


# --- 設定驗證與落地 ------------------------------------------------------------


def validate_custom_source(
    session: Session, node: EnvironmentNodeIn, owner: User
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


def validate_configuration(
    session: Session,
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
            validate_custom_source(session, node, owner)
            continue
        template = session.get(VMTemplate, node.source_template_id)
        # 範本 UUID 不是秘密（快速練習清單就看得到），所以和自訂來源一樣要
        # 確認擁有者看得到這份範本；訊息不區分「不存在」與「看不到」。
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


def replace_nodes(
    session: Session,
    version: CourseEnvironmentVersion,
    nodes: list[EnvironmentNodeIn],
    edges: list[EnvironmentEdgeIn],
    publications: list[EnvironmentPublicationIn] | None = None,
    *,
    owner: User,
) -> None:
    validate_configuration(session, nodes, edges, publications, owner=owner)
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


def assign_environment_fields(
    environment: CourseEnvironment, body: EnvironmentCreate
) -> None:
    """把表單的名稱、用途與提供方式寫到環境列上。"""
    environment.name = body.name.strip()
    environment.description = body.description
    environment.usage_scope = body.usage_scope
    environment.max_concurrent_sessions = body.max_concurrent_sessions


def replace_configuration(
    session: Session,
    version: CourseEnvironmentVersion,
    body: EnvironmentCreate,
    *,
    owner: User,
) -> None:
    """依表單改寫機器／連線／對外服務與互通政策；不 flush、不 commit。

    ``owner``：來源範本的可見性以這位使用者判斷。
    """
    replace_nodes(
        session, version, body.nodes, body.edges, body.publications, owner=owner
    )
    version.peer_policy = body.peer_policy


def config_row(model: Any) -> dict[str, Any]:
    """發布雜湊用：去掉資料列自己的 id 與所屬版本，只留設定內容。"""
    return {
        key: value
        for key, value in model.model_dump().items()
        if key not in {"id", "version_id"}
    }


def configuration_hash(
    version: CourseEnvironmentVersion,
    nodes: list[Any],
    edges: list[Any],
    publications: list[Any],
) -> str:
    payload: dict[str, Any] = {
        "peer_policy": version.peer_policy,
        "nodes": [config_row(node) for node in nodes],
        "edges": [config_row(edge) for edge in edges],
        "publications": [config_row(item) for item in publications],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


# --- 序列化 --------------------------------------------------------------------


def serialize_version(
    session: Session,
    environment: CourseEnvironment,
    version: CourseEnvironmentVersion,
) -> dict[str, Any]:
    nodes = quick_practice.nodes_for_version(session, version_id=version.id)
    edges = list_edges(session, version.id)
    publications = course_publication_service.list_for_version(
        session, version_id=version.id
    )
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
        "max_concurrent_sessions": environment.max_concurrent_sessions,
        "files": [
            {
                "id": item.id,
                "filename": item.filename,
                "size_bytes": item.size_bytes,
                "created_at": item.created_at,
            }
            for item in list_files(session, environment.id)
        ],
        "version": version.version,
        "status": version.status,
        "configuration_hash": version.configuration_hash,
        "draft_data": version.draft_data or None,
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
            # 與班級容量計算同一套拆法（逗號與 / 都算分隔）
            "network_count": len(
                {name for node in nodes for name in network_segments(node.network)}
            ),
        },
    }


def serialize_latest(
    session: Session, environment: CourseEnvironment
) -> dict[str, Any]:
    return serialize_version(session, environment, latest_version(session, environment))


# --- 列表 ----------------------------------------------------------------------


def list_environments(session: Session, current_user: User) -> list[dict[str, Any]]:
    query = select(CourseEnvironment).order_by(col(CourseEnvironment.updated_at).desc())
    if not can_bypass_teaching_ownership(current_user):
        query = query.where(CourseEnvironment.owner_id == current_user.id)
    return [
        serialize_latest(session, environment)
        for environment in session.exec(query).all()
    ]


def list_published_environments(
    session: Session, current_user: User
) -> list[dict[str, Any]]:
    result = []
    query = (
        select(CourseEnvironment)
        .where(col(CourseEnvironment.usage_scope).in_(["course", "both"]))
        .order_by(col(CourseEnvironment.updated_at).desc())
    )
    if not can_bypass_teaching_ownership(current_user):
        query = query.where(CourseEnvironment.owner_id == current_user.id)
    for environment in session.exec(query).all():
        versions = list_versions(session, environment.id)
        published = next(
            (
                version
                for version in versions
                if version.status == CourseEnvironmentVersionStatus.published
            ),
            None,
        )
        if published:
            result.append(serialize_version(session, environment, published))
    return result


# --- 草稿與編輯 ----------------------------------------------------------------


def create_draft(
    session: Session, owner: User, body: EnvironmentDraftIn
) -> dict[str, Any]:
    # 新資料列的 id 一律由伺服器產生
    environment = CourseEnvironment(id=uuid.uuid4(), owner_id=owner.id, name="")
    version = CourseEnvironmentVersion(
        environment_id=environment.id, version=1, draft_data=body.model_dump(mode="json")
    )
    session.add(environment)
    session.add(version)
    session.commit()
    return serialize_version(session, environment, version)


def save_draft(
    session: Session, environment: CourseEnvironment, body: EnvironmentDraftIn
) -> dict[str, Any]:
    version = latest_version(session, environment)
    if version.status != CourseEnvironmentVersionStatus.draft:
        raise BadRequestError(t("course_env.published_immutable"))
    version.draft_data = body.model_dump(mode="json")
    environment.updated_at = get_datetime_utc()
    session.add(version)
    session.add(environment)
    session.commit()
    return serialize_version(session, environment, version)


def create_environment(
    session: Session, owner: User, body: EnvironmentCreate
) -> dict[str, Any]:
    environment = CourseEnvironment(
        owner_id=owner.id,
        name=body.name.strip(),
        description=body.description,
        usage_scope=body.usage_scope,
        max_concurrent_sessions=body.max_concurrent_sessions,
    )
    version = CourseEnvironmentVersion(environment_id=environment.id, version=1)
    session.add(environment)
    session.add(version)
    session.flush()
    replace_configuration(session, version, body, owner=owner)
    session.commit()
    return serialize_version(session, environment, version)


def update_environment(
    session: Session,
    environment: CourseEnvironment,
    body: EnvironmentCreate,
    current_user: User,
) -> dict[str, Any]:
    version = latest_version(session, environment)
    if version.status != CourseEnvironmentVersionStatus.draft:
        raise BadRequestError(t("course_env.published_immutable"))
    assign_environment_fields(environment, body)
    environment.updated_at = get_datetime_utc()
    replace_configuration(
        session,
        version,
        body,
        owner=environment_owner(session, environment, current_user),
    )
    version.draft_data = None
    session.add(version)
    session.add(environment)
    session.commit()
    return serialize_version(session, environment, version)


def update_basics(
    session: Session, environment: CourseEnvironment, body: EnvironmentBasicsIn
) -> dict[str, Any]:
    """調整名稱、用途與提供方式，不需要開新版本。

    改成不提供給學生只影響「還沒啟動」的人：已經在跑的練習 Session 照自己的
    期限走完。若最新版本還帶著草稿快照，連草稿一起改，免得之後發布又把這次
    的調整蓋回去。
    """
    environment.name = body.name.strip()
    environment.description = body.description
    environment.usage_scope = body.usage_scope
    version = latest_version(session, environment)
    if version.draft_data:
        # 複製一份再改：JSON 欄位就地修改不會被 ORM 偵測到，不會寫回
        draft = copy.deepcopy(version.draft_data)
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
        version.draft_data = draft
        session.add(version)
    environment.updated_at = get_datetime_utc()
    session.add(environment)
    session.commit()
    return serialize_version(session, environment, version)


# --- 發布 ----------------------------------------------------------------------


def publish(
    session: Session, environment: CourseEnvironment, current_user: User
) -> dict[str, Any]:
    owner = environment_owner(session, environment, current_user)
    version = latest_version(session, environment)
    if version.status != CourseEnvironmentVersionStatus.draft:
        raise BadRequestError(t("course_env.only_draft_publishable"))
    if version.draft_data:
        draft = EnvironmentDraftIn.model_validate(version.draft_data)
        try:
            body = EnvironmentCreate.model_validate(draft.configuration)
            if not body.name.strip():
                raise ValueError("Environment name is required")
        except (ValidationError, ValueError) as exc:
            raise BadRequestError(str(exc)) from exc
        assign_environment_fields(environment, body)
        replace_configuration(session, version, body, owner=owner)
        session.flush()
        version.draft_data = None
    nodes = quick_practice.nodes_for_version(session, version_id=version.id)
    edges = list_edges(session, version.id)
    publications = course_publication_service.list_for_version(
        session, version_id=version.id
    )
    validate_configuration(
        session,
        [EnvironmentNodeIn.model_validate(node.model_dump()) for node in nodes],
        [EnvironmentEdgeIn.model_validate(edge.model_dump()) for edge in edges],
        [
            EnvironmentPublicationIn.model_validate(item.model_dump())
            for item in publications
        ],
        owner=owner,
    )
    version.configuration_hash = configuration_hash(
        version, nodes, edges, publications
    )
    version.status = CourseEnvironmentVersionStatus.published
    version.published_at = get_datetime_utc()
    environment.updated_at = get_datetime_utc()
    session.add(version)
    session.add(environment)
    session.commit()
    return serialize_version(session, environment, version)


# --- 環境文件 ------------------------------------------------------------------


def remove_file_blob(storage_key: str | None) -> None:
    """刪掉磁碟上的環境文件；storage_key 一律當成 ENVIRONMENT_FILE_ROOT 底下的相對路徑。"""
    upload_store.remove_blob(ENVIRONMENT_FILE_ROOT, storage_key)


async def add_file(
    session: Session,
    environment: CourseEnvironment,
    file: UploadFile,
    uploaded_by: User,
) -> dict[str, Any]:
    """文件掛在環境身分上，換版本不會讓講義跟著消失。"""
    filename = upload_store.sanitize_upload_filename(
        file.filename,
        default="file",
        invalid_message=t("course_env.file_name_invalid"),
        too_long_message=t("course_env.file_name_too_long"),
    )
    file_id, storage_key, written = await upload_store.save_upload(
        file,
        root=ENVIRONMENT_FILE_ROOT,
        suffix=".bin",
        max_bytes=MAX_ENVIRONMENT_FILE_BYTES,
        too_large_message=t("course_env.file_too_large"),
    )

    session.add(
        CourseEnvironmentFile(
            id=file_id,
            environment_id=environment.id,
            filename=filename,
            storage_key=storage_key,
            size_bytes=written,
            uploaded_by=uploaded_by.id,
        )
    )
    environment.updated_at = get_datetime_utc()
    session.add(environment)
    session.commit()
    return serialize_version(
        session, environment, latest_version(session, environment)
    )


def get_environment_file(
    session: Session,
    current_user: User,
    environment_id: uuid.UUID,
    file_id: uuid.UUID,
) -> tuple[CourseEnvironment, CourseEnvironmentFile]:
    environment = get_environment(session, current_user, environment_id)
    item = session.get(CourseEnvironmentFile, file_id)
    if item is None or item.environment_id != environment.id:
        raise NotFoundError(t("course_env.file_not_found"))
    return environment, item


def stored_file_path(item: CourseEnvironmentFile) -> Path:
    root = ENVIRONMENT_FILE_ROOT.resolve()
    stored = (root / item.storage_key).resolve()
    # storage_key 由伺服器產生，但仍然擋一次路徑跳脫，免得日後有人改成沿用檔名
    if not stored.is_relative_to(root) or not stored.is_file():
        raise NotFoundError(t("course_env.file_not_found"))
    return stored


def delete_file(
    session: Session, environment: CourseEnvironment, item: CourseEnvironmentFile
) -> dict[str, Any]:
    storage_key = item.storage_key
    session.delete(item)
    environment.updated_at = get_datetime_utc()
    session.add(environment)
    session.commit()
    remove_file_blob(storage_key)
    return serialize_version(
        session, environment, latest_version(session, environment)
    )


# --- 刪除 ----------------------------------------------------------------------


def environment_references(session: Session, environment_id: uuid.UUID) -> list[str]:
    """刪除前的引用盤點：有引用就不能硬刪，只能把提供方式收起來。"""
    version_ids = [version.id for version in list_versions(session, environment_id)]
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


def delete_environment(session: Session, environment: CourseEnvironment) -> None:
    """硬刪除；只允許沒有任何引用的環境，其餘一律走下架。"""
    reasons = environment_references(session, environment.id)
    if reasons:
        raise BadRequestError(
            t("course_env.delete_blocked", reasons="、".join(reasons))
        )
    # 文件列會跟著 FK CASCADE 消失，磁碟上的檔案要在 commit 後自己收
    removed_storage_keys = [
        item.storage_key for item in list_files(session, environment.id)
    ]
    version_ids = [version.id for version in list_versions(session, environment.id)]
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
    session.delete(environment)
    session.commit()
    for storage_key in removed_storage_keys:
        remove_file_blob(storage_key)
