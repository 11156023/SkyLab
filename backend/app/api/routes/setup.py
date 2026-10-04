"""首次安裝初始化精靈 API（免登入，僅在尚未完成初始化時可用）。

安全邊界：
- 每個寫入端點都經 `setup_service.ensure_setup_open`，`completed` 之後一律 403。
- 依 IP 限流，避免有人在精靈完成前對這組端點暴力嘗試。
- 回應只帶進度布林與這次寫入的結果，不揭露既有帳號或連線資料。
"""

from fastapi import APIRouter, Depends

from app.api.deps import SessionDep, rate_limit_by_ip
from app.schemas.gateway import (
    GatewayConfigPublic,
    GatewayConfigUpdate,
    GatewayConnectionTestResult,
    GatewayInstallOptions,
    GatewayInstallStatus,
    PlatformEntryPublic,
    PlatformEntryUpstreamTest,
    PlatformEntryUpstreamTestRequest,
)
from app.schemas.ip_management import SubnetConfigCreate
from app.schemas.setup import (
    SetupAdminCreate,
    SetupAdminResult,
    SetupCompleteResult,
    SetupPlatformEntryUpdate,
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
# Gateway 安裝中前端每 3 秒讀一次狀態，和寫入端點共用額度會把自己擋掉
_SETUP_POLL_RATE_LIMIT = Depends(
    rate_limit_by_ip(scope="setup-poll", limit=60, window_seconds=60)
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


@router.get(
    "/gateway", response_model=GatewayConfigPublic, dependencies=[_SETUP_RATE_LIMIT]
)
def setup_gateway_get(session: SessionDep) -> GatewayConfigPublic:
    """步驟四：目前的 Gateway 連線設定與公鑰。"""
    return setup_service.get_gateway(session=session)


@router.put(
    "/gateway", response_model=GatewayConfigPublic, dependencies=[_SETUP_RATE_LIMIT]
)
def setup_gateway(session: SessionDep, body: GatewayConfigUpdate) -> GatewayConfigPublic:
    """步驟四：儲存 Gateway 的 SSH 連線設定，還沒有金鑰就產生一組並回傳公鑰。"""
    return setup_service.configure_gateway(session=session, data=body)


@router.post(
    "/gateway/test",
    response_model=GatewayConnectionTestResult,
    dependencies=[_SETUP_RATE_LIMIT],
)
def setup_gateway_test(session: SessionDep) -> GatewayConnectionTestResult:
    """步驟四：用已儲存的設定測試 SSH 連線。"""
    return setup_service.test_gateway(session=session)


@router.get(
    "/gateway/install",
    response_model=GatewayInstallStatus,
    dependencies=[_SETUP_POLL_RATE_LIMIT],
)
def setup_gateway_install_status(session: SessionDep) -> GatewayInstallStatus:
    """步驟四：Gateway 一鍵安裝的狀態、日誌與建議參數（安裝中前端會輪詢）。"""
    return setup_service.get_gateway_install(session=session)


@router.post(
    "/gateway/install",
    response_model=GatewayInstallStatus,
    status_code=202,
    dependencies=[_SETUP_RATE_LIMIT],
)
def setup_gateway_install(
    session: SessionDep, body: GatewayInstallOptions
) -> GatewayInstallStatus:
    """步驟四：在 Gateway 背景執行 install.sh（nginx／certbot／WireGuard）。"""
    return setup_service.start_gateway_install(session=session, options=body)


@router.get(
    "/platform-entry",
    response_model=PlatformEntryPublic,
    dependencies=[_SETUP_RATE_LIMIT],
)
def setup_platform_entry_get(session: SessionDep) -> PlatformEntryPublic:
    """步驟五：目前的平台入口設定。"""
    return setup_service.get_platform_entry(session=session)


@router.post(
    "/platform-entry/test-upstream",
    response_model=PlatformEntryUpstreamTest,
    dependencies=[_SETUP_RATE_LIMIT],
)
def setup_platform_entry_test(
    session: SessionDep, body: PlatformEntryUpstreamTestRequest
) -> PlatformEntryUpstreamTest:
    """步驟五：從 Gateway 連一次主系統入口，回報通不通。"""
    return setup_service.test_platform_entry_upstream(session=session, data=body)


@router.put(
    "/platform-entry",
    response_model=PlatformEntryPublic,
    dependencies=[_SETUP_RATE_LIMIT],
)
def setup_platform_entry(
    session: SessionDep, body: SetupPlatformEntryUpdate
) -> PlatformEntryPublic:
    """步驟五：儲存平台入口並同步到 Gateway 的 nginx。"""
    return setup_service.configure_platform_entry(session=session, data=body)


@router.post(
    "/complete", response_model=SetupCompleteResult, dependencies=[_SETUP_RATE_LIMIT]
)
def setup_complete(session: SessionDep) -> SetupCompleteResult:
    """完成初始化；之後所有 /setup 寫入端點關閉。"""
    state = setup_service.complete(session=session)
    return SetupCompleteResult(completed=state.completed, completed_at=state.completed_at)
