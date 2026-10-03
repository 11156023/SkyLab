import uuid
from types import SimpleNamespace

from app.api.routes import teaching_classes
from app.services.teaching.class_status_service import (
    class_resource_usage_items as _class_resource_usage_items,
)


def test_class_resource_usage_converts_pve_ratios_and_memory_bytes() -> None:
    items = _class_resource_usage_items(
        [7300, 7301],
        [
            {
                "vmid": 7300,
                "status": "running",
                "cpu": 0.4267,
                "mem": 3 * 1024**3,
                "maxmem": 4 * 1024**3,
            },
            {
                "vmid": 7301,
                "status": "stopped",
                "cpu": 0,
                "mem": 0,
                "maxmem": 2 * 1024**3,
            },
        ],
    )

    assert [item.model_dump() for item in items] == [
        {
            "vmid": 7300,
            "status": "running",
            "cpu_usage_pct": 42.67,
            "ram_usage_pct": 75.0,
            "mem_used_bytes": 3 * 1024**3,
            "mem_total_bytes": 4 * 1024**3,
        },
        {
            "vmid": 7301,
            "status": "stopped",
            "cpu_usage_pct": 0.0,
            "ram_usage_pct": 0.0,
            "mem_used_bytes": 0,
            "mem_total_bytes": 2 * 1024**3,
        },
    ]


def test_class_resource_usage_marks_missing_pve_resources_unknown() -> None:
    items = _class_resource_usage_items([7302, 7302], [])

    assert len(items) == 1
    assert items[0].vmid == 7302
    assert items[0].status == "unknown"
    assert items[0].cpu_usage_pct is None
    assert items[0].ram_usage_pct is None


def test_class_resource_usage_rejects_non_finite_metrics() -> None:
    item = _class_resource_usage_items(
        [7303],
        [{"vmid": 7303, "status": "running", "cpu": "nan", "mem": 10, "maxmem": 0}],
    )[0]

    assert item.cpu_usage_pct is None
    assert item.ram_usage_pct is None


def test_class_resource_usage_endpoint_reads_pve_once_for_the_whole_class(
    monkeypatch,
) -> None:
    class_id = uuid.uuid4()
    enrollment_id = uuid.uuid4()
    machine_rows = [
        SimpleNamespace(class_student_id=enrollment_id, vmid=7300),
        SimpleNamespace(class_student_id=enrollment_id, vmid=7301),
    ]

    class _Result:
        def all(self):
            return machine_rows

    class _Session:
        def exec(self, _statement):
            return _Result()

    calls = []
    monkeypatch.setattr(
        teaching_classes,
        "_get_class",
        lambda _session, _user, requested_id: SimpleNamespace(id=requested_id),
    )
    monkeypatch.setattr(
        teaching_classes,
        "_students",
        lambda _session, _class_id: [SimpleNamespace(id=enrollment_id)],
    )
    monkeypatch.setattr(
        teaching_classes.proxmox_service,
        "list_all_resources",
        lambda: (
            calls.append(True)
            or [
                {"vmid": 7300, "status": "running", "cpu": 0.1, "mem": 1, "maxmem": 2},
                {"vmid": 7301, "status": "stopped", "cpu": 0, "mem": 0, "maxmem": 2},
            ]
        ),
    )

    response = teaching_classes.get_class_resource_usage(
        class_id=class_id,
        session=_Session(),
        current_user=SimpleNamespace(),
    )

    assert calls == [True]
    assert [item.vmid for item in response.items] == [7300, 7301]
