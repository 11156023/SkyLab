from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.models import User
from app.models.user import UserRole

# 存取層級與 App.jsx 的路由守衛一一對應：
#   all   -> 任何登入者
#   staff -> canTeach（teacher 或 admin）
#   admin -> isAdmin（is_superuser 或 role == admin）
RouteAccess = Literal["all", "staff", "admin"]


@dataclass(frozen=True)
class NavigationRoute:
    path: str
    title: str
    summary: str
    keywords: tuple[str, ...]
    access: RouteAccess = "all"


# 路徑必須實際存在於 frontend/src/App.jsx；
# test_ai_navigation_catalog.py 會逐一比對，改路由時這裡會一起紅燈。
_ROUTES: tuple[NavigationRoute, ...] = (
    NavigationRoute(
        path="/dashboard",
        title="首頁",
        summary="依身分顯示課程總覽與快速入口。",
        keywords=(
            "首頁",
            "儀表板",
            "dashboard",
            "總覽",
            "主頁",
            "課程",
            "關卡",
            "course",
            "學習",
            "練習",
        ),
    ),
    NavigationRoute(
        path="/my-resources",
        title="我的資源",
        summary="查看與操作我名下的 VM/LXC 機器。",
        keywords=("我的資源", "我的機器", "我有哪些機器", "my resource", "my vm", "開機", "關機"),
    ),
    NavigationRoute(
        path="/my-requests",
        title="我的申請",
        summary="提交新的機器申請，並追蹤審核進度。",
        keywords=("申請", "申請單", "我的申請", "request", "申請 vm", "申請 lxc", "借機器"),
    ),
    NavigationRoute(
        path="/account",
        title="帳號設定",
        summary="修改個人資料與密碼。",
        keywords=("帳號", "個人設定", "改密碼", "account", "profile"),
    ),
    NavigationRoute(
        path="/jobs",
        title="背景任務",
        summary="查看建立、克隆與批次任務的執行進度。",
        keywords=("任務", "job", "背景任務", "進度", "排程"),
    ),
    NavigationRoute(
        path="/firewall",
        title="防火牆",
        summary="設定機器的連線與埠規則。",
        keywords=("防火牆", "firewall", "開埠", "port", "連線規則"),
    ),
    NavigationRoute(
        path="/reverse-proxy",
        title="反向代理",
        summary="把機器上的服務對外公開成網址。",
        keywords=("反向代理", "reverse proxy", "對外網址", "公開網站", "traefik", "https"),
    ),
    NavigationRoute(
        path="/ai-api",
        title="AI API",
        summary="申請 AI API 金鑰並查看個人用量。",
        keywords=("ai api", "金鑰", "api key", "token 用量", "llm"),
    ),
    # --- 教師與管理者 ---
    NavigationRoute(
        path="/templates",
        title="範本管理",
        summary="把機器轉成範本，或開放範本給學生申請。",
        keywords=("範本", "模板", "template", "母範本", "轉範本"),
        access="staff",
    ),
    NavigationRoute(
        path="/class-management",
        title="班級管理",
        summary="管理班級名單、每週內容、上課監看與 AI 檢查。",
        keywords=("班級", "課堂", "學生名單", "每週", "上課", "class"),
        access="staff",
    ),
    NavigationRoute(
        path="/class-setup",
        title="建立班級",
        summary="開一個新班級並設定課程環境。",
        keywords=("開班", "建立班級", "新增班級", "create class"),
        access="staff",
    ),
    NavigationRoute(
        path="/course-template-management",
        title="課程環境範本",
        summary="設計課堂要用的學習環境並開放快速練習。",
        keywords=("課程環境", "環境範本", "學習環境", "多機環境", "快速練習"),
        access="staff",
    ),
    NavigationRoute(
        path="/course-cms",
        title="課程內容管理",
        summary="編輯課程關卡、任務與題目。",
        keywords=("課程內容", "關卡編輯", "出題", "cms"),
        access="staff",
    ),
    # --- 僅管理者 ---
    NavigationRoute(
        path="/resource-mgmt",
        title="資源管理",
        summary="管理全站的機器資源。",
        keywords=("資源管理", "所有資源", "全部機器", "resources"),
        access="admin",
    ),
    NavigationRoute(
        path="/request-review",
        title="申請審核",
        summary="審核使用者送出的機器申請。",
        keywords=("審核", "審批", "approval", "核准申請"),
        access="admin",
    ),
    NavigationRoute(
        path="/batch-review",
        title="批次開通審核",
        summary="審核整批建立的課堂機器。",
        keywords=("批次審核", "批次開通", "batch"),
        access="admin",
    ),
    NavigationRoute(
        path="/gpu-mgmt",
        title="GPU 管理",
        summary="查看 GPU 映射、vGPU 規格與使用狀況。",
        keywords=("gpu", "顯卡", "cuda", "vgpu"),
        access="admin",
    ),
    NavigationRoute(
        path="/monitoring",
        title="資源監控",
        summary="查看節點與機器的即時負載。",
        keywords=("監控", "monitoring", "負載", "cpu 使用率"),
        access="admin",
    ),
    NavigationRoute(
        path="/audit",
        title="稽核紀錄",
        summary="查詢系統操作紀錄。",
        keywords=("稽核", "audit", "操作紀錄", "log"),
        access="admin",
    ),
    # 原「系統設定」的七個分頁，2026-09 各自升格為系統管理底下的獨立頁面；
    # 舊網址 /settings 預設導到 PVE 連線，所以「系統設定」這個關鍵字留在它身上。
    NavigationRoute(
        path="/pve-connections",
        title="PVE 連線",
        summary="管理 Proxmox VE 連線入口，以及各叢集自己的 pool、Storage 與網段。",
        keywords=("pve 連線", "proxmox", "連線", "叢集", "cluster", "pool", "系統設定"),
        access="admin",
    ),
    NavigationRoute(
        path="/scheduler",
        title="資源排程",
        summary="調整放置、超配與排程開機參數。",
        keywords=("資源排程", "排程", "超配", "放置", "placement", "overcommit", "scheduler"),
        access="admin",
    ),
    NavigationRoute(
        path="/governance",
        title="治理",
        summary="設定資源警告、TTL 回收、閒置與挖礦偵測等治理政策。",
        keywords=("治理", "政策", "governance", "ttl", "閒置", "挖礦", "快照"),
        access="admin",
    ),
    NavigationRoute(
        path="/quotas",
        title="配額",
        summary="設定全域預設的資源上限與個別使用者的覆寫。",
        keywords=("配額", "quota", "上限", "限制"),
        access="admin",
    ),
    NavigationRoute(
        path="/ldap",
        title="LDAP",
        summary="設定 LDAP / Active Directory 登入與角色對映。",
        keywords=("ldap", "active directory", "ad 登入", "網域帳號"),
        access="admin",
    ),
    NavigationRoute(
        path="/nodes",
        title="節點管理",
        summary="管理各 PVE 節點的啟用狀態、連線位址與放置優先度。",
        keywords=("節點", "node", "節點管理", "優先度"),
        access="admin",
    ),
    NavigationRoute(
        path="/storage",
        title="Storage",
        summary="管理各 Storage 的速度等級、使用者優先度與啟用狀態。",
        keywords=("storage", "儲存", "磁碟", "nvme", "ssd"),
        access="admin",
    ),
    NavigationRoute(
        path="/ip-management",
        title="IP 管理",
        summary="配置子網與 IP 配發。",
        keywords=("ip", "子網", "subnet", "網段"),
        access="admin",
    ),
    NavigationRoute(
        path="/domain",
        title="網域管理",
        summary="管理 DNS 與 Cloudflare 網域設定。",
        keywords=("網域", "domain", "dns", "cloudflare"),
        access="admin",
    ),
    NavigationRoute(
        path="/gateway",
        title="閘道 VM",
        summary="管理對外流量的 Gateway VM。",
        keywords=("閘道", "gateway", "對外流量"),
        access="admin",
    ),
    NavigationRoute(
        path="/admin",
        title="管理主頁",
        summary="系統管理總覽入口。",
        keywords=("管理主頁", "後台", "admin"),
        access="admin",
    ),
    NavigationRoute(
        path="/ai-api-review",
        title="AI API 審核",
        summary="審核使用者的 AI API 申請。",
        keywords=("ai api 審核", "api 申請審核"),
        access="admin",
    ),
    NavigationRoute(
        path="/ai-api-keys",
        title="AI API 金鑰管理",
        summary="管理平台發出的 AI API 金鑰。",
        keywords=("金鑰管理", "api key 管理"),
        access="admin",
    ),
    NavigationRoute(
        path="/ai-monitoring",
        title="AI 使用監控",
        summary="查看全站 AI 用量與成本。",
        keywords=("ai 監控", "ai 用量", "ai monitoring"),
        access="admin",
    ),
)


def resolve_user_role(user: User) -> UserRole:
    if bool(getattr(user, "is_superuser", False)):
        return UserRole.admin
    role = getattr(user, "role", UserRole.student)
    if isinstance(role, UserRole):
        return role
    try:
        return UserRole(str(role))
    except ValueError:
        return UserRole.student


def can_access(access: RouteAccess, role: UserRole) -> bool:
    if access == "all":
        return True
    if access == "staff":
        return role in {UserRole.teacher, UserRole.admin}
    return role == UserRole.admin


def get_routes_for_user(user: User) -> tuple[NavigationRoute, ...]:
    role = resolve_user_role(user)
    return tuple(route for route in _ROUTES if can_access(route.access, role))


def all_routes() -> tuple[NavigationRoute, ...]:
    return _ROUTES


def find_route_by_path(
    path: str, routes: Iterable[NavigationRoute]
) -> NavigationRoute | None:
    target = path.strip()
    for route in routes:
        if route.path == target:
            return route
    return None
