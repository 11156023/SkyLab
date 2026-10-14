"""Coverage tests for app.services.teaching.class_status_service."""

from app.services.teaching import class_status_service


def test_finite_float_rejects_garbage():
    assert class_status_service._finite_float(None) is None
    assert class_status_service._finite_float("abc") is None
    assert class_status_service._finite_float("inf") is None
    assert class_status_service._finite_float("1.5") == 1.5


def test_usage_items_tolerate_unparseable_values():
    items = class_status_service.class_resource_usage_items(
        [7], [{"vmid": 7, "cpu": "n/a", "mem": None, "maxmem": "x", "status": None}]
    )
    assert items[0].cpu_usage_pct is None
    assert items[0].ram_usage_pct is None
    assert items[0].status == "unknown"
