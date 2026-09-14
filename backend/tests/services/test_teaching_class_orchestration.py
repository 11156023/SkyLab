import uuid
from datetime import date, time
from types import SimpleNamespace

import pytest

from app.api.routes.course_environments import (
    EnvironmentCreate,
    EnvironmentEdgeIn,
    EnvironmentNodeIn,
)
from app.api.routes.teaching_classes import _generate_weeks, _recurrence
from app.exceptions import BadRequestError
from app.models import BatchProvisionJobStatus, TeachingClassWeek
from app.services.teaching import class_capacity_service, class_network_service
from app.services.vm import batch_provision_service


def test_recurrence_uses_boot_day_when_lead_crosses_midnight():
    teaching_class = SimpleNamespace(
        start_date=date(2026, 9, 1),  # 週二
        weekday=1,
        start_time=time(0, 5),
        end_time=time(2, 0),
        boot_lead_minutes=10,
    )

    rule, duration = _recurrence(teaching_class)

    assert rule == "FREQ=WEEKLY;BYDAY=MO;BYHOUR=23;BYMINUTE=55"
    assert duration == 125


def test_recurrence_follows_the_class_weekday_not_the_start_date():
    """學期從週一開始、但每週三上課時，機器不能在週一開機。"""
    teaching_class = SimpleNamespace(
        start_date=date(2026, 9, 7),  # 週一
        weekday=2,  # 每週三上課
        start_time=time(13, 10),
        end_time=time(16, 0),
        boot_lead_minutes=10,
    )

    rule, duration = _recurrence(teaching_class)

    assert rule == "FREQ=WEEKLY;BYDAY=WE;BYHOUR=13;BYMINUTE=0"
    assert duration == 180


def test_recurrence_includes_the_shutdown_grace_in_the_window():
    teaching_class = SimpleNamespace(
        start_date=date(2026, 9, 9),
        weekday=2,
        start_time=time(13, 10),
        end_time=time(16, 0),
        boot_lead_minutes=10,
        shutdown_grace_minutes=30,
    )

    _rule, duration = _recurrence(teaching_class)

    assert duration == 170 + 10 + 30


class _FakeWeekResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeWeekSession:
    def __init__(self, weeks):
        self.weeks = weeks
        self.deleted = []

    def exec(self, _statement):
        return _FakeWeekResult(self.weeks)

    def add(self, row):
        if row not in self.weeks:
            self.weeks.append(row)

    def delete(self, row):
        self.deleted.append(row)
        self.weeks.remove(row)

    def commit(self):
        return None


def _class_with_weeks(*, weekday, end_date):
    class_id = uuid.uuid4()
    item = SimpleNamespace(
        id=class_id,
        start_date=date(2026, 9, 7),  # 週一開學
        end_date=end_date,
        weekday=weekday,
    )
    return item


def test_changing_the_class_weekday_keeps_every_week_topic():
    """改「每週上課日」只該搬動日期，不該把老師填好的主題與教材清光。"""
    item = _class_with_weeks(weekday=2, end_date=date(2026, 9, 24))
    weeks = [
        TeachingClassWeek(
            class_id=item.id,
            week_number=number,
            session_date=session_date,
            title=title,
        )
        for number, session_date, title in [
            (1, date(2026, 9, 9), "Linux 權限"),
            (2, date(2026, 9, 16), "SSH 金鑰"),
            (3, date(2026, 9, 23), "systemd"),
        ]
    ]
    session = _FakeWeekSession(weeks)

    item.weekday = 3  # 週三改到週四
    _generate_weeks(session, item, preserve=True)

    assert session.deleted == []
    assert [(row.week_number, row.session_date, row.title) for row in session.weeks] == [
        (1, date(2026, 9, 10), "Linux 權限"),
        (2, date(2026, 9, 17), "SSH 金鑰"),
        (3, date(2026, 9, 24), "systemd"),
    ]


