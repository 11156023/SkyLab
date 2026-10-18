"""Coverage tests for app.services.teaching.class_status_service."""

from app.services.teaching import class_status_service


def test_usage_items_tolerate_unparseable_values():
    items = class_status_service.class_resource_usage_items(
        [7], [{"vmid": 7, "cpu": "n/a", "mem": None, "maxmem": "x", "status": None}]
    )
    assert items[0].cpu_usage_pct is None
    assert items[0].ram_usage_pct is None
    assert items[0].status == "unknown"
