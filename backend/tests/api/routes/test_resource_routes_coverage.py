"""資源相關路由（resource_details、resources、vm_requests、gpu、lxc、vm、quotas、spec_change_requests、mining_incidents、jobs）的直接呼叫測試。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.api.routes import gpu as gpu_route
from app.api.routes import jobs as jobs_route
from app.api.routes import lxc as lxc_route
from app.api.routes import mining_incidents as mining_route
from app.api.routes import quotas as quotas_route
from app.api.routes import resource_details as details_route
from app.api.routes import resources as resources_route
from app.api.routes import spec_change_requests as spec_route
from app.api.routes import vm as vm_route
from app.api.routes import vm_requests as vm_requests_route
from app.exceptions import BadRequestError, ProxmoxError
from app.models import MiningIncidentStatus
from app.schemas.jobs import JobKind
from app.services.jobs.jobs_service import JobAccessDeniedError, JobNotFoundError

_USER = SimpleNamespace(id=uuid.uuid4(), email="user@example.com")
_NOW = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)


# ─── resource_details ────────────────────────────────────────────────────────


@pytest.mark.parametrize("has_template", [True, False])
def test_template_manual_lists_attachments(
    monkeypatch: pytest.MonkeyPatch, has_template: bool
) -> None:
    ownership = MagicMock()
    template_service = MagicMock()
    attachment = SimpleNamespace(
        id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        filename="manual.pdf",
        content_type="application/pdf",
        size_bytes=123,
        created_at=_NOW,
    )
    template = SimpleNamespace(name="Ubuntu 範本") if has_template else None
    template_service.get_manual_for_cloned_resource.return_value = (
        template,
        [attachment],
    )
    monkeypatch.setattr(details_route, "check_resource_ownership", ownership)
    monkeypatch.setattr(details_route, "template_service", template_service)
    session = MagicMock()

    result = details_route.get_template_manual(
        vmid=101, current_user=_USER, session=session
    )

    ownership.assert_called_once_with(101, _USER, session)
    assert result.count == 1
    assert result.data[0].filename == "manual.pdf"
    assert result.template_name == ("Ubuntu 範本" if has_template else None)


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [("application/pdf", "application/pdf"), (None, "application/octet-stream")],
)
def test_download_template_manual_returns_file(
    monkeypatch: pytest.MonkeyPatch, content_type: str | None, expected: str
) -> None:
    template_service = MagicMock()
    template_service.get_manual_attachment_for_cloned_resource.return_value = (
        "/tmp/stored.bin",
        SimpleNamespace(filename="手冊.pdf", content_type=content_type),
    )
    monkeypatch.setattr(details_route, "check_resource_ownership", MagicMock())
    monkeypatch.setattr(details_route, "template_service", template_service)

    response = details_route.download_template_manual(
        vmid=101,
        attachment_id=uuid.uuid4(),
        current_user=_USER,
        session=MagicMock(),
    )

    assert isinstance(response, FileResponse)
    assert response.filename == "手冊.pdf"
    assert response.media_type == expected


def test_resource_detail_stats_snapshots_spec_and_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource_service = MagicMock()
    resource_service.get_current_stats.return_value = {"status": "running", "cpu": 0.5}
    resource_service.get_rrd_stats.return_value = [
        {"time": 100, "cpu": 0.1, "mem": 2048},
        {"cpu": 0.2},
    ]
    snapshot_service = MagicMock()
    reset_service = MagicMock()
    reset_service.start_reset.return_value = "task-reset"
    monkeypatch.setattr(details_route, "resource_service", resource_service)
    monkeypatch.setattr(details_route, "snapshot_service", snapshot_service)
    monkeypatch.setattr(details_route, "reset_service", reset_service)
    info: dict[str, Any] = {"node": "pve1", "type": "qemu"}
    session = MagicMock()

    stats = details_route.get_current_stats(vmid=101, resource_info=info)
    assert stats.status == "running"
    assert stats.cpu == 0.5

    rrd = details_route.get_rrd_stats(vmid=101, resource_info=info, timeframe="day")
    assert rrd.timeframe == "day"
    assert [point.time for point in rrd.data] == [100, 0]
    assert rrd.data[0].mem == 2048

    assert (
        details_route.list_snapshots(vmid=101, resource_info=info)
        is snapshot_service.list_snapshots.return_value
    )

    request = SimpleNamespace(cores=2, memory=2048, disk_size=None)
    assert (
        details_route.direct_update_spec(
            vmid=101,
            request=request,
            resource_info=info,
            session=session,
            current_user=_USER,
        )
        is resource_service.direct_update_spec.return_value
    )
    assert resource_service.direct_update_spec.call_args.kwargs["memory"] == 2048

    accepted = details_route.reset_to_init(
        vmid=101, resource_info=info, session=session, current_user=_USER
    )
    assert accepted.task_id == "task-reset"


# ─── resources ───────────────────────────────────────────────────────────────


def test_resources_routes_delegate_to_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource_service = MagicMock()
    deletion_service = MagicMock()
    credentials_service = MagicMock()
    monkeypatch.setattr(resources_route, "resource_service", resource_service)
    monkeypatch.setattr(resources_route, "deletion_service", deletion_service)
    monkeypatch.setattr(resources_route, "credentials_service", credentials_service)
    session = MagicMock()
    info: dict[str, Any] = {"node": "pve1"}

    assert (
        resources_route.list_resources(session=session, current_user=_USER, node="pve1")
        is resource_service.list_all.return_value
    )
    assert (
        resources_route.list_my_resources(session=session, current_user=_USER)
        is resource_service.list_by_user.return_value
    )
    assert (
        resources_route.list_my_session_statuses(session=session, current_user=_USER)
        is resource_service.list_my_session_statuses.return_value
    )
    assert (
        resources_route.batch_action(
            body=SimpleNamespace(vmids=[1, 2], action="start"),
            session=session,
            current_user=_USER,
        )
        is resource_service.batch_action.return_value
    )
    assert (
        resources_route.get_resource(
            vmid=101, resource_info=info, session=session, current_user=_USER
        )
        is resource_service.annotate_access_for_user.return_value
    )
    assert (
        resource_service.annotate_access_for_user.call_args.kwargs["public"]
        is resource_service.get_by_vmid.return_value
    )
    assert (
        resources_route.get_resource_config(vmid=101, resource_info=info)
        is resource_service.get_config.return_value
    )

    for func, action in (
        (resources_route.start_resource, "start"),
        (resources_route.stop_resource, "stop"),
        (resources_route.reboot_resource, "reboot"),
        (resources_route.shutdown_resource, "shutdown"),
        (resources_route.reset_resource, "reset"),
    ):
        resource_service.control.reset_mock()
        func(vmid=101, resource_info=info, session=session, current_user=_USER)
        assert resource_service.control.call_args.kwargs["action"] == action

    assert (
        resources_route.get_session_status(
            vmid=101, resource_info=info, session=session, _current_user=_USER
        )
        is resource_service.get_session_status.return_value
    )
    assert (
        resources_route.extend_session(
            vmid=101, session=session, current_user=_USER, _resource_info=info
        )
        is resource_service.extend_session.return_value
    )
    assert (
        resources_route.get_ssh_key(
            vmid=101, session=session, _current_user=_USER, resource_info=info
        )
        is credentials_service.get_ssh_key.return_value
    )


# ─── vm_requests ─────────────────────────────────────────────────────────────


def _advise_request() -> SimpleNamespace:
    return SimpleNamespace(
        environment_type="web",
        os_info="Ubuntu",
        reason="課程",
        cores=2,
        memory=2048,
        gpu_mapping_id=None,
    )


def test_vm_request_advise_disabled_by_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vm_requests_route.governance_repo,
        "get_governance_config",
        lambda session: SimpleNamespace(workload_advisor_enabled=False),
    )
    with pytest.raises(BadRequestError):
        vm_requests_route.advise_workload(
            request_in=_advise_request(), session=MagicMock(), _=_USER
        )


def test_vm_request_advise_returns_engine_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    advisor = MagicMock()
    advisor.advise.return_value = SimpleNamespace(
        resource_type="lxc", confidence="high", reasons=["輕量服務"]
    )
    monkeypatch.setattr(
        vm_requests_route.governance_repo,
        "get_governance_config",
        lambda session: SimpleNamespace(workload_advisor_enabled=True),
    )
    monkeypatch.setattr(vm_requests_route, "workload_advisor", advisor)

    result = vm_requests_route.advise_workload(
        request_in=_advise_request(), session=MagicMock(), _=_USER
    )

    assert result.resource_type == "lxc"
    assert result.reasons == ["輕量服務"]
    assert advisor.advise.call_args.kwargs["memory"] == 2048


def test_vm_request_routes_delegate_to_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_service = MagicMock()
    availability_service = MagicMock()
    monkeypatch.setattr(vm_requests_route, "vm_request_service", request_service)
    monkeypatch.setattr(
        vm_requests_route, "vm_request_availability_service", availability_service
    )
    session = MagicMock()
    request_id = uuid.uuid4()

    assert (
        vm_requests_route.get_vm_request_window_availability(
            request_in="req", session=session, current_user=_USER
        )
        is availability_service.assess_request_window.return_value
    )
    assert (
        vm_requests_route.list_my_vm_requests(
            session=session, current_user=_USER, skip=0, limit=10
        )
        is request_service.list_by_user.return_value
    )
    assert (
        vm_requests_route.list_all_vm_requests(
            session=session, current_user=_USER, status=None, skip=0, limit=10
        )
        is request_service.list_all.return_value
    )
    assert (
        vm_requests_route.get_vm_request(
            request_id=request_id, session=session, current_user=_USER
        )
        is request_service.get.return_value
    )
    assert (
        vm_requests_route.cancel_vm_request(
            request_id=request_id, session=session, current_user=_USER
        )
        is request_service.cancel.return_value
    )
    assert (
        vm_requests_route.retry_vm_request(
            request_id=request_id, session=session, current_user=_USER
        )
        is request_service.retry.return_value
    )
    assert (
        vm_requests_route.get_vm_request_review_context(
            request_id=request_id, session=session, current_user=_USER
        )
        is request_service.get_review_context.return_value
    )
    assert (
        vm_requests_route.review_vm_request(
            request_id=request_id,
            review="review",
            session=session,
            current_user=_USER,
        )
        is request_service.review.return_value
    )
    assert request_service.review.call_args.kwargs["reviewer"] is _USER


# ─── gpu ─────────────────────────────────────────────────────────────────────


def test_gpu_mapping_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    service.list_gpu_mappings.return_value = []
    monkeypatch.setattr(gpu_route, "gpu_service", service)

    listed = gpu_route.list_gpu_mappings(current_user=_USER)
    assert listed.count == 0
    assert (
        gpu_route.get_gpu_mapping(mapping_id="h200", current_user=_USER)
        is service.get_gpu_mapping.return_value
    )
    body = SimpleNamespace(id="h200", description="H200", map=["node=pve1"])
    assert gpu_route.create_gpu_mapping(body=body, current_user=_USER) == {
        "message": "GPU mapping 'h200' created"
    }
    service.create_gpu_mapping.assert_called_once_with(
        mapping_id="h200", description="H200", map_entries=["node=pve1"]
    )
    assert gpu_route.delete_gpu_mapping(mapping_id="h200", current_user=_USER) == {
        "message": "GPU mapping 'h200' deleted"
    }
    service.delete_gpu_mapping.assert_called_once_with("h200")


def test_gpu_options_with_and_without_reservation_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MagicMock()
    service.list_gpu_options.return_value = ["gpu-a"]
    service.apply_reservation_window.return_value = ["gpu-a-windowed"]
    monkeypatch.setattr(gpu_route, "gpu_service", service)
    session = MagicMock()

    assert gpu_route.list_gpu_options(
        current_user=_USER, session=session, start_at=_NOW, end_at=None, node="pve1"
    ) == ["gpu-a"]
    service.apply_reservation_window.assert_not_called()

    assert gpu_route.list_gpu_options(
        current_user=_USER, session=session, start_at=_NOW, end_at=_NOW, node=None
    ) == ["gpu-a-windowed"]
    service.apply_reservation_window.assert_called_once_with(
        session, ["gpu-a"], start_at=_NOW, end_at=_NOW
    )


# ─── lxc ─────────────────────────────────────────────────────────────────────


def test_lxc_terminal_returns_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lxc_route.proxmox_service,
        "get_terminal_ticket",
        lambda node, vmid: {"ticket": f"{node}-{vmid}"},
    )
    result = lxc_route.get_lxc_terminal(vmid=200, container_info={"node": "pve1"})
    assert result["ticket"] == "pve1-200"
    assert result["ws_url"] == "/ws/terminal/200/"


def test_lxc_terminal_reraises_proxmox_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(node: str, vmid: int) -> dict[str, str]:
        raise ProxmoxError("PVE says no")

    monkeypatch.setattr(lxc_route.proxmox_service, "get_terminal_ticket", fail)
    with pytest.raises(ProxmoxError, match="PVE says no"):
        lxc_route.get_lxc_terminal(vmid=200, container_info={"node": "pve1"})


def test_lxc_terminal_wraps_unexpected_error() -> None:
    # container_info 少了 node：非預期例外一律包成 ProxmoxError
    with pytest.raises(ProxmoxError, match="Failed to get LXC terminal"):
        lxc_route.get_lxc_terminal(vmid=200, container_info={})


def test_lxc_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lxc_route.provisioning_service, "get_lxc_templates", lambda: ["debian"]
    )
    assert lxc_route.get_templates(current_user=_USER) == ["debian"]


# ─── vm ──────────────────────────────────────────────────────────────────────


async def test_vm_console_rejects_non_qemu() -> None:
    with pytest.raises(BadRequestError):
        await vm_route.get_vm_console(vmid=200, vm_info={"type": "lxc", "node": "n"})


async def test_vm_console_wraps_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(nodes: list[str]) -> set[int]:
        raise RuntimeError("cluster unreachable")

    monkeypatch.setattr(vm_route.proxmox_service, "list_booting_vmids", boom)
    with pytest.raises(ProxmoxError, match="Failed to get VM console"):
        await vm_route.get_vm_console(vmid=101, vm_info={"type": "qemu", "node": "n"})


@pytest.mark.parametrize("can_manage", [True, False])
def test_vm_templates_hide_registered_for_non_managers(
    monkeypatch: pytest.MonkeyPatch, can_manage: bool
) -> None:
    templates = [SimpleNamespace(vmid=9000), SimpleNamespace(vmid=9001)]
    monkeypatch.setattr(
        vm_route.provisioning_service, "get_vm_templates", lambda: templates
    )
    monkeypatch.setattr(vm_route, "has_permission", lambda user, perm: can_manage)
    monkeypatch.setattr(
        vm_route.vm_template_repo, "registered_pve_vmids", lambda session: {9000}
    )

    result = vm_route.get_vm_templates(session=MagicMock(), current_user=_USER)

    expected = [9000, 9001] if can_manage else [9001]
    assert [item.vmid for item in result] == expected


# ─── quotas ──────────────────────────────────────────────────────────────────


def _quota() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        max_cpu_cores=4,
        max_memory_mb=8192,
        max_disk_gb=100,
        max_instances=3,
        created_at=_NOW,
    )


def test_quota_my_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    service.get_effective_quota.return_value = _quota()
    service.get_usage.return_value = SimpleNamespace(
        cpu_cores=1, memory_mb=1024, disk_gb=20, instances=1
    )
    monkeypatch.setattr(quotas_route, "quota_service", service)

    usage = quotas_route.get_my_usage(session=MagicMock(), current_user=_USER)

    assert usage.used_memory_mb == 1024
    assert usage.quota.max_instances == 3


def test_quota_admin_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    with_user = _quota()
    orphan = _quota()
    service.list_user_quotas.return_value = [with_user, orphan]
    service.create_user_quota.return_value = with_user
    monkeypatch.setattr(quotas_route, "quota_service", service)
    session = MagicMock()
    session.get.side_effect = lambda model, user_id: (
        SimpleNamespace(email="a@example.com") if user_id == with_user.user_id else None
    )

    listed = quotas_route.list_quotas(session=session, _=_USER)
    assert [item.user_email for item in listed] == ["a@example.com", None]

    created = quotas_route.create_quota(
        body="body", session=session, current_user=_USER
    )
    assert created.id == with_user.id
    service.create_user_quota.assert_called_once_with(
        session, "body", actor_id=_USER.id
    )

    quota_id = uuid.uuid4()
    deleted = quotas_route.delete_quota(
        quota_id=quota_id, session=session, current_user=_USER
    )
    assert deleted.message == "Quota deleted"
    service.delete_user_quota.assert_called_once_with(
        session, quota_id, actor_id=_USER.id
    )


# ─── spec_change_requests ────────────────────────────────────────────────────


def test_spec_change_routes_delegate_to_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MagicMock()
    monkeypatch.setattr(spec_route, "spec_change_service", service)
    session = MagicMock()
    request_id = uuid.uuid4()

    assert (
        spec_route.create_spec_change_request(
            request_in="in", session=session, current_user=_USER
        )
        is service.create.return_value
    )
    assert (
        spec_route.get_all_spec_change_requests(
            session=session, current_user=_USER, skip=0, limit=10, status=None, vmid=5
        )
        is service.list_all.return_value
    )
    assert (
        spec_route.review_spec_change_request(
            request_id=request_id, review="r", session=session, current_user=_USER
        )
        is service.review.return_value
    )
    assert (
        spec_route.apply_spec_change_request(
            request_id=request_id, session=session, current_user=_USER
        )
        is service.apply.return_value
    )
    assert (
        spec_route.cancel_spec_change_request(
            request_id=request_id, session=session, current_user=_USER
        )
        is service.cancel.return_value
    )
    service.list_all.assert_called_once_with(
        session=session, skip=0, limit=10, status=None, vmid=5
    )


# ─── mining_incidents ────────────────────────────────────────────────────────


class _FakeIncidentPublic:
    @staticmethod
    def model_validate(obj: Any, *, from_attributes: bool) -> Any:
        assert from_attributes is True
        return ("public", obj)


def _mining_incident(vmid: int) -> SimpleNamespace:
    now = datetime(2026, 10, 16, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(), vmid=vmid, user_id=uuid.uuid4(), node="pve1",
        resource_type="qemu", avg_cpu=96.8, window_hours=6, snapshot_name=None,
        status=MiningIncidentStatus.suspended, detected_at=now, suspended_at=now,
        reviewed_by=None, reviewed_at=None, review_note=None,
    )


def test_mining_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = MagicMock()
    repo.list_incidents_with_owners.return_value = [
        (_mining_incident(101), SimpleNamespace(email="a@x.tw", full_name="甲")),
        (_mining_incident(102), None),
    ]
    service = MagicMock()
    service.ban_incident.return_value = "banned"
    service.set_exemption.return_value = SimpleNamespace(mining_exempt=True)
    monkeypatch.setattr(mining_route, "mining_repo", repo)
    monkeypatch.setattr(mining_route, "mining_service", service)
    session = MagicMock()

    listed = mining_route.list_incidents(
        session=session, _=_USER, status=None, limit=50
    )
    assert [(i.vmid, i.owner_email, i.owner_full_name) for i in listed] == [
        (101, "a@x.tw", "甲"),
        (102, None, None),
    ]
    repo.list_incidents_with_owners.assert_called_once_with(
        session=session, status=None, limit=50
    )

    monkeypatch.setattr(mining_route, "MiningIncidentPublic", _FakeIncidentPublic)

    assert mining_route.ban_incident(
        incident_id=uuid.uuid4(), session=session, current_user=_USER
    ) == ("public", "banned")

    exemption = mining_route.set_exemption(
        vmid=101,
        body=SimpleNamespace(exempt=True),
        session=session,
        current_user=_USER,
    )
    assert exemption.vmid == 101
    assert exemption.exempt is True


# ─── jobs ────────────────────────────────────────────────────────────────────


def test_jobs_list_and_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    monkeypatch.setattr(jobs_route, "jobs_service", service)
    session = MagicMock()

    assert (
        jobs_route.list_unified_jobs(
            session=session,
            current_user=_USER,
            kinds="vm_request, bogus ,deletion",
            statuses=None,
            active_only=True,
            limit=10,
            offset=0,
            history_days=7,
        )
        is service.list_jobs.return_value
    )
    kwargs = service.list_jobs.call_args.kwargs
    assert kwargs["kinds"] == [JobKind.vm_request, JobKind.deletion]
    assert kwargs["statuses"] is None

    assert (
        jobs_route.list_recent_jobs(session=session, current_user=_USER, limit=3)
        is service.list_recent_for_user.return_value
    )


@pytest.mark.parametrize(
    ("error", "status_code"),
    [(None, None), (JobNotFoundError("nope"), 404), (JobAccessDeniedError("no"), 403)],
)
def test_jobs_detail_maps_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None, status_code: int | None
) -> None:
    get_detail = MagicMock(side_effect=error) if error else MagicMock()
    monkeypatch.setattr(jobs_route.jobs_service, "get_job_detail", get_detail)

    if status_code is None:
        assert (
            jobs_route.get_job(
                job_id="vm_request:1", session=MagicMock(), current_user=_USER
            )
            is get_detail.return_value
        )
        return
    with pytest.raises(HTTPException) as exc_info:
        jobs_route.get_job(
            job_id="vm_request:1", session=MagicMock(), current_user=_USER
        )
    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == str(error)
