from datetime import UTC, datetime
from uuid import uuid4

from app.models import ProxmoxConfig, VMMigrationStatus, VMRequest, VMRequestStatus


def test_proxmox_config_exposes_grouped_state_without_schema_split() -> None:
    config = ProxmoxConfig(
        host="pve.example.test",
        user="root@pam",
        encrypted_password="secret",
    )

    assert config.connection.host == "pve.example.test"
    assert config.connection.user == "root@pam"
    assert config.connection.data_storage == "local-lvm"
    assert config.placement.strategy == "priority_dominant_share"
    assert config.migration.enabled is True
    assert config.migration.retry_limit == 3
    assert config.rebalance.resource_weight_cpu == 1.0
    assert config.scheduler.boot_batch_size == 5
    assert config.scheduler.expiry_warning_hours == 24


def test_vm_request_exposes_grouped_state_without_schema_split() -> None:
    user_id = uuid4()
    reviewer_id = uuid4()
    batch_job_id = uuid4()
    now = datetime(2026, 5, 18, tzinfo=UTC)
    request = VMRequest(
        user_id=user_id,
        reason="Need a research VM for testing",
        resource_type="lxc",
        hostname="research-01",
        password="encrypted",
        status=VMRequestStatus.approved,
        reviewer_id=reviewer_id,
        review_comment="approved",
        reviewed_at=now,
        vmid=101,
        assigned_node="pve-a",
        desired_node="pve-b",
        actual_node="pve-a",
        placement_strategy_used="priority_dominant_share",
        migration_status=VMMigrationStatus.pending,
        migration_error="waiting for capacity",
        migration_pinned=True,
        resource_warning="high load",
        rebalance_epoch=2,
        last_rebalanced_at=now,
        last_migrated_at=now,
        start_at=now,
        end_at=now,
        recurrence_rule="FREQ=WEEKLY",
        recurrence_duration_minutes=120,
        schedule_timezone="Asia/Taipei",
        next_window_start=now,
        next_window_end=now,
        batch_job_id=batch_job_id,
        created_at=now,
    )

    assert request.review_state.reviewer_id == reviewer_id
    assert request.review_state.review_comment == "approved"
    assert request.schedule.batch_job_id == batch_job_id
    assert request.schedule.recurrence_duration_minutes == 120
    assert request.provisioning.vmid == 101
    assert request.provisioning.desired_node == "pve-b"
    assert request.migration.status == VMMigrationStatus.pending
    assert request.migration.rebalance_epoch == 2
