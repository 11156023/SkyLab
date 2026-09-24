"""首次安裝初始化精靈 API（免登入，僅在尚未完成初始化時可用）。

安全邊界：
- 每個寫入端點都經 `setup_service.ensure_setup_open`，`completed` 之後一律 403。
- 依 IP 限流，避免有人在精靈完成前對這組端點暴力嘗試。
- 回應只帶進度布林與這次寫入的結果，不揭露既有帳號或連線資料。
"""

from fastapi import APIRouter, Depends

from app.api.deps import SessionDep, rate_limit_by_ip
from app.schemas.ip_management import SubnetConfigCreate
from app.schemas.setup import (
    SetupAdminCreate,
    SetupAdminResult,
    SetupCompleteResult,
    SetupProxmoxCreate,
    SetupProxmoxResult,
    SetupProxmoxTestRequest,
    SetupProxmoxTestResult,
    SetupStatusPublic,
    SetupSubnetResult,
)
from app.services.system import setup_service

router = APIRouter(prefix="/setup", tags=["setup"])

_SETUP_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="setup", limit=20, window_seconds=60)
)


@router.get("/status", response_model=SetupStatusPublic)
def get_setup_status(session: SessionDep) -> SetupStatusPublic:
    """初始化進度（公開端點；前端據此決定是否導向精靈）。"""
    return setup_service.get_status(session=session)


@router.post(
    "/admin", response_model=SetupAdminResult, dependencies=[_SETUP_RATE_LIMIT]
)
def setup_admin(session: SessionDep, body: SetupAdminCreate) -> SetupAdminResult:
    """步驟一：建立系統管理員（信箱已是超級使用者時改為接管）。"""
    return setup_service.configure_admin(session=session, data=body)


@router.post(
    "/proxmox/test",
    response_model=SetupProxmoxTestResult,
    dependencies=[_SETUP_RATE_LIMIT],
)
def setup_proxmox_test(
    session: SessionDep, body: SetupProxmoxTestRequest
) -> SetupProxmoxTestResult:
    """步驟二：用表單內容測試 PVE 連線，回節點與 storage 清單（不儲存）。"""
    setup_service.ensure_setup_open(session=session)
    return setup_service.test_proxmox(data=body)


@router.post(
    "/proxmox", response_model=SetupProxmoxResult, dependencies=[_SETUP_RATE_LIMIT]
)
def setup_proxmox(session: SessionDep, body: SetupProxmoxCreate) -> SetupProxmoxResult:
    """步驟二：建立第一組 PVE 連線並同步節點／Storage。"""
    return setup_service.configure_proxmox(session=session, data=body)


@router.post(
    "/subnet", response_model=SetupSubnetResult, dependencies=[_SETUP_RATE_LIMIT]
)
def setup_subnet(session: SessionDep, body: SubnetConfigCreate) -> SetupSubnetResult:
    """步驟三：設定實驗室 IP 網段。"""
    return setup_service.configure_subnet(session=session, data=body)


@router.post(
    "/complete", response_model=SetupCompleteResult, dependencies=[_SETUP_RATE_LIMIT]
)
def setup_complete(session: SessionDep) -> SetupCompleteResult:
    """完成初始化；之後所有 /setup 寫入端點關閉。"""
    state = setup_service.complete(session=session)
    return SetupCompleteResult(completed=state.completed, completed_at=state.completed_at)
