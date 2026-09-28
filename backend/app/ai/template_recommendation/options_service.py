"""AI 範本推薦的候選資源清單（GPU、節點、OS／應用範本）與其短期快取。

這裡全是同步的 PVE／DB 呼叫；路由一律用 ``asyncio.to_thread`` 丟到 worker
thread 執行，不可在 event loop 上直接呼叫。
"""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from time import monotonic
from typing import Any

from sqlmodel import Session

from app.ai.template_recommendation.node_service import (
    build_resource_option_bundle,
    load_live_device_nodes,
)
from app.ai.template_recommendation.schemas import ChatRequest
from app.core.permissions import Permission, has_permission
from app.models import User
from app.repositories import vm_template as vm_template_repo
from app.schemas.gpu import GPUSummary
from app.services.proxmox import gpu_service
from app.services.template import template_service

logger = logging.getLogger(__name__)

GPU_OPTIONS_CACHE_TTL_SECONDS = 20.0
LIVE_NODES_CACHE_TTL_SECONDS = 15.0
RESOURCE_OPTIONS_CACHE_TTL_SECONDS = 300.0
# 應用範本目錄讀取失敗（PVE 掛掉）時也短暫快取空清單，避免每個請求都重打 PVE
APPLICATION_TEMPLATES_FAILURE_TTL_SECONDS = 30.0
_gpu_options_cache: dict[str, Any] = {"at": 0.0, "items": []}
_live_nodes_cache: dict[str, Any] = {"at": 0.0, "items": []}
_base_resource_options_cache: dict[str, Any] = {"at": 0.0, "items": None}
_application_templates_cache: dict[str, Any] = {"at": 0.0, "items": None}

_GPU_KEYWORDS = (
    "gpu",
    "vram",
    "cuda",
    "nvidia",
    "pytorch",
    "tensorflow",
    "llm",
    "yolo",
    "訓練",
    "推理",
    "顯卡",
)


def latest_user_text(request: ChatRequest) -> str:
    for message in reversed(request.messages):
        if str(message.role).strip().lower() == "user":
            return str(message.content or "")
    return ""


def should_include_gpu_runtime_context(request: ChatRequest) -> bool:
    form_context = request.form_context
    if form_context and (
        (form_context.resource_type and str(form_context.resource_type).lower() == "vm")
        or form_context.selected_gpu_mapping_id
    ):
        return True

    text = latest_user_text(request).lower()
    return any(keyword in text for keyword in _GPU_KEYWORDS)


def get_base_gpu_options_cached() -> list[dict[str, Any]]:
    now = monotonic()
    cached_at = float(_gpu_options_cache.get("at") or 0.0)
    cached_items = list(_gpu_options_cache.get("items") or [])
    if cached_items and (now - cached_at) <= GPU_OPTIONS_CACHE_TTL_SECONDS:
        return [dict(item) for item in cached_items]

    fresh_items = [
        item.model_dump(mode="json") for item in gpu_service.list_gpu_options()
    ]
    _gpu_options_cache["at"] = now
    _gpu_options_cache["items"] = fresh_items
    return [dict(item) for item in fresh_items]


def get_live_device_nodes_cached() -> list[Any]:
    now = monotonic()
    cached_at = float(_live_nodes_cache.get("at") or 0.0)
    cached_items = list(_live_nodes_cache.get("items") or [])
    if cached_at > 0 and (now - cached_at) <= LIVE_NODES_CACHE_TTL_SECONDS:
        return [item.model_copy() for item in cached_items]

    fresh_items = load_live_device_nodes()
    _live_nodes_cache["at"] = now
    _live_nodes_cache["items"] = fresh_items
    return [item.model_copy() for item in fresh_items]


async def get_live_device_nodes_safely() -> list[Any]:
    try:
        return await asyncio.to_thread(get_live_device_nodes_cached)
    except Exception as exc:
        logger.warning("Unable to refresh live nodes for AI recommendation: %s", exc)
        return []


def get_base_resource_options_cached() -> dict[str, Any]:
    now = monotonic()
    cached_at = float(_base_resource_options_cache.get("at") or 0.0)
    cached_items = _base_resource_options_cache.get("items")
    if (
        cached_items is not None
        and (now - cached_at) <= RESOURCE_OPTIONS_CACHE_TTL_SECONDS
    ):
        return deepcopy(cached_items)

    # bundle 的 gpu_options 一律是空清單，實際 GPU 由 build_resource_options_with_gpu 併入
    fresh_items = build_resource_option_bundle()
    _base_resource_options_cache["at"] = now
    _base_resource_options_cache["items"] = fresh_items
    return deepcopy(fresh_items)


def build_resource_options_with_gpu(
    gpu_options: list[dict[str, Any]],
) -> dict[str, Any]:
    resource_options = get_base_resource_options_cached()
    resource_options["gpu_options"] = [dict(item) for item in gpu_options]
    return resource_options


