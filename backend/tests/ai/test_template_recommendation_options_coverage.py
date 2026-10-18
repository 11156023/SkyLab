"""Coverage for app.ai.template_recommendation options_service / node_service / config."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.template_recommendation import config as tr_config
from app.ai.template_recommendation import node_service, options_service
from app.ai.template_recommendation.schemas import (
    ChatMessage,
    ChatRequest,
    DeviceNode,
    GPUOptionContext,
    LXCOSOptionContext,
    RecommendationFormContext,
    VMOSOptionContext,
)
from app.schemas.gpu import GPUSummary


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    options_service._gpu_options_cache.clear()
    options_service._gpu_options_cache.update({"at": 0.0, "items": []})
    options_service._live_nodes_cache.clear()
    options_service._live_nodes_cache.update({"at": 0.0, "items": []})
    options_service._base_resource_options_cache.clear()
    options_service._base_resource_options_cache.update({"at": 0.0, "items": None})
    options_service._application_templates_cache.clear()
    options_service._application_templates_cache.update({"at": 0.0, "items": None})
    yield
    options_service._gpu_options_cache.update({"at": 0.0, "items": []})
    options_service._live_nodes_cache.update({"at": 0.0, "items": []})
    options_service._base_resource_options_cache.update({"at": 0.0, "items": None})
    options_service._application_templates_cache.clear()
    options_service._application_templates_cache.update({"at": 0.0, "items": None})


class _Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _request(
    messages: list[tuple[str, str]] | None = None,
    form_context: RecommendationFormContext | None = None,
) -> ChatRequest:
    msgs = messages or [("user", "hello")]
    return ChatRequest(
        messages=[ChatMessage(role=r, content=c) for r, c in msgs],
        form_context=form_context,
    )


# ---------------------------------------------------------------- caches


def test_gpu_options_cache_fresh_then_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(options_service, "monotonic", clock)
    calls: list[int] = []

    def fake_list() -> list[GPUSummary]:
        calls.append(1)
        return [GPUSummary(mapping_id="gpu-a")]

    monkeypatch.setattr(options_service.gpu_service, "list_gpu_options", fake_list)
    first = options_service.get_base_gpu_options_cached()
    assert first[0]["mapping_id"] == "gpu-a"
    clock.value += 5
    second = options_service.get_base_gpu_options_cached()
    assert second == first
    assert len(calls) == 1


def test_live_nodes_cache_fresh_then_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(options_service, "monotonic", clock)
    calls: list[int] = []

    def fake_load() -> list[DeviceNode]:
        calls.append(1)
        return [DeviceNode(node="pve1")]

    monkeypatch.setattr(options_service, "load_live_device_nodes", fake_load)
    first = options_service.get_live_device_nodes_cached()
    assert first[0].node == "pve1"
    clock.value += 1
    second = options_service.get_live_device_nodes_cached()
    assert second[0].node == "pve1"
    assert len(calls) == 1


async def test_live_nodes_safely_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        options_service,
        "get_live_device_nodes_cached",
        lambda: [DeviceNode(node="n1")],
    )
    result = await options_service.get_live_device_nodes_safely()
    assert result[0].node == "n1"

    def boom() -> list[Any]:
        raise RuntimeError("pve down")

    monkeypatch.setattr(options_service, "get_live_device_nodes_cached", boom)
    assert await options_service.get_live_device_nodes_safely() == []


def _bundle() -> dict[str, Any]:
    return {
        "lxc_os_images": [{"value": "local:vztmpl/debian.tar.zst", "label": "debian"}],
        "vm_operating_systems": [
            {"template_id": 9000, "label": "ubuntu", "node": "pve1"},
            {"template_id": 9001, "label": "registered", "node": "pve1"},
        ],
        "gpu_options": [],
    }


def test_base_resource_options_cache_and_with_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    monkeypatch.setattr(options_service, "monotonic", clock)
    calls: list[int] = []

    def fake_bundle() -> dict[str, Any]:
        calls.append(1)
        return _bundle()

    monkeypatch.setattr(options_service, "build_resource_option_bundle", fake_bundle)
    first = options_service.get_base_resource_options_cached()
    first["lxc_os_images"].clear()  # deepcopy: mutation must not leak
    second = options_service.get_base_resource_options_cached()
    assert len(second["lxc_os_images"]) == 1
    assert len(calls) == 1

    merged = options_service.build_resource_options_with_gpu([{"mapping_id": "g"}])
    assert merged["gpu_options"] == [{"mapping_id": "g"}]


def _catalog_item(**overrides: Any) -> SimpleNamespace:
    data = {
        "pve_vmid": 7000,
        "name": "WordPress",
        "description": None,
        "resource_type": "vm",
        "cores": 2,
        "memory_mb": 2048,
        "disk_gb": 20,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_application_templates_cache_success_and_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    monkeypatch.setattr(options_service, "monotonic", clock)
    calls: list[int] = []

    def fake_catalog(*, session: Any) -> list[SimpleNamespace]:
        calls.append(1)
        return [_catalog_item(), _catalog_item(pve_vmid=7001, description="x")]

    monkeypatch.setattr(
        options_service.template_service, "list_student_catalog", fake_catalog
    )
    items = options_service.get_application_templates_cached(object())
    assert items[0]["description"] == ""
    assert items[1]["description"] == "x"
    clock.value += 10
    again = options_service.get_application_templates_cached(object())
    assert again == items
    assert len(calls) == 1
    assert (
        options_service._application_templates_cache["ttl"]
        == options_service.RESOURCE_OPTIONS_CACHE_TTL_SECONDS
    )


# ---------------------------------------------------------------- allowed ids


def test_allowed_vm_template_ids_empty_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        options_service,
        "get_base_resource_options_cached",
        lambda: {"vm_operating_systems": []},
    )
    assert options_service.allowed_vm_template_ids(object(), object(), []) == set()


def test_allowed_vm_template_ids_student_and_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(options_service, "get_base_resource_options_cached", _bundle)
    monkeypatch.setattr(
        options_service.vm_template_repo,
        "registered_pve_vmids",
        lambda *, session: {9001},
    )
    apps = [
        {"template_id": 7000, "resource_type": "vm"},
        {"template_id": 7100, "resource_type": "lxc"},
    ]
    monkeypatch.setattr(options_service, "has_permission", lambda u, p: False)
    assert options_service.allowed_vm_template_ids(object(), object(), apps) == {
        9000,
        7000,
    }
    monkeypatch.setattr(options_service, "has_permission", lambda u, p: True)
    assert options_service.allowed_vm_template_ids(object(), object(), apps) == {
        9000,
        9001,
        7000,
    }


# ---------------------------------------------------------------- resolve


def test_resolve_resource_options_from_client_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps = [{"template_id": 7000, "resource_type": "vm"}]
    monkeypatch.setattr(
        options_service, "get_application_templates_cached", lambda session: apps
    )
    monkeypatch.setattr(
        options_service, "allowed_vm_template_ids", lambda s, u, a: {9000}
    )
    ctx = RecommendationFormContext(
        resource_options_from_client=True,
        vm_os_options=[
            VMOSOptionContext(template_id=9000, label="ok"),
            VMOSOptionContext(template_id=9999, label="hidden"),
        ],
        lxc_os_options=[LXCOSOptionContext(value="local:vztmpl/x", label="x")],
    )
    result = options_service.resolve_resource_options(
        _request(form_context=ctx), [{"mapping_id": "g"}], object(), object()
    )
    assert [i["template_id"] for i in result["vm_operating_systems"]] == [9000]
    assert result["lxc_os_images"][0]["value"] == "local:vztmpl/x"
    assert result["application_templates"] == apps
    assert result["gpu_options"] == [{"mapping_id": "g"}]


def test_resolve_resource_options_server_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        options_service, "get_application_templates_cached", lambda session: []
    )
    monkeypatch.setattr(options_service, "get_base_resource_options_cached", _bundle)
    monkeypatch.setattr(
        options_service.vm_template_repo,
        "registered_pve_vmids",
        lambda *, session: {9001},
    )
    monkeypatch.setattr(options_service, "has_permission", lambda u, p: False)
    student = options_service.resolve_resource_options(
        _request(), [], object(), object()
    )
    assert [i["template_id"] for i in student["vm_operating_systems"]] == [9000]
    assert student["application_templates"] == []

    monkeypatch.setattr(options_service, "has_permission", lambda u, p: True)
    manager = options_service.resolve_resource_options(
        _request(), [], object(), object()
    )
    assert len(manager["vm_operating_systems"]) == 2


def test_resolve_recommend_gpu_options_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object()
    assert (
        options_service.resolve_recommend_gpu_options(
            _request(), session, requires_gpu=False
        )
        == []
    )
    monkeypatch.setattr(
        options_service,
        "get_base_gpu_options_cached",
        lambda: [{"mapping_id": "base"}],
    )
    assert options_service.resolve_recommend_gpu_options(
        _request(), session, requires_gpu=True
    ) == [{"mapping_id": "base"}]
    sel = RecommendationFormContext(selected_gpu_mapping_id="base")
    assert options_service.resolve_recommend_gpu_options(
        _request(form_context=sel), session, requires_gpu=False
    ) == [{"mapping_id": "base"}]

    # 前端帶來的清單不採用：可能過期，也沒扣掉預約
    ctx = RecommendationFormContext(gpu_options=[GPUOptionContext(mapping_id="c")])
    assert options_service.resolve_recommend_gpu_options(
        _request(form_context=ctx), session, requires_gpu=True
    ) == [{"mapping_id": "base"}]


def test_resolve_recommend_gpu_options_treats_pve_failure_as_no_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing() -> list[dict[str, Any]]:
        raise RuntimeError("PVE down")

    monkeypatch.setattr(options_service, "get_base_gpu_options_cached", failing)
    assert (
        options_service.resolve_recommend_gpu_options(
            _request(), object(), requires_gpu=True
        )
        == []
    )


# ---------------------------------------------------------------- node_service


def test_load_live_device_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        type(node_service.settings),
        "parsed_backend_node_gpu_map",
        property(lambda self: {"pve1": 2}),
    )
    monkeypatch.setattr(
        node_service.proxmox_service,
        "list_nodes",
        lambda: [
            {
                "node": "pve1",
                "maxcpu": 8,
                "cpu": 0.25,
                "maxmem": 8 * 1024**3,
                "mem": 2 * 1024**3,
            },
            {},
        ],
    )
    nodes = node_service.load_live_device_nodes()
    assert nodes[0].node == "pve1"
    assert nodes[0].gpu_count == 2
    assert nodes[0].maxmem_gb == 8.0
    assert nodes[0].mem_usage_ratio == 0.25
    assert nodes[1].node == "unknown"
    assert nodes[1].maxmem_gb == 0.0
    assert nodes[1].mem_usage_ratio == 0.0


def test_summarize_device_nodes() -> None:
    summary = node_service.summarize_device_nodes(
        [
            DeviceNode(
                node="a",
                maxcpu=4,
                cpu_usage_ratio=0.5,
                maxmem_gb=8,
                mem_usage_ratio=0.5,
                gpu_count=1,
            ),
            DeviceNode(node="b", maxcpu=2, maxmem_gb=2, gpu_count=0),
        ]
    )
    assert summary["free_cpu"] == 4
    assert summary["free_memory_gb"] == 6.0
    assert summary["gpu_count"] == 1
    assert [n["node"] for n in summary["nodes"]] == ["a", "b"]


# ---------------------------------------------------------------- config


def test_template_recommendation_settings_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = SimpleNamespace(
        backend_node_gpu_map={"a": "2", "b": "x", "c": -3, "d": None},
        vllm=SimpleNamespace(min_p=0.05, presence_penalty=None),
    )
    monkeypatch.setattr(
        tr_config,
        "system_ai_config",
        SimpleNamespace(template_recommendation=section),
    )
    settings = tr_config.TemplateRecommendationSettings()
    assert settings.section is section
    assert settings.parsed_backend_node_gpu_map == {"a": 2, "c": 0}
    assert settings.VLLM_MIN_P == 0.05
    assert settings.VLLM_PRESENCE_PENALTY == 0.0
    section.vllm.presence_penalty = 1.5
    assert settings.VLLM_PRESENCE_PENALTY == 1.5