def test_extending_the_course_appends_weeks_without_touching_the_old_ones():
    item = _class_with_weeks(weekday=2, end_date=date(2026, 9, 16))
    weeks = [
        TeachingClassWeek(
            class_id=item.id, week_number=1, session_date=date(2026, 9, 9), title="Linux 權限"
        ),
        TeachingClassWeek(
            class_id=item.id, week_number=2, session_date=date(2026, 9, 16), title="SSH 金鑰"
        ),
    ]
    session = _FakeWeekSession(weeks)

    item.end_date = date(2026, 9, 30)
    _generate_weeks(session, item, preserve=True)

    assert session.deleted == []
    assert [(row.week_number, row.session_date, row.title) for row in session.weeks] == [
        (1, date(2026, 9, 9), "Linux 權限"),
        (2, date(2026, 9, 16), "SSH 金鑰"),
        (3, date(2026, 9, 23), ""),
        (4, date(2026, 9, 30), ""),
    ]


def test_shortening_the_course_drops_only_the_trailing_weeks():
    item = _class_with_weeks(weekday=2, end_date=date(2026, 9, 23))
    weeks = [
        TeachingClassWeek(
            class_id=item.id, week_number=1, session_date=date(2026, 9, 9), title="Linux 權限"
        ),
        TeachingClassWeek(
            class_id=item.id, week_number=2, session_date=date(2026, 9, 16), title="SSH 金鑰"
        ),
        TeachingClassWeek(
            class_id=item.id, week_number=3, session_date=date(2026, 9, 23), title="systemd"
        ),
    ]
    session = _FakeWeekSession(weeks)

    item.end_date = date(2026, 9, 16)
    _generate_weeks(session, item, preserve=True)

    assert [row.week_number for row in session.deleted] == [3]
    assert [(row.week_number, row.title) for row in session.weeks] == [
        (1, "Linux 權限"),
        (2, "SSH 金鑰"),
    ]


def test_submit_batch_for_class_students_uses_formal_class(monkeypatch):
    class_id = uuid.uuid4()
    student_ids = [uuid.uuid4(), uuid.uuid4()]
    created_id = uuid.uuid4()
    captured = {}

    monkeypatch.setattr(
        batch_provision_service.ip_management_service,
        "ensure_subnet_configured",
        lambda _session: None,
    )
    monkeypatch.setattr(
        batch_provision_service.ip_management_service,
        "get_ip_stats",
        lambda _session: {"available": 10},
    )

    def create_job(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=created_id)

    monkeypatch.setattr(batch_provision_service.bp_repo, "create_job", create_job)

    job_id = batch_provision_service.submit_batch_job_for_users(
        session=object(),
        member_user_ids=student_ids,
        teaching_class_id=class_id,
        initiated_by_id=uuid.uuid4(),
        resource_type="qemu",
        hostname_prefix="linux-class-web",
        params={"cores": 2, "memory": 4096, "disk_size": 30},
    )

    assert job_id == created_id
    assert captured["teaching_class_id"] == class_id
    assert captured["member_user_ids"] == student_ids


def test_submit_batch_for_class_requires_students():
    with pytest.raises(BadRequestError, match="班級沒有學生"):
        batch_provision_service.submit_batch_job_for_users(
            session=object(),
            member_user_ids=[],
            teaching_class_id=uuid.uuid4(),
            initiated_by_id=uuid.uuid4(),
            resource_type="qemu",
            hostname_prefix="empty-class",
            params={},
        )


def test_class_capacity_is_calculated_for_the_complete_roster():
    nodes = [
        SimpleNamespace(
            cpu=2,
            memory_mb=4096,
            disk_gb=30,
            network="lab-net",
        ),
        SimpleNamespace(
            cpu=2,
            memory_mb=8192,
            disk_gb=80,
            network="lab-net / backend-net",
        ),
    ]
    students = [SimpleNamespace(user_id=uuid.uuid4()) for _ in range(30)]

    result = class_capacity_service.calculate(nodes=nodes, students=students)

    assert result == {
        "student_count": 30,
        "machines_per_student": 2,
        "machine_count": 60,
        "cpu_cores": 120,
        "memory_mb": 368640,
        "disk_gb": 3300,
        "ip_count": 60,
        "network_count": 60,
    }