def get_application_templates_cached(session: Session) -> list[dict[str, Any]]:
    """已開放的應用範本目錄。

    目錄與使用者無關（開放與否是範本自己的旗標），所以整個程序共用一份快取；
    來源一律由伺服器決定，不採用客戶端送來的清單，否則模型的候選會變成前端
    可以偽造的東西。
    """
    now = monotonic()
    cached_at = float(_application_templates_cache.get("at") or 0.0)
    cached_items = _application_templates_cache.get("items")
    cached_ttl = float(
        _application_templates_cache.get("ttl") or RESOURCE_OPTIONS_CACHE_TTL_SECONDS
    )
    if cached_items is not None and (now - cached_at) <= cached_ttl:
        return deepcopy(cached_items)
    try:
        catalog = template_service.list_student_catalog(session=session)
    except Exception as exc:  # PVE 失敗不該擋住建議
        logger.warning("Unable to load the application template catalog: %s", exc)
        _application_templates_cache["at"] = now
        _application_templates_cache["items"] = []
        _application_templates_cache["ttl"] = APPLICATION_TEMPLATES_FAILURE_TTL_SECONDS
        return []
    items = [
        {
            "template_id": item.pve_vmid,
            "name": item.name,
            "description": item.description or "",
            "resource_type": item.resource_type,
            "cores": item.cores,
            "memory_mb": item.memory_mb,
            "disk_gb": item.disk_gb,
        }
        for item in catalog
    ]
    _application_templates_cache["at"] = now
    _application_templates_cache["items"] = items
    _application_templates_cache["ttl"] = RESOURCE_OPTIONS_CACHE_TTL_SECONDS
    return deepcopy(items)


def allowed_vm_template_ids(
    session: Session,
    user: User,
    application_templates: list[dict[str, Any]],
) -> set[int]:
    """使用者實際可以拿來申請的 VM 來源 id（PVE 讀不到時回空集合）。"""
    base = get_base_resource_options_cached().get("vm_operating_systems") or []
    if not base:
        return set()
    allowed = {int(item.get("template_id") or 0) for item in base}
    if not has_permission(user, Permission.TEMPLATE_MANAGE):
        allowed -= vm_template_repo.registered_pve_vmids(session=session)
    allowed |= {
        int(item.get("template_id") or 0)
        for item in application_templates
        if str(item.get("resource_type")) != "lxc"
    }
    return allowed


def resolve_resource_options(
    request: ChatRequest,
    gpu_options: list[dict[str, Any]],
    session: Session,
    user: User,
) -> dict[str, Any]:
    """候選清單必須跟使用者實際能選的一致。

    母範本同時也是 PVE template，所以伺服器端組清單時要濾掉已註冊的範本，
    再把開放申請的應用範本以獨立清單交給模型；否則模型會推薦到使用者根本
    申請不到（甚至看不到）的來源。
    """
    form_context = request.form_context
    application_templates = get_application_templates_cached(session)
    if form_context and form_context.resource_options_from_client:
        client_vm_options = [
            item.model_dump(mode="json") for item in form_context.vm_os_options
        ]
        allowed_vm_ids = allowed_vm_template_ids(session, user, application_templates)
        if allowed_vm_ids:
            client_vm_options = [
                item
                for item in client_vm_options
                if int(item.get("template_id") or 0) in allowed_vm_ids
            ]
        return {
            "lxc_os_images": [
                item.model_dump(mode="json") for item in form_context.lxc_os_options
            ],
            "vm_operating_systems": client_vm_options,
            "application_templates": application_templates,
            "gpu_options": [dict(item) for item in gpu_options],
        }
    resource_options = build_resource_options_with_gpu(gpu_options)
    if not has_permission(user, Permission.TEMPLATE_MANAGE):
        registered = vm_template_repo.registered_pve_vmids(session=session)
        resource_options["vm_operating_systems"] = [
            item
            for item in resource_options.get("vm_operating_systems") or []
            if int(item.get("template_id") or 0) not in registered
        ]
    resource_options["application_templates"] = application_templates
    return resource_options


def resolve_recommend_gpu_options(
    request: ChatRequest, *, requires_gpu: bool
) -> list[dict[str, Any]]:
    form_context = request.form_context
    if form_context and form_context.gpu_options:
        return [item.model_dump(mode="json") for item in form_context.gpu_options]

    selected_gpu_mapping_id = (
        form_context.selected_gpu_mapping_id if form_context else None
    )
    if not requires_gpu and not selected_gpu_mapping_id:
        return []

    return get_base_gpu_options_cached()


def resolve_chat_gpu_options(
    request: ChatRequest, session: Session
) -> list[dict[str, Any]]:
    if not should_include_gpu_runtime_context(request):
        return []

    # 20 秒快取只留在這裡：/gpu/options 直接查 PVE，不共用這份快取
    options = get_base_gpu_options_cached()
    form_context = request.form_context
    if not form_context or not form_context.start_at or not form_context.end_at:
        return options

    adjusted = gpu_service.apply_reservation_window(
        session,
        [GPUSummary.model_validate(option) for option in options],
        start_at=form_context.start_at,
        end_at=form_context.end_at,
    )
    return [option.model_dump(mode="json") for option in adjusted]
