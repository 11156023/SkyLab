"""app.services.monitoring.prometheus_sd_service：從 DB 的 Gateway 設定產生 http_sd target。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.repositories import gateway_config as gw_repo
from app.services.monitoring import prometheus_sd_service


class _FakeSession:
    def __init__(self, _engine: Any) -> None:
        pass

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (None, []),
        (SimpleNamespace(host=None), []),
        (
            SimpleNamespace(host="10.0.0.2"),
            [
                {
                    "targets": [
                        f"10.0.0.2:{prometheus_sd_service.EXPORTER_PORTS['node']}"
                    ],
                    "labels": {"skylab_role": "gateway", "exporter": "node"},
                }
            ],
        ),
    ],
)
def test_gateway_targets_reads_config(
    monkeypatch: pytest.MonkeyPatch, config: Any, expected: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(prometheus_sd_service, "Session", _FakeSession)
    monkeypatch.setattr(gw_repo, "get_gateway_config", lambda _s: config)
    assert prometheus_sd_service.gateway_targets("node") == expected