def test_full_capacity_preview_checks_cluster_and_ip(monkeypatch):
    nodes = [
        SimpleNamespace(
            cpu=2,
            memory_mb=2048,
            disk_gb=20,
            network="lab-net",
        )
    ]
    students = [SimpleNamespace(user_id=uuid.uuid4()) for _ in range(3)]
    monkeypatch.setattr(
        class_capacity_service.ip_management_service,
        "get_ip_stats",
        lambda _session: {"available": 20},
    )
    monkeypatch.setattr(
        class_capacity_service,
        "_evaluate_cluster_capacity",
        lambda _session, **_kwargs: (
            {
                "pve1": {
                    "cpu_cores": 6,
                    "memory_bytes": 6 * 1024**3,
                    "disk_bytes": 60 * 1024**3,
                    "machines": 3,
                }
            },
            {},
            [],
        ),
    )

    result = class_capacity_service.preview(
        object(),
        nodes=nodes,
        students=students,
        check_cluster=True,
    )

    assert result["ready"] is True
    assert result["cluster_checked"] is True
    assert result["issues"] == []
    assert result["placement_plan"]["pve1"]["machines"] == 3


def test_full_capacity_preview_reports_cluster_issue(monkeypatch):
    nodes = [
        SimpleNamespace(
            cpu=2,
            memory_mb=2048,
            disk_gb=20,
            network="lab-net",
        )
    ]
    students = [SimpleNamespace(user_id=uuid.uuid4())]
    monkeypatch.setattr(
        class_capacity_service.ip_management_service,
        "get_ip_stats",
        lambda _session: {"available": 20},
    )

    monkeypatch.setattr(
        class_capacity_service,
        "_evaluate_cluster_capacity",
        lambda *_args, **_kwargs: ({}, {}, ["pve1 RAM 不足"]),
    )

    result = class_capacity_service.preview(
        object(),
        nodes=nodes,
        students=students,
        check_cluster=True,
    )

    assert result["ready"] is False
    assert result["issues"] == ["pve1 RAM 不足"]


def test_reserved_class_batch_does_not_repeat_per_node_ip_check(monkeypatch):
    created_id = uuid.uuid4()
    monkeypatch.setattr(
        batch_provision_service.ip_management_service,
        "ensure_subnet_configured",
        lambda _session: None,
    )
    monkeypatch.setattr(
        batch_provision_service.ip_management_service,
        "get_ip_stats",
        lambda _session: {"available": 0},
    )
    monkeypatch.setattr(
        batch_provision_service.bp_repo,
        "create_job",
        lambda **_kwargs: SimpleNamespace(id=created_id),
    )

    assert (
        batch_provision_service.submit_batch_job_for_users(
            session=object(),
            member_user_ids=[uuid.uuid4(), uuid.uuid4()],
            teaching_class_id=uuid.uuid4(),
            initiated_by_id=uuid.uuid4(),
            resource_type="qemu",
            hostname_prefix="reserved-class",
            params={},
            capacity_reserved=True,
        )
        == created_id
    )


def test_class_jobs_are_approved_as_one_decision(monkeypatch):
    job_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    jobs = [SimpleNamespace(id=job_id) for job_id in job_ids]
    started = []

    monkeypatch.setattr(
        batch_provision_service.bp_repo,
        "transition_pending_reviews",
        lambda **_kwargs: jobs,
    )

    class FakeThread:
        def __init__(self, *, args, **_kwargs):
            self.job_id = args[0]

        def start(self):
            started.append(self.job_id)

    monkeypatch.setattr(batch_provision_service.threading, "Thread", FakeThread)

    reviewed = batch_provision_service.review_batch_jobs(
        session=object(),
        job_ids=job_ids,
        reviewer_id=uuid.uuid4(),
        decision=BatchProvisionJobStatus.approved,
    )

    assert reviewed == jobs
    assert started == job_ids


def test_network_labels_accept_ui_slash_or_comma_notation():
    assert class_network_service._segments("lab-net / backend-net, management") == {
        "lab-net",
        "backend-net",
        "management",
    }


