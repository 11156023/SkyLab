"""Curated multi-step journeys the navigation assistant can walk a user through.

A single page is often not the answer: "我要申請一台機器" ends at the request
form, but the user still has to wait for review and then find the machine.  The
model's job is only to pick which flow matches -- never to invent the steps --
so the guidance stays correct even when the model is weak or offline.

Every ``path`` must exist in :mod:`app.ai.navigation.catalog`; the catalog test
checks both against the real router table.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.ai.navigation.catalog import RouteAccess, can_access, resolve_user_role
from app.ai.navigation.schemas import NavigationStepPublic
from app.models import User

# 配置模式問完之後要接回這條流程：規劃配置本來就是「申請一台機器」的其中一步。
INTAKE_FLOW_ID = "request_machine"


@dataclass(frozen=True)
class NavigationStep:
    title: str
    path: str
    detail: str
    # 傳給 react-router 的 location state，讓落地頁直接進到正確的視圖，
    # 例如 {"create": True} 會讓 /my-requests 直接開啟申請表單。
    state: dict[str, Any] | None = None
    # 這一步不是「去某一頁」，而是就地讓助手做一件事。
    # "recommend" = 依對話規劃一份配置，產出可直接填進申請單的內容。
    action: str | None = None


@dataclass(frozen=True)
class NavigationFlow:
    flow_id: str
    title: str
    summary: str
    keywords: tuple[str, ...]
    steps: tuple[NavigationStep, ...]
    access: RouteAccess = "all"
    entities: tuple[str, ...] = field(default=())


_FLOWS: tuple[NavigationFlow, ...] = (
    NavigationFlow(
        flow_id="request_machine",
        title="申請一台機器",
        summary="從填申請單到機器可以使用的完整流程。",
        keywords=(
            "申請機器", "申請一台", "我要一台", "借一台", "要機器", "開機器",
            "申請 vm", "申請 lxc", "申請容器", "申請虛擬機", "要 gpu", "申請 gpu",
        ),
        steps=(
            NavigationStep(
                title="打開申請單",
                path="/my-requests",
                detail="填寫需求與時段，帳號密碼自行輸入。",
                state={"create": True},
            ),
            NavigationStep(
                title="AI 協助填寫",
                path="/my-requests",
                detail="回答需求後帶入配置，也可自行填寫。",
                action="recommend",
            ),
            NavigationStep(
                title="等待審核",
                path="/my-requests",
                detail="送出後查看審核狀態。",
            ),
            NavigationStep(
                title="開始使用",
                path="/my-resources",
                detail="核准並建機完成後，即可連線。",
            ),
        ),
    ),
    NavigationFlow(
        flow_id="publish_service",
        title="把機器上的服務對外公開",
        summary="讓別人用網址連到你機器上跑的服務。",
        keywords=(
            "對外公開", "公開網站", "網站公開", "公開服務", "服務對外", "對外服務",
            "外部連線", "外面連", "別人連", "網址", "網站上線",
            "reverse proxy", "反向代理", "開埠", "https",
        ),
        steps=(
            NavigationStep(
                title="確認服務",
                path="/my-resources",
                detail="確認服務已啟動及使用的連接埠。",
            ),
            NavigationStep(
                title="建立對外網址",
                path="/reverse-proxy",
                detail="新增反向代理，指向服務連接埠。",
            ),
            NavigationStep(
                title="放行需要的埠",
                path="/firewall",
                detail="允許服務所需的防火牆連接埠。",
            ),
        ),
    ),
    NavigationFlow(
        flow_id="open_class",
        title="建立班級",
        summary="課表、學生、選用或建立教學環境、每週任務、確認建立。機器範本是教學環境的可選來源，不是班級。",
        keywords=("開班", "開一個班", "新班級", "建立班級", "建立課堂", "帶班", "開課"),
        access="staff",
        steps=(
            NavigationStep(
                title="填寫課表",
                path="/class-setup",
                detail="填班級名稱與上課時間。",
            ),
            NavigationStep(
                title="加入學生名單",
                path="/class-setup",
                detail="輸入學生 Email，加入班級。",
            ),
            NavigationStep(
                title="選擇教學環境",
                path="/class-setup",
                detail="選已發布環境；沒有合適的再建立。",
            ),
            NavigationStep(
                title="安排每週內容",
                path="/class-setup",
                detail="填每週主題與教材。",
            ),
            NavigationStep(
                title="確認建立",
                path="/class-setup",
                detail="檢查容量後，確認建立學生機器。",
            ),
        ),
    ),
    NavigationFlow(
        flow_id="prepare_environment",
        title="建立教學環境",
        summary="在教學環境編輯頁填基本資料、加入機器配置，再確認發布並鎖定版本。依套用方式提供正式課程、快速練習或兩者。",
        keywords=("建立教學環境", "新增教學環境", "環境範本", "多機環境", "建立課程環境"),
        access="staff",
        steps=(
            NavigationStep(title="填寫基本資料", path="/course-template-management/new",
                           state={"environmentTab": "basic"},
                           detail="填名稱，選擇套用方式。"),
            NavigationStep(title="設定機器", path="/course-template-management/new",
                           state={"environmentTab": "machines"},
                           detail="加入 1–3 台機器，調整規格。"),
            NavigationStep(title="發布環境", path="/course-template-management/new",
                           state={"environmentTab": "machines"},
                           detail="確認後發布，機器配置將鎖定。"),
        ),
    ),
    NavigationFlow(
        flow_id="share_template",
        title="把機器做成範本給別人用",
        summary="裝好一台機器後轉成範本，並決定開放給誰。",
        keywords=("做範本", "轉範本", "建立範本", "建立機器範本", "變成範本", "範本給學生", "共用環境", "母範本"),
        access="staff",
        steps=(
            NavigationStep(
                title="準備來源機器",
                path="/my-resources",
                detail="裝好軟體；轉換後來源機器會成為範本。",
            ),
            NavigationStep(
                title="轉換成範本",
                path="/templates",
                detail="選來源機器，填範本名稱與規格。",
            ),
            NavigationStep(
                title="設定可見範圍",
                path="/templates",
                detail="選擇是否開放學生選用。",
            ),
        ),
    ),
    NavigationFlow(
        flow_id="review_requests",
        title="處理待審的機器申請",
        summary="審核申請並確認機器真的開出來了。",
        keywords=("審核申請", "處理申請", "待審", "核准", "批准申請"),
        access="admin",
        steps=(
            NavigationStep(
                title="逐筆審核",
                path="/request-review",
                detail="確認需求，核准或退回申請。",
            ),
            NavigationStep(
                title="確認建立進度",
                path="/jobs",
                detail="查看建機進度與失敗原因。",
            ),
            NavigationStep(
                title="確認機器已開通",
                path="/resource-mgmt",
                detail="在資源管理確認機器狀態。",
            ),
        ),
    ),
)


def get_flows_for_user(user: User) -> tuple[NavigationFlow, ...]:
    role = resolve_user_role(user)
    return tuple(flow for flow in _FLOWS if can_access(flow.access, role))


def all_flows() -> tuple[NavigationFlow, ...]:
    return _FLOWS


def public_steps(flow: NavigationFlow, active: int = 0) -> list[NavigationStepPublic]:
    """把流程步驟轉成回給前端的形狀，並標出走到哪一步。"""
    return [
        NavigationStepPublic(
            index=index,
            title=step.title,
            path=step.path,
            detail=step.detail,
            status=(
                "done" if index < active else "current" if index == active else "todo"
            ),
            state=step.state,
            action=step.action,
        )
        for index, step in enumerate(flow.steps)
    ]


def find_flow_by_id(
    flow_id: str, flows: Iterable[NavigationFlow]
) -> NavigationFlow | None:
    target = flow_id.strip()
    if not target:
        return None
    for flow in flows:
        if flow.flow_id == target:
            return flow
    return None
