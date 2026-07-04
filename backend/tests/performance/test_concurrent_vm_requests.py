"""層 1 壓測：200 名學生併發提交 VM 申請（需測試 DB；無 DB 環境由
conftest gate 排除）。

驗證：API 全部成功（無 5xx）、p95 響應 < 2 秒、DB 筆數不重不漏。
clone 不在此層發生（scheduled 申請停留 pending 等審核）。
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, func, select

from app.api.routes import vm_requests as vm_requests_routes
from app.main import app
from app.models import VMRequest

pytestmark = pytest.mark.performance

TOTAL_REQUESTS = 200
MAX_WORKERS = 50
P95_LIMIT_SECONDS = 2.0


@pytest.fixture()
def _stubbed_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """略過視窗驗證（會打 PVE）、rate limit 與背景 provision 觸發。"""
    from app.services.vm import vm_request_service

    monkeypatch.setattr(
        vm_request_service.vm_request_availability_service,
        "validate_request_window",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        vm_request_service, "submit_sync", lambda *a, **k: ""
    )
    # rate limit（20/min/user）在 200 併發下必觸發 — 壓測聚焦吞吐，停用之
    app.dependency_overrides[
        vm_requests_routes._CREATE_RATE_LIMIT.dependency
    ] = lambda: None
    yield  # type: ignore[misc]
    app.dependency_overrides.pop(
        vm_requests_routes._CREATE_RATE_LIMIT.dependency, None
    )


def _payload(index: int, run_tag: str) -> dict:
    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(days=7)
    return {
        "resource_type": "lxc",
        "hostname": f"stress-{run_tag}-{index}",
        "ostemplate": "local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst",
        "cores": 2,
        "memory": 4096,
        "rootfs_size": 8,
        "password": "stress-test-pw-123",
        "reason": "壓力測試：模擬 200 名學生同時申請課程實作環境",
        "mode": "scheduled",
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
    }


def test_200_concurrent_vm_request_submissions(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
    _stubbed_side_effects: None,
) -> None:
    run_tag = uuid.uuid4().hex[:8]
    latencies: list[float] = []
    statuses: list[int] = []

    def submit(index: int) -> None:
        started = time.monotonic()
        resp = client.post(
            "/api/v1/vm-requests/",
            headers=normal_user_token_headers,
            json=_payload(index, run_tag),
        )
        latencies.append(time.monotonic() - started)
        statuses.append(resp.status_code)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        list(pool.map(submit, range(TOTAL_REQUESTS)))

    # 全部成功、無 5xx
    server_errors = [s for s in statuses if s >= 500]
    assert not server_errors, f"{len(server_errors)} server errors: {set(server_errors)}"
    success = [s for s in statuses if 200 <= s < 300]
    assert len(success) == TOTAL_REQUESTS

    # p95 響應時間
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    assert p95 < P95_LIMIT_SECONDS, f"p95={p95:.2f}s exceeds {P95_LIMIT_SECONDS}s"

    # DB 不重不漏（以本輪 run_tag 的 hostname 前綴計數）
    count = db.exec(
        select(func.count())
        .select_from(VMRequest)
        .where(VMRequest.hostname.like(f"stress-{run_tag}-%"))  # type: ignore[attr-defined]
    ).one()
    assert count == TOTAL_REQUESTS