def test_course_connection_creates_matching_source_out_and_target_in(monkeypatch):
    rules = []
    monkeypatch.setattr(
        class_network_service,
        "_ip_by_vmid",
        lambda _session, vmid: {101: "10.0.0.11", 102: "10.0.0.12"}[vmid],
    )
    monkeypatch.setattr(
        class_network_service.proxmox_service,
        "find_resource",
        lambda vmid: {"node": "pve1", "type": "qemu", "vmid": vmid},
    )
    monkeypatch.setattr(
        class_network_service,
        "_ensure_rule",
        lambda **kwargs: rules.append(kwargs),
    )

    class_network_service._allow_one_way(
        object(),
        class_id=uuid.uuid4(),
        source_vmid=101,
        target_vmid=102,
        protocol="tcp",
        port=443,
    )

    assert rules[0]["vmid"] == 101
    assert rules[0]["rule"] == {
        "type": "out",
        "action": "ACCEPT",
        "pos": 0,
        "dest": "10.0.0.12",
        "proto": "tcp",
        "dport": "443",
    }
    assert rules[1]["vmid"] == 102
    assert rules[1]["rule"] == {
        "type": "in",
        "action": "ACCEPT",
        "source": "10.0.0.11",
        "proto": "tcp",
        "dport": "443",
    }


def test_sync_scope_rules_removes_stale_rules_from_a_previous_vmid(monkeypatch):
    """重試會換掉 vmid 與 IP；舊機器上指向舊 IP 的白名單必須跟著消失。

    留著的話，那個 IP 回到池子後被分配給別的學生，就是一條意外的跨學生連通。
    """
    prefix = class_network_service.COMMENT_PREFIX
    existing = {
        101: [
            {"pos": 0, "comment": f"{prefix}abc12345:101>102:any"},
            {"pos": 1, "comment": f"{prefix}abc12345:101>999:any"},  # 舊 vmid 的殘留
            {"pos": 2, "comment": "SkyLab:block-extra:10.0.0.0/8"},  # 別人的規則
        ],
    }
    created: list[dict] = []
    deleted: list[tuple[int, int]] = []

    monkeypatch.setattr(
        class_network_service.proxmox_service,
        "find_resource",
        lambda vmid: {"node": "pve1", "type": "qemu", "vmid": vmid},
    )
    monkeypatch.setattr(
        class_network_service.firewall_service,
        "get_vm_firewall_rules",
        lambda _node, vmid, _type: existing.get(vmid, []),
    )
    monkeypatch.setattr(
        class_network_service.firewall_service,
        "create_rule",
        lambda _node, vmid, _type, rule: created.append({"vmid": vmid, **rule}),
    )
    monkeypatch.setattr(
        class_network_service.firewall_service,
        "delete_rule_by_pos",
        lambda _node, vmid, _type, pos: deleted.append((vmid, pos)),
    )

    errors = class_network_service.sync_scope_rules(
        comment_prefix=prefix,
        scope_vmids={101},
        planned=[
            class_network_service.PlannedRule(
                vmid=101,
                node="pve1",
                resource_type="qemu",
                comment=f"{prefix}abc12345:101>102:any",
                rule={"type": "out", "action": "ACCEPT"},
            )
        ],
    )

    assert errors == []
    assert created == []  # 已經存在的不重複建立
    assert deleted == [(101, 1)]  # 只刪自家前綴的孤兒，block-extra 不動


def test_sync_scope_rules_cleans_machines_that_lost_every_edge(monkeypatch):
    prefix = class_network_service.COMMENT_PREFIX
    deleted: list[tuple[int, int]] = []
    monkeypatch.setattr(
        class_network_service.proxmox_service,
        "find_resource",
        lambda vmid: {"node": "pve1", "type": "qemu", "vmid": vmid},
    )
    monkeypatch.setattr(
        class_network_service.firewall_service,
        "get_vm_firewall_rules",
        lambda _node, _vmid, _type: [{"pos": 3, "comment": f"{prefix}abc12345:101>102:any"}],
    )
    monkeypatch.setattr(
        class_network_service.firewall_service,
        "delete_rule_by_pos",
        lambda _node, vmid, _type, pos: deleted.append((vmid, pos)),
    )

    class_network_service.sync_scope_rules(
        comment_prefix=prefix, scope_vmids={101}, planned=[]
    )

    assert deleted == [(101, 3)]


def test_sync_scope_rules_skips_machines_that_no_longer_exist(monkeypatch):
    def gone(_vmid):
        raise RuntimeError("not found")

    monkeypatch.setattr(class_network_service.proxmox_service, "find_resource", gone)

    assert class_network_service.sync_scope_rules(
        comment_prefix=class_network_service.COMMENT_PREFIX,
        scope_vmids={404},
        planned=[],
    ) == []


def test_ensure_firewall_enabled_restores_a_weakened_inbound_policy(monkeypatch):
    """防火牆開著但 policy_in 被改成 ACCEPT，隔離就整個失效。"""
    from app.services.network import firewall_service

    applied: list[dict] = []
    monkeypatch.setattr(
        firewall_service,
        "get_firewall_options",
        lambda _node, _vmid, _type: {"enable": 1, "policy_in": "ACCEPT"},
    )
    monkeypatch.setattr(
        firewall_service,
        "_set_firewall_options",
        lambda _node, _vmid, _type, **kwargs: applied.append(kwargs),
    )

    firewall_service.ensure_firewall_enabled("pve1", 101, "qemu")

    assert applied == [{"policy_in": "DROP"}]


def test_ensure_firewall_enabled_leaves_a_healthy_machine_alone(monkeypatch):
    """policy_in 沒有值代表沿用 PVE 預設的 DROP，不必動它。"""
    from app.services.network import firewall_service

    applied: list[dict] = []
    monkeypatch.setattr(
        firewall_service,
        "get_firewall_options",
        lambda _node, _vmid, _type: {"enable": 1},
    )
    monkeypatch.setattr(
        firewall_service,
        "_set_firewall_options",
        lambda _node, _vmid, _type, **kwargs: applied.append(kwargs),
    )

    firewall_service.ensure_firewall_enabled("pve1", 101, "qemu")

    assert applied == []


def test_course_environment_accepts_template_and_custom_nodes_with_three_node_limit():
    template_id = uuid.uuid4()
    body = EnvironmentCreate(
        name="Network Lab",
        nodes=[
            EnvironmentNodeIn(
                node_key="gateway",
                source_type="custom",
                custom_image_ref="local:vztmpl/debian.tar.zst",
                name="Gateway",
                role="firewall",
                resource_type="lxc",
                cpu=2,
                memory_mb=2048,
                disk_gb=8,
            ),
            EnvironmentNodeIn(
                node_key="web",
                source_type="template",
                source_template_id=template_id,
                name="Web",
                role="server",
                resource_type="qemu",
                cpu=2,
                memory_mb=4096,
                disk_gb=30,
            ),
        ],
        edges=[
            EnvironmentEdgeIn(
                source_node_key="gateway",
                target_node_key="web",
                direction="one_way",
                protocol="tcp",
                port=443,
            )
        ],
    )

    assert body.nodes[0].source_template_id is None
    assert body.nodes[1].source_template_id == template_id
    assert body.edges[0].port == 443


def test_custom_vm_requires_numeric_base_template_vmid():
    with pytest.raises(ValueError, match="VMID"):
        EnvironmentNodeIn(
            node_key="vm",
            source_type="custom",
            custom_image_ref="ubuntu-cloud-image",
            name="VM",
            role="student",
            resource_type="qemu",
            cpu=2,
            memory_mb=2048,
            disk_gb=20,
        )


def test_course_edge_defaults_match_firewall_connection_dialog():
    edge = EnvironmentEdgeIn(
        source_node_key="client",
        target_node_key="server",
    )

    assert edge.direction == "one_way"
    assert edge.protocol == "tcp"
    assert edge.port == 22


def test_course_edge_rejects_portless_firewall_service():
    with pytest.raises(ValueError, match="Port"):
        EnvironmentEdgeIn(
            source_node_key="client",
            target_node_key="server",
            protocol="tcp",
            port=None,
        )
