# 模組 E 教學體驗 (E1–E8) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有 SkyLab 分層架構上實作教學體驗八個子功能：一鍵重置（E1）、配置分發（E2）、進度熱圖（E3）、自定義快照強化（E4）、Pair Mode 協作（E5）、批次規格調整（E6）、資源配額（E7）、快照自動清理（E8）。

**Architecture:** 全面沿用 Routes → Services → Infrastructure 分層。一支 Alembic migration（`resource_quotas` 表 + `governance_config` 三欄位）。批次操作（E2/E6）用 in-memory `ExpiringStore` 存任務狀態 + `background_tasks.submit_factory` fan-out。E5 擴充既有 `VncSessionManager`。純函式（quota 解析、清理資格、activity 判定）與 I/O 分離，比照 `governance/lifecycle_policy` 模式。

**Tech Stack:** FastAPI + SQLModel + PostgreSQL、proxmoxer、paramiko（pct push）、React 19 + TanStack Router/Query + shadcn/ui。

## Global Constraints

- 錯誤一律 `raise AppError 子類`（`app/exceptions.py`：`BadRequestError`=400、`ConflictError`=409、`PermissionDeniedError`=403、`NotFoundError`=404）。
- 初始快照名稱固定 `skylab-init`；配額內建預設 8 cores / 16384 MB / 100 GB / 5 台。
- 配置檔案上限 1 MB（超過回 413）；目標路徑必須為絕對路徑且不含 `..`。
- E8 保留天數預設 7（ge=1, le=90）、學生快照上限預設 3（ge=1, le=10）。
- 批次操作單台失敗不中斷整批，逐台記錄結果。
- 後端測試風格比照 `backend/tests/services/test_mining_service.py`（SimpleNamespace + monkeypatch，無 DB）。單測指令在 `backend/` 下執行：`uv run pytest tests/services/<file> -v`。
- Commit 訊息格式：`模組E教學體驗: <內容> (E<N>)`，結尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 前端新 API 呼叫用 `__request(OpenAPI, {...})` 模式（見 `frontend/src/routes/_layout/admin.configuration.tsx`），避免依賴重新生成的 service 名稱；既有 service（如 `ResourceDetailsService`）照用。
- 已知設計偏差（已定案，實作時不需再討論）：
  1. **E7 用量來源**：`resources` 表沒有 cores/memory/disk 欄位 → 用量 = DB（擁有的 vmid 清單、台數）×  PVE `cluster/resources`（maxcpu/maxmem/maxdisk，單次呼叫）。PVE 不可用時 fail-open（記 warning、放行），不阻斷 provisioning。
  2. **E8 保護清單**：除 `skylab-init` 外也排除 `mining-*` 存證快照與 `current` 偽快照。
  3. **E8 掃描游標**：module-level in-memory 輪替游標（不加 DB 欄位）。
  4. **E5 pair session 記錄**：in-memory（與 VncSessionManager 一致），不進 DB。
  5. **E2 LXC pct push**：SSH 至 `get_active_host()`（與 script_deploy_service 相同的單主機假設）。

---

### Task 1: ResourceQuota model + GovernanceConfig 三欄位 + Alembic migration

**Files:**
- Create: `backend/app/models/resource_quota.py`
- Modify: `backend/app/models/governance_config.py`（三欄位）
- Modify: `backend/app/models/__init__.py`（匯出）
- Create: `backend/app/alembic/versions/e01_teaching_add_quota_and_snapshot_governance.py`

**Interfaces:**
- Produces: `ResourceQuota`、`QuotaScope`（enum: `group`/`user`）、`GovernanceConfig.snapshot_cleanup_enabled/snapshot_retention_days/student_snapshot_max_count`

- [ ] **Step 1: 建立 model**

`backend/app/models/resource_quota.py`：

```python
"""資源配額模型（群組預設 + 個人覆寫）。"""

import uuid
from datetime import datetime
from enum import Enum

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, SQLModel

from .base import get_datetime_utc


class QuotaScope(str, Enum):
    group = "group"
    user = "user"


class ResourceQuota(SQLModel, table=True):
    """配額列：scope=group 時 group_id 必填；scope=user 時 user_id 必填（覆寫）。"""

    __tablename__ = "resource_quotas"
    __table_args__ = (
        sa.UniqueConstraint("group_id", name="uq_resource_quotas_group_id"),
        sa.UniqueConstraint("user_id", name="uq_resource_quotas_user_id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    scope: QuotaScope
    group_id: uuid.UUID | None = Field(default=None, foreign_key="group.id")
    user_id: uuid.UUID | None = Field(default=None, foreign_key="user.id")
    max_cpu_cores: int = Field(default=8, ge=1, le=256)
    max_memory_mb: int = Field(default=16384, ge=256, le=1048576)
    max_disk_gb: int = Field(default=100, ge=1, le=65536)
    max_instances: int = Field(default=5, ge=1, le=100)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["QuotaScope", "ResourceQuota"]
```

- [ ] **Step 2: GovernanceConfig 加欄位**

在 `backend/app/models/governance_config.py` 的 `provision_max_concurrency` 之後、`updated_at` 之前插入：

```python
    # ── 快照治理（模組 E）─────────────────────────────────────────────────
    snapshot_cleanup_enabled: bool = Field(default=True)
    snapshot_retention_days: int = Field(default=7, ge=1, le=90)
    student_snapshot_max_count: int = Field(default=3, ge=1, le=10)
```

- [ ] **Step 3: models/__init__.py 匯出**

import 區塊（依字母序，`.resource` 之後）加：

```python
from .resource_quota import QuotaScope, ResourceQuota
```

`__all__` 的 `# Resource` 段落加 `"ResourceQuota", "QuotaScope",`。

- [ ] **Step 4: 確認 migration head**

Run: `docker compose exec backend alembic heads`（或本機 `cd backend && uv run alembic heads`，需 DB 環境時允許失敗改用下一步的靜態確認）
Expected: 單一 head `gov05_mining`。若非，改以實際 head 作 down_revision。

- [ ] **Step 5: 手寫 migration**

`backend/app/alembic/versions/e01_teaching_add_quota_and_snapshot_governance.py`（比照 `gov05_add_mining_detection.py`）：

```python
"""add resource_quotas table and snapshot governance fields

Revision ID: e01_teaching
Revises: gov05_mining
Create Date: 2026-07-04 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e01_teaching"
down_revision = "gov05_mining"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "governance_config",
        sa.Column(
            "snapshot_cleanup_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "governance_config",
        sa.Column(
            "snapshot_retention_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("7"),
        ),
    )
    op.add_column(
        "governance_config",
        sa.Column(
            "student_snapshot_max_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("3"),
        ),
    )

    quota_scope = sa.Enum("group", "user", name="quotascope")
    op.create_table(
        "resource_quotas",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", quota_scope, nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("max_cpu_cores", sa.Integer(), nullable=False),
        sa.Column("max_memory_mb", sa.Integer(), nullable=False),
        sa.Column("max_disk_gb", sa.Integer(), nullable=False),
        sa.Column("max_instances", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["group.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", name="uq_resource_quotas_group_id"),
        sa.UniqueConstraint("user_id", name="uq_resource_quotas_user_id"),
    )


def downgrade() -> None:
    op.drop_table("resource_quotas")
    sa.Enum(name="quotascope").drop(op.get_bind(), checkfirst=True)
    op.drop_column("governance_config", "student_snapshot_max_count")
    op.drop_column("governance_config", "snapshot_retention_days")
    op.drop_column("governance_config", "snapshot_cleanup_enabled")
```

- [ ] **Step 6: 驗證可 import + lint**

Run: `cd backend && uv run python -c "from app.models import ResourceQuota, QuotaScope; print('ok')" && uv run ruff check app/models/`
Expected: `ok`、ruff 無錯誤。

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/ backend/app/alembic/versions/e01_teaching_add_quota_and_snapshot_governance.py
git commit -m "模組E教學體驗: ResourceQuota 模型與快照治理欄位 migration (E7/E8)"
```

---

### Task 2: quota_policy 純函式 + 單測

**Files:**
- Create: `backend/app/services/resource/quota_policy.py`
- Test: `backend/tests/services/test_quota_policy.py`

**Interfaces:**
- Produces:
  - `EffectiveQuota(max_cpu_cores: int, max_memory_mb: int, max_disk_gb: int, max_instances: int)`（frozen dataclass）
  - `QuotaUsage(cpu_cores: int, memory_mb: int, disk_gb: int, instances: int)`（frozen dataclass）
  - `DEFAULT_QUOTA: EffectiveQuota`
  - `resolve_effective_quota(user_quota, group_quotas) -> EffectiveQuota`：user 覆寫全勝 → group 逐欄位取最大 → 內建預設
  - `check_quota_delta(usage, quota, *, delta_cores=0, delta_memory_mb=0, delta_disk_gb=0, delta_instances=0) -> list[str]`：回傳超限訊息（空 list = 通過）

- [ ] **Step 1: 寫失敗測試**

`backend/tests/services/test_quota_policy.py`：

```python
"""配額解析與執法純函式測試。"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.resource.quota_policy import (
    DEFAULT_QUOTA,
    EffectiveQuota,
    QuotaUsage,
    check_quota_delta,
    resolve_effective_quota,
)


def _quota_row(**overrides: object) -> SimpleNamespace:
    values: dict = {
        "max_cpu_cores": 8,
        "max_memory_mb": 16384,
        "max_disk_gb": 100,
        "max_instances": 5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestResolveEffectiveQuota:
    def test_no_rows_returns_default(self) -> None:
        assert resolve_effective_quota(None, []) == DEFAULT_QUOTA

    def test_user_override_wins_over_groups(self) -> None:
        user_q = _quota_row(max_cpu_cores=2, max_instances=1)
        group_q = _quota_row(max_cpu_cores=32)
        result = resolve_effective_quota(user_q, [group_q])
        assert result.max_cpu_cores == 2
        assert result.max_instances == 1

    def test_group_quotas_take_per_field_max(self) -> None:
        g1 = _quota_row(max_cpu_cores=4, max_memory_mb=8192)
        g2 = _quota_row(max_cpu_cores=16, max_memory_mb=4096)
        result = resolve_effective_quota(None, [g1, g2])
        assert result.max_cpu_cores == 16
        assert result.max_memory_mb == 8192


class TestCheckQuotaDelta:
    def _quota(self) -> EffectiveQuota:
        return EffectiveQuota(
            max_cpu_cores=8, max_memory_mb=16384, max_disk_gb=100, max_instances=5
        )

    def test_within_quota_passes(self) -> None:
        usage = QuotaUsage(cpu_cores=4, memory_mb=8192, disk_gb=40, instances=2)
        assert check_quota_delta(usage, self._quota(), delta_cores=4) == []

    def test_cpu_over_quota_reports(self) -> None:
        usage = QuotaUsage(cpu_cores=6, memory_mb=0, disk_gb=0, instances=0)
        violations = check_quota_delta(usage, self._quota(), delta_cores=4)
        assert len(violations) == 1
        assert "CPU" in violations[0]

    def test_multiple_violations_all_reported(self) -> None:
        usage = QuotaUsage(cpu_cores=8, memory_mb=16384, disk_gb=100, instances=5)
        violations = check_quota_delta(
            usage,
            self._quota(),
            delta_cores=1,
            delta_memory_mb=1,
            delta_disk_gb=1,
            delta_instances=1,
        )
        assert len(violations) == 4

    def test_negative_delta_always_passes(self) -> None:
        usage = QuotaUsage(cpu_cores=8, memory_mb=16384, disk_gb=100, instances=5)
        assert check_quota_delta(usage, self._quota(), delta_cores=-2) == []
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/services/test_quota_policy.py -v`
Expected: FAIL（`ModuleNotFoundError: app.services.resource.quota_policy`）

- [ ] **Step 3: 實作**

`backend/app/services/resource/quota_policy.py`：

```python
"""配額解析與執法純函式（無 I/O，可單測）。

解析順序：user 覆寫（整列全勝）→ 所屬群組逐欄位取最大 → 內建預設。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EffectiveQuota:
    max_cpu_cores: int
    max_memory_mb: int
    max_disk_gb: int
    max_instances: int


@dataclass(frozen=True)
class QuotaUsage:
    cpu_cores: int
    memory_mb: int
    disk_gb: int
    instances: int


DEFAULT_QUOTA = EffectiveQuota(
    max_cpu_cores=8, max_memory_mb=16384, max_disk_gb=100, max_instances=5
)


def resolve_effective_quota(
    user_quota: Any | None, group_quotas: list[Any]
) -> EffectiveQuota:
    if user_quota is not None:
        return EffectiveQuota(
            max_cpu_cores=int(user_quota.max_cpu_cores),
            max_memory_mb=int(user_quota.max_memory_mb),
            max_disk_gb=int(user_quota.max_disk_gb),
            max_instances=int(user_quota.max_instances),
        )
    if group_quotas:
        return EffectiveQuota(
            max_cpu_cores=max(int(q.max_cpu_cores) for q in group_quotas),
            max_memory_mb=max(int(q.max_memory_mb) for q in group_quotas),
            max_disk_gb=max(int(q.max_disk_gb) for q in group_quotas),
            max_instances=max(int(q.max_instances) for q in group_quotas),
        )
    return DEFAULT_QUOTA


def check_quota_delta(
    usage: QuotaUsage,
    quota: EffectiveQuota,
    *,
    delta_cores: int = 0,
    delta_memory_mb: int = 0,
    delta_disk_gb: int = 0,
    delta_instances: int = 0,
) -> list[str]:
    """回傳超限訊息清單；空 list 表示通過。負增量（縮減）永遠通過該欄位。"""
    violations: list[str] = []
    checks = [
        ("CPU", usage.cpu_cores, delta_cores, quota.max_cpu_cores, "cores"),
        ("記憶體", usage.memory_mb, delta_memory_mb, quota.max_memory_mb, "MB"),
        ("磁碟", usage.disk_gb, delta_disk_gb, quota.max_disk_gb, "GB"),
        ("實例數", usage.instances, delta_instances, quota.max_instances, "台"),
    ]
    for label, used, delta, limit, unit in checks:
        if delta > 0 and used + delta > limit:
            violations.append(
                f"{label}超出配額（目前 {used} + 新增 {delta} > 上限 {limit} {unit}）"
            )
    return violations
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && uv run pytest tests/services/test_quota_policy.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/resource/quota_policy.py backend/tests/services/test_quota_policy.py
git commit -m "模組E教學體驗: 配額解析/執法純函式 (E7)"
```

---

### Task 3: quota_service（I/O 層）+ schemas + 單測

**Files:**
- Create: `backend/app/services/resource/quota_service.py`
- Create: `backend/app/schemas/quota.py`
- Modify: `backend/app/schemas/__init__.py`（匯出）
- Test: `backend/tests/services/test_quota_service.py`

**Interfaces:**
- Consumes: Task 1 的 `ResourceQuota`/`QuotaScope`、Task 2 的純函式
- Produces:
  - `quota_service.get_effective_quota(session, user_id) -> EffectiveQuota`
  - `quota_service.get_usage(session, user_id, *, cluster_resources: list[dict] | None = None) -> QuotaUsage`
  - `quota_service.check_quota(session, user_id, *, delta_cores=0, delta_memory_mb=0, delta_disk_gb=0, delta_instances=0) -> None`（超限 raise `ConflictError`；PVE 失敗 fail-open 記 warning）
  - schemas：`ResourceQuotaCreate`、`ResourceQuotaUpdate`、`ResourceQuotaPublic`、`EffectiveQuotaPublic`、`QuotaUsagePublic`

- [ ] **Step 1: 寫失敗測試**

`backend/tests/services/test_quota_service.py`：

```python
"""配額 I/O 層測試（mock DB 查詢與 PVE）。"""

from __future__ import annotations

import uuid

import pytest

from app.exceptions import ConflictError
from app.services.resource import quota_service
from app.services.resource.quota_policy import DEFAULT_QUOTA, EffectiveQuota, QuotaUsage

USER_ID = uuid.uuid4()


@pytest.fixture()
def stub_rows(monkeypatch: pytest.MonkeyPatch):
    """樁掉 DB 查詢：回傳 (user_quota, group_quotas)。"""

    def _set(user_quota=None, group_quotas=None):
        monkeypatch.setattr(
            quota_service,
            "_quota_rows_for_user",
            lambda session, user_id: (user_quota, group_quotas or []),
        )

    return _set


def test_get_effective_quota_defaults(stub_rows) -> None:
    stub_rows()
    assert quota_service.get_effective_quota(None, USER_ID) == DEFAULT_QUOTA


def test_get_usage_sums_cluster_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        quota_service, "_owned_vmids", lambda session, user_id: [101, 102]
    )
    cluster = [
        {"vmid": 101, "maxcpu": 2, "maxmem": 2 * 1024**3, "maxdisk": 20 * 1024**3},
        {"vmid": 102, "maxcpu": 4, "maxmem": 4 * 1024**3, "maxdisk": 30 * 1024**3},
        {"vmid": 999, "maxcpu": 64, "maxmem": 64 * 1024**3, "maxdisk": 999 * 1024**3},
    ]
    usage = quota_service.get_usage(None, USER_ID, cluster_resources=cluster)
    assert usage == QuotaUsage(cpu_cores=6, memory_mb=6144, disk_gb=50, instances=2)


def test_check_quota_raises_conflict(monkeypatch: pytest.MonkeyPatch, stub_rows) -> None:
    stub_rows(user_quota=None, group_quotas=[])
    monkeypatch.setattr(
        quota_service,
        "get_usage",
        lambda session, user_id, cluster_resources=None: QuotaUsage(
            cpu_cores=8, memory_mb=0, disk_gb=0, instances=0
        ),
    )
    with pytest.raises(ConflictError):
        quota_service.check_quota(None, USER_ID, delta_cores=1)


def test_check_quota_fail_open_on_pve_error(
    monkeypatch: pytest.MonkeyPatch, stub_rows
) -> None:
    stub_rows()

    def _boom(session, user_id, cluster_resources=None):
        raise RuntimeError("PVE down")

    monkeypatch.setattr(quota_service, "get_usage", _boom)
    quota_service.check_quota(None, USER_ID, delta_cores=100)  # 不 raise
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/services/test_quota_service.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: 實作 service**

`backend/app/services/resource/quota_service.py`：

```python
"""配額計算與執法 I/O 層（純函式在 quota_policy）。

用量來源：DB resources 表決定擁有的 vmid 與台數；specs 取自 PVE
cluster/resources（maxcpu / maxmem / maxdisk，單次呼叫）。
PVE 不可用時 fail-open（記 warning、放行），不阻斷 provisioning。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlmodel import Session, col, select

from app.exceptions import AppError, ConflictError
from app.models import GroupMember, QuotaScope, Resource, ResourceQuota
from app.services.proxmox import proxmox_service
from app.services.resource.quota_policy import (
    EffectiveQuota,
    QuotaUsage,
    check_quota_delta,
    resolve_effective_quota,
)

logger = logging.getLogger(__name__)

_MIB = 1024**2
_GIB = 1024**3


def _quota_rows_for_user(
    session: Session, user_id: uuid.UUID
) -> tuple[ResourceQuota | None, list[ResourceQuota]]:
    user_quota = session.exec(
        select(ResourceQuota).where(
            ResourceQuota.scope == QuotaScope.user,
            ResourceQuota.user_id == user_id,
        )
    ).first()
    group_ids = session.exec(
        select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    ).all()
    group_quotas: list[ResourceQuota] = []
    if group_ids:
        group_quotas = list(
            session.exec(
                select(ResourceQuota).where(
                    ResourceQuota.scope == QuotaScope.group,
                    col(ResourceQuota.group_id).in_(list(group_ids)),
                )
            ).all()
        )
    return user_quota, group_quotas


def _owned_vmids(session: Session, user_id: uuid.UUID) -> list[int]:
    return [
        int(v)
        for v in session.exec(
            select(Resource.vmid).where(Resource.user_id == user_id)
        ).all()
    ]


def get_effective_quota(session: Session, user_id: uuid.UUID) -> EffectiveQuota:
    user_quota, group_quotas = _quota_rows_for_user(session, user_id)
    return resolve_effective_quota(user_quota, group_quotas)


def get_usage(
    session: Session,
    user_id: uuid.UUID,
    *,
    cluster_resources: list[dict[str, Any]] | None = None,
) -> QuotaUsage:
    vmids = set(_owned_vmids(session, user_id))
    listing = (
        cluster_resources
        if cluster_resources is not None
        else proxmox_service.list_all_resources()
    )
    cores = memory_mb = disk_gb = 0
    for item in listing:
        if int(item.get("vmid") or 0) not in vmids:
            continue
        cores += int(item.get("maxcpu") or 0)
        memory_mb += int(item.get("maxmem") or 0) // _MIB
        disk_gb += int(item.get("maxdisk") or 0) // _GIB
    return QuotaUsage(
        cpu_cores=cores, memory_mb=memory_mb, disk_gb=disk_gb, instances=len(vmids)
    )


def check_quota(
    session: Session,
    user_id: uuid.UUID,
    *,
    delta_cores: int = 0,
    delta_memory_mb: int = 0,
    delta_disk_gb: int = 0,
    delta_instances: int = 0,
) -> None:
    """執法點呼叫；超限 raise ConflictError(409)。PVE 失敗 fail-open。"""
    quota = get_effective_quota(session, user_id)
    try:
        usage = get_usage(session, user_id)
    except Exception:
        logger.warning(
            "Quota usage lookup failed for user %s; skipping enforcement",
            user_id,
            exc_info=True,
        )
        return
    violations = check_quota_delta(
        usage,
        quota,
        delta_cores=delta_cores,
        delta_memory_mb=delta_memory_mb,
        delta_disk_gb=delta_disk_gb,
        delta_instances=delta_instances,
    )
    if violations:
        raise ConflictError("配額不足：" + "；".join(violations))


__all__ = [
    "AppError",
    "check_quota",
    "get_effective_quota",
    "get_usage",
]
```

- [ ] **Step 4: 建 schemas**

`backend/app/schemas/quota.py`：

```python
"""配額 API schemas。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models import QuotaScope


class ResourceQuotaCreate(BaseModel):
    scope: QuotaScope
    group_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    max_cpu_cores: int = Field(default=8, ge=1, le=256)
    max_memory_mb: int = Field(default=16384, ge=256, le=1048576)
    max_disk_gb: int = Field(default=100, ge=1, le=65536)
    max_instances: int = Field(default=5, ge=1, le=100)

    @model_validator(mode="after")
    def _validate_target(self) -> "ResourceQuotaCreate":
        if self.scope == QuotaScope.group and self.group_id is None:
            raise ValueError("scope=group requires group_id")
        if self.scope == QuotaScope.user and self.user_id is None:
            raise ValueError("scope=user requires user_id")
        return self


class ResourceQuotaUpdate(BaseModel):
    max_cpu_cores: int | None = Field(default=None, ge=1, le=256)
    max_memory_mb: int | None = Field(default=None, ge=256, le=1048576)
    max_disk_gb: int | None = Field(default=None, ge=1, le=65536)
    max_instances: int | None = Field(default=None, ge=1, le=100)


class ResourceQuotaPublic(BaseModel):
    id: uuid.UUID
    scope: QuotaScope
    group_id: uuid.UUID | None
    user_id: uuid.UUID | None
    group_name: str | None = None
    user_email: str | None = None
    max_cpu_cores: int
    max_memory_mb: int
    max_disk_gb: int
    max_instances: int
    created_at: datetime


class EffectiveQuotaPublic(BaseModel):
    max_cpu_cores: int
    max_memory_mb: int
    max_disk_gb: int
    max_instances: int


class QuotaUsagePublic(BaseModel):
    used_cpu_cores: int
    used_memory_mb: int
    used_disk_gb: int
    used_instances: int
    quota: EffectiveQuotaPublic
```

在 `backend/app/schemas/__init__.py` 加（依既有字母序 import 區塊）：

```python
from .quota import (
    EffectiveQuotaPublic,
    QuotaUsagePublic,
    ResourceQuotaCreate,
    ResourceQuotaPublic,
    ResourceQuotaUpdate,
)
```

並將五個名稱加入 `__all__`。

- [ ] **Step 5: 跑測試確認通過 + lint**

Run: `cd backend && uv run pytest tests/services/test_quota_service.py tests/services/test_quota_policy.py -v && uv run ruff check app/services/resource/ app/schemas/quota.py`
Expected: 全部 PASS、ruff 乾淨

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/resource/quota_service.py backend/app/schemas/ backend/tests/services/test_quota_service.py
git commit -m "模組E教學體驗: quota_service 與 schemas (E7)"
```

---

### Task 4: quotas API routes（admin CRUD + my-usage）

**Files:**
- Create: `backend/app/api/routes/quotas.py`
- Modify: `backend/app/api/main.py`（註冊 router）

**Interfaces:**
- Consumes: Task 3 的 `quota_service`、schemas
- Produces: `GET/POST /quotas`、`PUT/DELETE /quotas/{quota_id}`（AdminUser）、`GET /quotas/my-usage`（CurrentUser）

- [ ] **Step 1: 實作 route**

`backend/app/api/routes/quotas.py`：

```python
"""資源配額 API：admin 管理群組/個人配額；所有登入者查自己用量。"""

import logging
import uuid

from fastapi import APIRouter
from sqlmodel import select

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.exceptions import ConflictError, NotFoundError
from app.models import Group, QuotaScope, ResourceQuota, User
from app.schemas import (
    EffectiveQuotaPublic,
    QuotaUsagePublic,
    ResourceQuotaCreate,
    ResourceQuotaPublic,
    ResourceQuotaUpdate,
)
from app.schemas.common import Message
from app.services.resource import quota_service
from app.services.user import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quotas", tags=["quotas"])


def _to_public(session: SessionDep, quota: ResourceQuota) -> ResourceQuotaPublic:
    group_name = None
    user_email = None
    if quota.group_id is not None:
        group = session.get(Group, quota.group_id)
        group_name = group.name if group else None
    if quota.user_id is not None:
        user = session.get(User, quota.user_id)
        user_email = user.email if user else None
    return ResourceQuotaPublic(
        id=quota.id,
        scope=quota.scope,
        group_id=quota.group_id,
        user_id=quota.user_id,
        group_name=group_name,
        user_email=user_email,
        max_cpu_cores=quota.max_cpu_cores,
        max_memory_mb=quota.max_memory_mb,
        max_disk_gb=quota.max_disk_gb,
        max_instances=quota.max_instances,
        created_at=quota.created_at,
    )


@router.get("/my-usage", response_model=QuotaUsagePublic)
def get_my_usage(session: SessionDep, current_user: CurrentUser) -> QuotaUsagePublic:
    quota = quota_service.get_effective_quota(session, current_user.id)
    usage = quota_service.get_usage(session, current_user.id)
    return QuotaUsagePublic(
        used_cpu_cores=usage.cpu_cores,
        used_memory_mb=usage.memory_mb,
        used_disk_gb=usage.disk_gb,
        used_instances=usage.instances,
        quota=EffectiveQuotaPublic(
            max_cpu_cores=quota.max_cpu_cores,
            max_memory_mb=quota.max_memory_mb,
            max_disk_gb=quota.max_disk_gb,
            max_instances=quota.max_instances,
        ),
    )


@router.get("", response_model=list[ResourceQuotaPublic])
def list_quotas(session: SessionDep, _: AdminUser) -> list[ResourceQuotaPublic]:
    quotas = session.exec(select(ResourceQuota)).all()
    return [_to_public(session, q) for q in quotas]


@router.post("", response_model=ResourceQuotaPublic, status_code=201)
def create_quota(
    body: ResourceQuotaCreate, session: SessionDep, current_user: AdminUser
) -> ResourceQuotaPublic:
    if body.scope == QuotaScope.group:
        if session.get(Group, body.group_id) is None:
            raise NotFoundError("Group not found")
        existing = session.exec(
            select(ResourceQuota).where(ResourceQuota.group_id == body.group_id)
        ).first()
    else:
        if session.get(User, body.user_id) is None:
            raise NotFoundError("User not found")
        existing = session.exec(
            select(ResourceQuota).where(ResourceQuota.user_id == body.user_id)
        ).first()
    if existing is not None:
        raise ConflictError("此對象已有配額設定，請改用更新")

    quota = ResourceQuota(
        scope=body.scope,
        group_id=body.group_id if body.scope == QuotaScope.group else None,
        user_id=body.user_id if body.scope == QuotaScope.user else None,
        max_cpu_cores=body.max_cpu_cores,
        max_memory_mb=body.max_memory_mb,
        max_disk_gb=body.max_disk_gb,
        max_instances=body.max_instances,
    )
    session.add(quota)
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="config_update",
        details=f"Created {body.scope.value} quota for "
        f"{body.group_id or body.user_id}",
        commit=False,
    )
    session.commit()
    session.refresh(quota)
    return _to_public(session, quota)


@router.put("/{quota_id}", response_model=ResourceQuotaPublic)
def update_quota(
    quota_id: uuid.UUID,
    body: ResourceQuotaUpdate,
    session: SessionDep,
    current_user: AdminUser,
) -> ResourceQuotaPublic:
    quota = session.get(ResourceQuota, quota_id)
    if quota is None:
        raise NotFoundError("Quota not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(quota, field, value)
    session.add(quota)
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="config_update",
        details=f"Updated quota {quota_id}",
        commit=False,
    )
    session.commit()
    session.refresh(quota)
    return _to_public(session, quota)


@router.delete("/{quota_id}", response_model=Message)
def delete_quota(
    quota_id: uuid.UUID, session: SessionDep, current_user: AdminUser
) -> Message:
    quota = session.get(ResourceQuota, quota_id)
    if quota is None:
        raise NotFoundError("Quota not found")
    session.delete(quota)
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="config_update",
        details=f"Deleted quota {quota_id}",
        commit=False,
    )
    session.commit()
    return Message(message="Quota deleted")
```

注意：`audit_service.log_action` 的 `action` 參數若為 enum（`AuditAction`），比照 `governance.py` 用 `AuditAction.config_update`；實作時開啟 `backend/app/services/user/audit_service.py` 確認接受 str 或 enum，統一用既有 governance route 的寫法。

- [ ] **Step 2: 註冊 router**

`backend/app/api/main.py`：import 區塊加 `quotas`（字母序，`proxmox_config` 之後），並在 `api_router.include_router(governance.router)` 之後加：

```python
api_router.include_router(quotas.router)
```

- [ ] **Step 3: 驗證 app 可載入**

Run: `cd backend && uv run python -c "from app.api.main import api_router; print('ok')" && uv run ruff check app/api/routes/quotas.py`
Expected: `ok`、ruff 乾淨

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/
git commit -m "模組E教學體驗: 配額 CRUD 與 my-usage API (E7)"
```

---

### Task 5: 配額執法點接入（VM request / batch provision / spec change）

**Files:**
- Modify: `backend/app/services/vm/vm_request_service.py`（`create()`，約 line 296–380）
- Modify: `backend/app/services/vm/batch_provision_service.py`（`_provision_one()`，約 line 281）
- Modify: `backend/app/services/vm/spec_change_service.py`（`review()`，約 line 203 approved 分支）
- Test: `backend/tests/services/test_quota_enforcement.py`

**Interfaces:**
- Consumes: `quota_service.check_quota(session, user_id, *, delta_cores, delta_memory_mb, delta_disk_gb, delta_instances)`

- [ ] **Step 1: 寫失敗測試**

`backend/tests/services/test_quota_enforcement.py`：

```python
"""配額執法點測試：超限時 create/review 應在寫入前被 409 擋下。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import ConflictError
from app.services.vm import spec_change_service, vm_request_service


def test_vm_request_create_blocked_by_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    def _deny(session, user_id, **kwargs):
        raise ConflictError("配額不足")

    monkeypatch.setattr(
        vm_request_service.quota_service, "check_quota", _deny
    )
    request_in = SimpleNamespace(
        resource_type="lxc",
        requested_mode="manual",
        ostemplate="local:vztmpl/x.tar.zst",
        cores=2,
        memory=2048,
        rootfs_size=8,
        disk_size=None,
        mode="scheduled",
    )
    user = SimpleNamespace(id=uuid.uuid4(), email="stu@campus.edu")
    with pytest.raises(ConflictError):
        vm_request_service.create(session=None, request_in=request_in, user=user)


def test_spec_change_review_blocked_by_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    db_request = SimpleNamespace(
        id=uuid.uuid4(),
        vmid=101,
        user_id=uuid.uuid4(),
        status=spec_change_service.SpecChangeRequestStatus.pending,
        requested_cpu=8,
        current_cpu=2,
        requested_memory=None,
        current_memory=2048,
        requested_disk=None,
        current_disk=20,
    )
    monkeypatch.setattr(
        spec_change_service.spec_request_repo,
        "get_spec_change_request_by_id",
        lambda **kwargs: db_request,
    )

    def _deny(session, user_id, **kwargs):
        raise ConflictError("配額不足")

    monkeypatch.setattr(spec_change_service.quota_service, "check_quota", _deny)
    review_data = SimpleNamespace(
        status=spec_change_service.SpecChangeRequestStatus.approved,
        review_comment=None,
    )
    reviewer = SimpleNamespace(id=uuid.uuid4(), email="admin@campus.edu")

    class _S:
        def rollback(self) -> None: ...

    with pytest.raises(ConflictError):
        spec_change_service.review(
            session=_S(), request_id=db_request.id, review_data=review_data,
            reviewer=reviewer,
        )
```

注意：第一個測試依賴 `create()` 在「任何 DB 寫入之前」先呼叫 check_quota，session=None 才不會炸在別處。若 create() 前段有其他 session 使用（例如 auto mode 的 governance 查詢），把 `requested_mode` 保持 "manual" 即可繞過。若仍有前置 session 存取（`validate_request_window` 需要 start_at/end_at），將 check_quota 呼叫放在 `create()` 的 resource_type 驗證之後、mode 驗證之前，測試即可通過。

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/services/test_quota_enforcement.py -v`
Expected: FAIL（`AttributeError: quota_service`——尚未 import）

- [ ] **Step 3: 接入 vm_request_service.create**

`backend/app/services/vm/vm_request_service.py`：

頂部 import 區加：

```python
from app.services.resource import quota_service
```

在 `create()` 內、`if request_in.resource_type not in ("lxc", "vm")` 驗證之後（auto mode 區塊之前）插入：

```python
    # ---------- 配額執法（E7）：寫入前先擋 ----------
    quota_service.check_quota(
        session,
        user.id,
        delta_cores=int(request_in.cores or 0),
        delta_memory_mb=int(request_in.memory or 0),
        delta_disk_gb=int(request_in.disk_size or request_in.rootfs_size or 0),
        delta_instances=1,
    )
```

- [ ] **Step 4: 接入 batch_provision_service._provision_one**

`backend/app/services/vm/batch_provision_service.py`：

頂部 import 加 `from app.services.resource import quota_service`。

`_provision_one()` 函式開頭（docstring 之後）插入：

```python
    quota_service.check_quota(
        session,
        user_id,
        delta_cores=int(params.get("cores") or 0),
        delta_memory_mb=int(params.get("memory") or 0),
        delta_disk_gb=int(params.get("disk_size") or params.get("rootfs_size") or 0),
        delta_instances=1,
    )
```

超限會 raise ConflictError → 被 `_process_task` 的 except 捕捉 → task 標 failed、錯誤訊息含「配額不足」。單台失敗不中斷整批（既有行為）。

- [ ] **Step 5: 接入 spec_change_service.review**

`backend/app/services/vm/spec_change_service.py`：

頂部 import 加 `from app.services.resource import quota_service`。

在 `review()` 的 `try:` 內、`if review_data.status == SpecChangeRequestStatus.approved:` 分支的 `_apply_spec_changes` 呼叫**之前**插入：

```python
            quota_service.check_quota(
                session,
                db_request.user_id,
                delta_cores=max(
                    0,
                    int(db_request.requested_cpu or db_request.current_cpu or 0)
                    - int(db_request.current_cpu or 0),
                ),
                delta_memory_mb=max(
                    0,
                    int(db_request.requested_memory or db_request.current_memory or 0)
                    - int(db_request.current_memory or 0),
                ),
                delta_disk_gb=max(
                    0,
                    int(db_request.requested_disk or db_request.current_disk or 0)
                    - int(db_request.current_disk or 0),
                ),
            )
```

- [ ] **Step 6: 跑測試確認通過**

Run: `cd backend && uv run pytest tests/services/test_quota_enforcement.py tests/services/test_quota_policy.py tests/services/test_quota_service.py -v`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/vm/ backend/tests/services/test_quota_enforcement.py
git commit -m "模組E教學體驗: 配額執法點接入三條 provisioning 路徑 (E7)"
```

---

### Task 6: 教學存取共用 helper（owner / 群組老師 / admin）

**Files:**
- Create: `backend/app/services/teaching/__init__.py`
- Create: `backend/app/services/teaching/access.py`
- Modify: `backend/app/api/deps/proxmox.py`（新增 `TeachingResourceInfoDep`）
- Modify: `backend/app/api/deps/__init__.py`（匯出）
- Test: `backend/tests/services/test_teaching_access.py`

**Interfaces:**
- Produces:
  - `teaching_access.require_vm_teaching_access(session, user, vmid) -> Resource`：owner 本人、RESOURCE_OWNERSHIP_BYPASS（admin）、或 `group_repo.is_user_in_any_owned_group(instructor_id=user.id, member_user_id=owner_id)` 的老師可通過；其餘 `PermissionDeniedError`；資源不存在 `NotFoundError`
  - `TeachingResourceInfoDep = Annotated[dict, Depends(get_resource_info_teaching)]`：通過上述檢查後回傳 `proxmox_service.find_resource(vmid)`

- [ ] **Step 1: 寫失敗測試**

`backend/tests/services/test_teaching_access.py`：

```python
"""教學存取檢查測試：owner / 群組老師 / admin / 陌生人邊界。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import NotFoundError, PermissionDeniedError
from app.services.teaching import access as teaching_access

OWNER_ID = uuid.uuid4()


class _FakeSession:
    def __init__(self, resource) -> None:
        self._resource = resource

    def get(self, model: type, key: object):
        return self._resource


def _resource() -> SimpleNamespace:
    return SimpleNamespace(vmid=101, user_id=OWNER_ID)


@pytest.fixture(autouse=True)
def no_bypass(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        teaching_access, "can_bypass_resource_ownership", lambda user: False
    )
    monkeypatch.setattr(
        teaching_access.group_repo,
        "is_user_in_any_owned_group",
        lambda **kwargs: False,
    )


def test_owner_allowed() -> None:
    user = SimpleNamespace(id=OWNER_ID)
    result = teaching_access.require_vm_teaching_access(
        _FakeSession(_resource()), user, 101
    )
    assert result.vmid == 101


def test_admin_bypass_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        teaching_access, "can_bypass_resource_ownership", lambda user: True
    )
    user = SimpleNamespace(id=uuid.uuid4())
    assert teaching_access.require_vm_teaching_access(
        _FakeSession(_resource()), user, 101
    )


def test_group_teacher_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        teaching_access.group_repo,
        "is_user_in_any_owned_group",
        lambda **kwargs: True,
    )
    user = SimpleNamespace(id=uuid.uuid4())
    assert teaching_access.require_vm_teaching_access(
        _FakeSession(_resource()), user, 101
    )


def test_stranger_denied() -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    with pytest.raises(PermissionDeniedError):
        teaching_access.require_vm_teaching_access(
            _FakeSession(_resource()), user, 101
        )


def test_missing_resource_404() -> None:
    user = SimpleNamespace(id=OWNER_ID)
    with pytest.raises(NotFoundError):
        teaching_access.require_vm_teaching_access(_FakeSession(None), user, 999)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/services/test_teaching_access.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: 實作**

`backend/app/services/teaching/__init__.py`：

```python
"""教學體驗服務（模組 E）：配置分發、進度熱圖、批次規格調整、共用存取檢查。"""
```

`backend/app/services/teaching/access.py`：

```python
"""教學情境的 VM 存取檢查：owner 本人、所屬群組的老師、或 admin。

與 ``api/deps/proxmox.check_resource_ownership``（owner/admin only）的差異：
多放行「VM 擁有者所屬群組的 owner（老師）」，供 E1 重置、E2 分發、
E4 快照管理、E6 批次調整共用。
"""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.core.authorizers import can_bypass_resource_ownership
from app.exceptions import NotFoundError, PermissionDeniedError
from app.models import Resource, User
from app.repositories import group as group_repo

logger = logging.getLogger(__name__)


def require_vm_teaching_access(session: Session, user: User, vmid: int) -> Resource:
    resource = session.get(Resource, vmid)
    if resource is None:
        raise NotFoundError(f"Resource {vmid} not found")
    if resource.user_id == user.id:
        return resource
    if can_bypass_resource_ownership(user):
        return resource
    if group_repo.is_user_in_any_owned_group(
        session=session, instructor_id=user.id, member_user_id=resource.user_id
    ):
        return resource
    logger.warning(
        "User %s denied teaching access to resource %s", user.id, vmid
    )
    raise PermissionDeniedError("You don't have permission to manage this resource")
```

- [ ] **Step 4: 新增 dep**

`backend/app/api/deps/proxmox.py` 底部加：

```python
def get_resource_info_teaching(
    vmid: int,
    current_user: CurrentUser,
    session: SessionDep,
) -> dict:
    """owner / 群組老師 / admin 皆可通過的資源資訊 dep（模組 E）。"""
    from app.services.teaching import access as teaching_access

    teaching_access.require_vm_teaching_access(session, current_user, vmid)
    return proxmox_service.find_resource(vmid)


TeachingResourceInfoDep = Annotated[dict, Depends(get_resource_info_teaching)]
```

`backend/app/api/deps/__init__.py`：比照 `ResourceInfoDep` 的匯出方式加 `TeachingResourceInfoDep`（開檔確認既有 re-export 樣式後照做）。

- [ ] **Step 5: 跑測試確認通過**

Run: `cd backend && uv run pytest tests/services/test_teaching_access.py -v && uv run ruff check app/services/teaching/ app/api/deps/proxmox.py`
Expected: 全部 PASS、ruff 乾淨

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/teaching/ backend/app/api/deps/ backend/tests/services/test_teaching_access.py
git commit -m "模組E教學體驗: 教學存取共用檢查與 TeachingResourceInfoDep"
```

---

### Task 7: E1 reset_service + provision 完成點 hook

**Files:**
- Create: `backend/app/services/resource/reset_service.py`
- Modify: `backend/app/services/scheduling/coordinator.py`（`_provision_new_resource` 尾端 hook）
- Modify: `backend/app/services/vm/batch_provision_service.py`（`_process_task` 成功後 hook）
- Test: `backend/tests/services/test_reset_service.py`

**Interfaces:**
- Consumes: `proxmox_service.list_snapshots/create_snapshot/rollback_snapshot/get_status/control/find_resource`、`background_tasks.submit_sync`、`audit_service.log_action`
- Produces:
  - `reset_service.INIT_SNAPSHOT_NAME = "skylab-init"`
  - `reset_service.ensure_init_snapshot(vmid: int) -> bool`（best-effort，失敗記 warning 回 False，絕不 raise）
  - `reset_service.create_init_snapshot(session, *, vmid, resource_info, user) -> dict`（已存在 → ConflictError 409）
  - `reset_service.start_reset(session, *, vmid, resource_info, user) -> str`（無 init 快照 → BadRequestError 400；回 runner task_id `reset-{vmid}` 去重）
  - `reset_service._run_reset(vmid, node, resource_type, user_id) -> None`（背景執行本體，供測試直呼）

- [ ] **Step 1: 寫失敗測試**

`backend/tests/services/test_reset_service.py`：

```python
"""一鍵重置編排測試（mock PVE）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError, ConflictError
from app.services.resource import reset_service

USER = SimpleNamespace(id=uuid.uuid4(), email="t@campus.edu")
INFO = {"node": "pve1", "type": "qemu"}


class _FakeSession:
    def add(self, obj) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@pytest.fixture()
def pve(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"snapshots": [], "control": [], "rollback": [], "status": "running"}
    monkeypatch.setattr(
        reset_service.proxmox_service,
        "list_snapshots",
        lambda node, vmid, rtype: calls["snapshots"],
    )
    monkeypatch.setattr(
        reset_service.proxmox_service,
        "create_snapshot",
        lambda node, vmid, rtype, wait_timeout_seconds=None, **p: calls.setdefault(
            "created", []
        ).append(p.get("snapname")),
    )
    monkeypatch.setattr(
        reset_service.proxmox_service,
        "get_status",
        lambda node, vmid, rtype: {"status": calls["status"]},
    )
    monkeypatch.setattr(
        reset_service.proxmox_service,
        "control",
        lambda node, vmid, rtype, action: calls["control"].append(action),
    )
    monkeypatch.setattr(
        reset_service.proxmox_service,
        "rollback_snapshot",
        lambda node, vmid, rtype, snapname: calls["rollback"].append(snapname),
    )
    monkeypatch.setattr(
        reset_service.proxmox_service,
        "find_resource",
        lambda vmid: {"vmid": vmid, "node": "pve1", "type": "qemu"},
    )
    monkeypatch.setattr(
        reset_service.audit_service, "log_action", lambda **kwargs: None
    )
    return calls


def test_start_reset_requires_init_snapshot(pve: dict) -> None:
    pve["snapshots"] = [{"name": "current"}]
    with pytest.raises(BadRequestError):
        reset_service.start_reset(
            _FakeSession(), vmid=101, resource_info=INFO, user=USER
        )


def test_run_reset_stops_rolls_back_and_restarts(pve: dict) -> None:
    pve["status"] = "running"
    reset_service._run_reset(101, "pve1", "qemu", USER.id)
    assert pve["control"] == ["stop", "start"]
    assert pve["rollback"] == [reset_service.INIT_SNAPSHOT_NAME]


def test_run_reset_stopped_vm_stays_stopped(pve: dict) -> None:
    pve["status"] = "stopped"
    reset_service._run_reset(101, "pve1", "qemu", USER.id)
    assert pve["control"] == []
    assert pve["rollback"] == [reset_service.INIT_SNAPSHOT_NAME]


def test_create_init_snapshot_conflicts_when_exists(pve: dict) -> None:
    pve["snapshots"] = [{"name": reset_service.INIT_SNAPSHOT_NAME}]
    with pytest.raises(ConflictError):
        reset_service.create_init_snapshot(
            _FakeSession(), vmid=101, resource_info=INFO, user=USER
        )


def test_ensure_init_snapshot_swallow_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(vmid):
        raise RuntimeError("PVE down")

    monkeypatch.setattr(reset_service.proxmox_service, "find_resource", _boom)
    assert reset_service.ensure_init_snapshot(101) is False
```

注意：`_run_reset` 內部會開 `Session(engine)` 寫 audit——為了免 DB 測試，實作把 audit 寫入包在 `try/except`＋獨立函式 `_audit_reset`，測試 monkeypatch `reset_service._audit_reset`。測試檔加：

```python
@pytest.fixture(autouse=True)
def no_audit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(reset_service, "_audit_reset", lambda *a, **k: None)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/services/test_reset_service.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: 實作**

`backend/app/services/resource/reset_service.py`：

```python
"""一鍵環境重置（E1）：rollback 到受保護的 skylab-init 初始快照。

- ``ensure_init_snapshot``：provision 完成點呼叫，best-effort；失敗只記
  warning（該 VM 之後「重置不可用」，可由老師/admin 補建）。
- ``start_reset``：API 進入點，驗證前置條件後丟背景任務（202）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from sqlmodel import Session

from app.exceptions import BadRequestError, ConflictError
from app.infrastructure.worker import background_tasks
from app.services.proxmox import proxmox_service
from app.services.user import audit_service

logger = logging.getLogger(__name__)

INIT_SNAPSHOT_NAME = "skylab-init"
INIT_SNAPSHOT_DESCRIPTION = "SkyLab 初始快照（受保護）"
INIT_SNAPSHOT_WAIT_SECONDS = 120.0


def _rtype(resource_info: dict) -> Literal["qemu", "lxc"]:
    return "lxc" if str(resource_info.get("type") or "") == "lxc" else "qemu"


def _has_init_snapshot(node: str, vmid: int, rtype: Literal["qemu", "lxc"]) -> bool:
    snapshots = proxmox_service.list_snapshots(node, vmid, rtype)
    return any(s.get("name") == INIT_SNAPSHOT_NAME for s in snapshots)


def ensure_init_snapshot(vmid: int) -> bool:
    """Provision 完成點 hook；失敗不阻斷 provision。"""
    try:
        info = proxmox_service.find_resource(vmid)
        node = str(info["node"])
        rtype = _rtype(info)
        if _has_init_snapshot(node, vmid, rtype):
            return True
        proxmox_service.create_snapshot(
            node,
            vmid,
            rtype,
            wait_timeout_seconds=INIT_SNAPSHOT_WAIT_SECONDS,
            snapname=INIT_SNAPSHOT_NAME,
            description=INIT_SNAPSHOT_DESCRIPTION,
        )
        logger.info("Init snapshot created for vmid=%s", vmid)
        return True
    except Exception:
        logger.warning(
            "Init snapshot failed for vmid=%s (reset unavailable until"
            " an instructor re-creates it)",
            vmid,
            exc_info=True,
        )
        return False


def create_init_snapshot(
    session: Session, *, vmid: int, resource_info: dict, user
) -> dict:
    """老師/admin 為舊 VM 補建初始快照；已存在回 409。"""
    node = str(resource_info["node"])
    rtype = _rtype(resource_info)
    if _has_init_snapshot(node, vmid, rtype):
        raise ConflictError("初始快照 skylab-init 已存在")
    proxmox_service.create_snapshot(
        node,
        vmid,
        rtype,
        wait_timeout_seconds=INIT_SNAPSHOT_WAIT_SECONDS,
        snapname=INIT_SNAPSHOT_NAME,
        description=INIT_SNAPSHOT_DESCRIPTION,
    )
    audit_service.log_action(
        session=session,
        user_id=user.id,
        vmid=vmid,
        action="snapshot_create",
        details="Created protected init snapshot skylab-init",
    )
    return {"message": "初始快照已建立", "snapname": INIT_SNAPSHOT_NAME}


def _audit_reset(vmid: int, user_id: uuid.UUID, *, ok: bool, detail: str) -> None:
    """背景任務內寫 audit（獨立 session；失敗吞掉）。"""
    from app.core.db import engine  # noqa: PLC0415 — 測試環境不一定有 DB

    try:
        with Session(engine) as session:
            audit_service.log_action(
                session=session,
                user_id=user_id,
                vmid=vmid,
                action="snapshot_rollback",
                details=detail,
            )
    except Exception:
        logger.warning("Failed to audit reset for vmid=%s", vmid, exc_info=True)


def _run_reset(
    vmid: int, node: str, rtype: Literal["qemu", "lxc"], user_id: uuid.UUID
) -> None:
    """背景任務本體：記電源狀態 → 強制停機 → rollback → 原狀態恢復。"""
    try:
        status = proxmox_service.get_status(node, vmid, rtype)
        was_running = str(status.get("status") or "").lower() == "running"
        if was_running:
            proxmox_service.control(node, vmid, rtype, "stop")
        proxmox_service.rollback_snapshot(node, vmid, rtype, INIT_SNAPSHOT_NAME)
        if was_running:
            proxmox_service.control(node, vmid, rtype, "start")
        _audit_reset(
            vmid, user_id, ok=True,
            detail=f"Reset to {INIT_SNAPSHOT_NAME} (was_running={was_running})",
        )
        logger.info("Reset vmid=%s to init snapshot", vmid)
    except Exception as exc:
        _audit_reset(vmid, user_id, ok=False, detail=f"Reset failed: {exc}")
        logger.exception("Reset failed for vmid=%s", vmid)
        raise


def start_reset(session: Session, *, vmid: int, resource_info: dict, user) -> str:
    node = str(resource_info["node"])
    rtype = _rtype(resource_info)
    if not _has_init_snapshot(node, vmid, rtype):
        raise BadRequestError(
            "此資源沒有 skylab-init 初始快照，無法重置；請老師或管理員先補建"
        )
    audit_service.log_action(
        session=session,
        user_id=user.id,
        vmid=vmid,
        action="snapshot_rollback",
        details="Requested reset to init snapshot",
    )
    task_id = background_tasks.submit_sync(
        _run_reset,
        vmid,
        node,
        rtype,
        user.id,
        name=f"reset-vm:{vmid}",
        task_id=f"reset-{vmid}",
    )
    return task_id
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && uv run pytest tests/services/test_reset_service.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Hook 兩條 provision 完成點**

`backend/app/services/scheduling/coordinator.py`：在 `_provision_new_resource` 的 Phase 3 `with Session(engine) as finish_session:` 區塊結束後、`logger.info("Provisioned request ...")` 之前插入：

```python
    # E1：provision 完成即建受保護初始快照（best-effort，不阻斷）
    from app.services.resource import reset_service  # noqa: PLC0415 — 避免 import cycle

    reset_service.ensure_init_snapshot(new_vmid)
```

`backend/app/services/vm/batch_provision_service.py`：在 `_process_task` 的 `bp_repo.update_task_done(...)` 前（`vmid = _provision_one(...)` 成功之後）插入：

```python
        # E1：批量建立完成點也建初始快照（best-effort）
        from app.services.resource import reset_service  # noqa: PLC0415

        reset_service.ensure_init_snapshot(vmid)
```

- [ ] **Step 6: 驗證 import + 相關既有測試**

Run: `cd backend && uv run pytest tests/services/test_reset_service.py tests/services/test_provision_pool.py -v && uv run ruff check app/services/`
Expected: PASS、ruff 乾淨

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/ backend/tests/services/test_reset_service.py
git commit -m "模組E教學體驗: 一鍵重置服務與初始快照 hook (E1)"
```

---

### Task 8: E1 reset / init-snapshot API

**Files:**
- Modify: `backend/app/api/routes/resource_details.py`
- Modify: `backend/app/schemas/resource.py`（新增 `ResetAcceptedResponse`，比照既有 schema 樣式）
- Modify: `backend/app/schemas/__init__.py`

**Interfaces:**
- Consumes: Task 6 `TeachingResourceInfoDep`、Task 7 `reset_service`
- Produces: `POST /resources/{vmid}/reset` → 202 `{message, task_id}`；`POST /resources/{vmid}/init-snapshot`（InstructorUser）→ 201

- [ ] **Step 1: schema**

`backend/app/schemas/resource.py` 底部加：

```python
class ResetAcceptedResponse(BaseModel):
    message: str
    task_id: str
```

（若該檔用 SQLModel/BaseModel 混用，比照鄰近 class 的基底。）`schemas/__init__.py` 匯出 `ResetAcceptedResponse`。

- [ ] **Step 2: routes**

`backend/app/api/routes/resource_details.py`：

import 區調整：

```python
from app.api.deps import (
    AdminUser,
    CurrentUser,
    InstructorUser,
    ResourceInfoDep,
    SessionDep,
    TeachingResourceInfoDep,
)
from app.schemas import ResetAcceptedResponse  # 併入既有 schemas import
from app.services.resource import reset_service
```

檔尾加：

```python
@router.post(
    "/{vmid}/reset", response_model=ResetAcceptedResponse, status_code=202
)
def reset_resource(
    vmid: int,
    resource_info: TeachingResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    task_id = reset_service.start_reset(
        session, vmid=vmid, resource_info=resource_info, user=current_user
    )
    return ResetAcceptedResponse(
        message="重置任務已排入背景執行", task_id=task_id
    )


@router.post("/{vmid}/init-snapshot", status_code=201)
def create_init_snapshot(
    vmid: int,
    resource_info: TeachingResourceInfoDep,
    session: SessionDep,
    current_user: InstructorUser,
):
    return reset_service.create_init_snapshot(
        session, vmid=vmid, resource_info=resource_info, user=current_user
    )
```

- [ ] **Step 3: 驗證**

Run: `cd backend && uv run python -c "from app.api.main import api_router; print('ok')" && uv run ruff check app/api/ app/schemas/`
Expected: `ok`、ruff 乾淨

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/ backend/app/schemas/
git commit -m "模組E教學體驗: reset 與 init-snapshot API (E1)"
```

---

### Task 9: E4 快照權限收斂 / skylab-init 保護 / 數量上限

**Files:**
- Modify: `backend/app/services/network/snapshot_service.py`
- Modify: `backend/app/api/routes/resource_details.py`（snapshots 端點改用 `TeachingResourceInfoDep` 並傳 user）
- Test: `backend/tests/services/test_snapshot_service_guards.py`

**Interfaces:**
- Consumes: `governance_repo.get_governance_config`（`student_snapshot_max_count`）、`is_admin`（`app.core.permissions`）、Task 7 `INIT_SNAPSHOT_NAME`
- Produces: `snapshot_service.create_snapshot(..., user)` 與 `delete_snapshot(..., user)` 簽名新增 `user` 參數（`rollback_snapshot`、`list_snapshots` 不變）

- [ ] **Step 1: 寫失敗測試**

`backend/tests/services/test_snapshot_service_guards.py`：

```python
"""快照守門測試：保留名、上限、init 保護。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError, ConflictError, PermissionDeniedError
from app.services.network import snapshot_service

INFO = {"node": "pve1", "type": "qemu"}
STUDENT = SimpleNamespace(id=uuid.uuid4(), email="s@campus.edu")


class _FakeSession:
    def add(self, obj) -> None: ...
    def commit(self) -> None: ...


@pytest.fixture()
def pve(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"snapshots": [], "created": [], "deleted": []}
    monkeypatch.setattr(
        snapshot_service.proxmox_service,
        "list_snapshots",
        lambda node, vmid, rtype: calls["snapshots"],
    )
    monkeypatch.setattr(
        snapshot_service.proxmox_service,
        "create_snapshot",
        lambda node, vmid, rtype, **p: calls["created"].append(p.get("snapname"))
        or "UPID:x",
    )
    monkeypatch.setattr(
        snapshot_service.proxmox_service,
        "delete_snapshot",
        lambda node, vmid, rtype, snapname: calls["deleted"].append(snapname)
        or "UPID:x",
    )
    monkeypatch.setattr(
        snapshot_service.audit_service, "log_action", lambda **kwargs: None
    )
    monkeypatch.setattr(snapshot_service, "_is_admin", lambda user: False)
    monkeypatch.setattr(
        snapshot_service,
        "_snapshot_max_count",
        lambda session: 3,
    )
    return calls


def test_create_reserved_name_rejected(pve: dict) -> None:
    with pytest.raises(BadRequestError):
        snapshot_service.create_snapshot(
            session=_FakeSession(), vmid=101, snapname="skylab-init",
            description=None, vmstate=False, resource_info=INFO,
            user_id=STUDENT.id, user=STUDENT,
        )


def test_create_over_limit_conflicts(pve: dict) -> None:
    pve["snapshots"] = [
        {"name": "a"}, {"name": "b"}, {"name": "c"},
        {"name": "skylab-init"}, {"name": "current"},
    ]
    with pytest.raises(ConflictError):
        snapshot_service.create_snapshot(
            session=_FakeSession(), vmid=101, snapname="d",
            description=None, vmstate=False, resource_info=INFO,
            user_id=STUDENT.id, user=STUDENT,
        )


def test_create_within_limit_ok(pve: dict) -> None:
    pve["snapshots"] = [{"name": "a"}, {"name": "skylab-init"}]
    result = snapshot_service.create_snapshot(
        session=_FakeSession(), vmid=101, snapname="b",
        description=None, vmstate=False, resource_info=INFO,
        user_id=STUDENT.id, user=STUDENT,
    )
    assert pve["created"] == ["b"]
    assert "task_id" in result


def test_delete_init_snapshot_forbidden(pve: dict) -> None:
    with pytest.raises(PermissionDeniedError):
        snapshot_service.delete_snapshot(
            session=_FakeSession(), vmid=101, snapname="skylab-init",
            resource_info=INFO, user_id=STUDENT.id, user=STUDENT,
        )


def test_admin_can_delete_init_snapshot(
    pve: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshot_service, "_is_admin", lambda user: True)
    snapshot_service.delete_snapshot(
        session=_FakeSession(), vmid=101, snapname="skylab-init",
        resource_info=INFO, user_id=STUDENT.id, user=STUDENT,
    )
    assert pve["deleted"] == ["skylab-init"]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/services/test_snapshot_service_guards.py -v`
Expected: FAIL（TypeError: unexpected keyword `user` 等）

- [ ] **Step 3: 改 snapshot_service**

`backend/app/services/network/snapshot_service.py`：

頂部 import 加：

```python
from app.core.permissions import is_admin as _is_admin
from app.exceptions import BadRequestError, ConflictError, PermissionDeniedError
from app.repositories import governance as governance_repo
```

加 helper 與常數（`INIT_SNAPSHOT_NAME` 直接定義字串常數，避免與 reset_service 互相 import）：

```python
INIT_SNAPSHOT_NAME = "skylab-init"


def _snapshot_max_count(session: Session) -> int:
    return int(
        governance_repo.get_governance_config(session=session).student_snapshot_max_count
    )
```

`create_snapshot` 簽名加 `user`（放 `user_id` 之後），函式開頭插入守門：

```python
    if snapname == INIT_SNAPSHOT_NAME and not _is_admin(user):
        raise BadRequestError("skylab-init 為系統保留快照名稱")
    if not _is_admin(user):
        existing = proxmox_service.list_snapshots(node, vmid, resource_type)
        countable = [
            s
            for s in existing
            if s.get("name") not in ("current", INIT_SNAPSHOT_NAME)
        ]
        limit = _snapshot_max_count(session)
        if len(countable) >= limit:
            raise ConflictError(
                f"快照數量已達上限（{limit}），請先刪除舊快照再建立"
            )
```

注意：守門要放在既有 `node = resource_info["node"]` 賦值之後。

`delete_snapshot` 簽名加 `user`，開頭插入：

```python
    if snapname == INIT_SNAPSHOT_NAME and not _is_admin(user):
        raise PermissionDeniedError("skylab-init 受保護，僅管理員可刪除")
```

- [ ] **Step 4: 改 routes**

`backend/app/api/routes/resource_details.py` 的四個 snapshots 端點：

- `list_snapshots`/`create_snapshot`/`delete_snapshot`/`rollback_snapshot` 的參數 `resource_info: ResourceInfoDep` 全改為 `resource_info: TeachingResourceInfoDep`（owner/老師/admin 可用）。
- `create_snapshot` 與 `delete_snapshot` 呼叫 service 時多傳 `user=current_user`。

- [ ] **Step 5: 跑測試確認通過**

Run: `cd backend && uv run pytest tests/services/test_snapshot_service_guards.py -v && uv run python -c "from app.api.main import api_router; print('ok')"`
Expected: 全部 PASS、`ok`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/network/snapshot_service.py backend/app/api/routes/resource_details.py backend/tests/services/test_snapshot_service_guards.py
git commit -m "模組E教學體驗: 快照權限收斂與 init 保護/上限 (E4)"
```

---

### Task 10: E8 snapshot_cleanup_policy 純函式

**Files:**
- Create: `backend/app/services/governance/snapshot_cleanup_policy.py`
- Test: `backend/tests/services/test_snapshot_cleanup_policy.py`

**Interfaces:**
- Produces: `is_cleanup_eligible(*, name: str | None, snaptime: int | None, now: datetime, retention_days: int) -> bool`

- [ ] **Step 1: 寫失敗測試**

`backend/tests/services/test_snapshot_cleanup_policy.py`：

```python
"""快照清理資格純函式測試。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.governance.snapshot_cleanup_policy import is_cleanup_eligible

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


def _ts(days_ago: int) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp())


def test_old_snapshot_eligible() -> None:
    assert is_cleanup_eligible(
        name="snap-1", snaptime=_ts(8), now=NOW, retention_days=7
    )


def test_fresh_snapshot_not_eligible() -> None:
    assert not is_cleanup_eligible(
        name="snap-1", snaptime=_ts(3), now=NOW, retention_days=7
    )


def test_init_snapshot_protected() -> None:
    assert not is_cleanup_eligible(
        name="skylab-init", snaptime=_ts(100), now=NOW, retention_days=7
    )


def test_mining_evidence_protected() -> None:
    assert not is_cleanup_eligible(
        name="mining-202607011200", snaptime=_ts(100), now=NOW, retention_days=7
    )


def test_current_pseudo_snapshot_skipped() -> None:
    assert not is_cleanup_eligible(
        name="current", snaptime=None, now=NOW, retention_days=7
    )


def test_missing_snaptime_skipped() -> None:
    assert not is_cleanup_eligible(
        name="snap-1", snaptime=None, now=NOW, retention_days=7
    )
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/services/test_snapshot_cleanup_policy.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: 實作**

`backend/app/services/governance/snapshot_cleanup_policy.py`：

```python
"""快照自動清理資格判定（純函式，無 I/O）。

受保護不清：``skylab-init`` 初始快照、``mining-*`` 存證快照、
Proxmox 的 ``current`` 偽快照、缺 snaptime 的條目。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

PROTECTED_NAMES = ("skylab-init", "current")
PROTECTED_PREFIXES = ("mining-",)


def is_cleanup_eligible(
    *,
    name: str | None,
    snaptime: int | None,
    now: datetime,
    retention_days: int,
) -> bool:
    if not name or name in PROTECTED_NAMES:
        return False
    if any(name.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return False
    if snaptime is None:
        return False
    taken_at = datetime.fromtimestamp(int(snaptime), tz=timezone.utc)
    return now - taken_at > timedelta(days=retention_days)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && uv run pytest tests/services/test_snapshot_cleanup_policy.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/governance/snapshot_cleanup_policy.py backend/tests/services/test_snapshot_cleanup_policy.py
git commit -m "模組E教學體驗: 快照清理資格純函式 (E8)"
```

---

### Task 11: E8 snapshot_cleanup_service + scheduler 掛入 + governance schema 欄位

**Files:**
- Create: `backend/app/services/governance/snapshot_cleanup_service.py`
- Modify: `backend/app/services/scheduling/coordinator.py`（`run_scheduler` tasks + wrapper）
- Modify: `backend/app/schemas/monitoring.py`（`GovernanceConfigPublic`/`GovernanceConfigUpdate` 加三欄位）
- Test: `backend/tests/services/test_snapshot_cleanup_service.py`

**Interfaces:**
- Consumes: Task 10 純函式、`lifecycle_service` 的 `_pve_resource_map`/`_send_owner_email` 模式（複製樣式，不跨模組 import 私有函式）
- Produces: `snapshot_cleanup_service.process_snapshot_cleanup() -> int`（回傳刪除數）

- [ ] **Step 1: 寫失敗測試**

`backend/tests/services/test_snapshot_cleanup_service.py`：

```python
"""快照自動清理掃描測試（mock DB / PVE / email）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.governance import snapshot_cleanup_service as svc

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


def _ts(days_ago: int) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp())


def _resource(vmid: int) -> SimpleNamespace:
    return SimpleNamespace(
        vmid=vmid,
        user_id=uuid.uuid4(),
        user=SimpleNamespace(email="s@campus.edu", full_name="學生"),
    )


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"deleted": [], "emails": []}
    config = SimpleNamespace(
        snapshot_cleanup_enabled=True, snapshot_retention_days=7
    )
    monkeypatch.setattr(svc, "_utc_now", lambda: NOW)
    monkeypatch.setattr(svc, "_get_config", lambda session: config)
    monkeypatch.setattr(
        svc, "_list_scan_batch", lambda session, cursor, limit: [_resource(101)]
    )
    monkeypatch.setattr(
        svc,
        "_pve_resource_map",
        lambda: {101: {"vmid": 101, "node": "pve1", "type": "qemu"}},
    )
    monkeypatch.setattr(
        svc.proxmox_service,
        "list_snapshots",
        lambda node, vmid, rtype: [
            {"name": "old", "snaptime": _ts(10)},
            {"name": "fresh", "snaptime": _ts(1)},
            {"name": "skylab-init", "snaptime": _ts(30)},
        ],
    )
    monkeypatch.setattr(
        svc.proxmox_service,
        "delete_snapshot",
        lambda node, vmid, rtype, snapname: calls["deleted"].append(snapname),
    )
    monkeypatch.setattr(svc, "_audit_and_notify", lambda *a, **k: calls[
        "emails"
    ].append(a[1] if len(a) > 1 else None))
    monkeypatch.setattr(svc, "_reset_cursor", lambda: None)
    return calls


def test_only_eligible_snapshots_deleted(harness: dict) -> None:
    deleted = svc.process_snapshot_cleanup()
    assert deleted == 1
    assert harness["deleted"] == ["old"]


def test_disabled_config_noop(
    harness: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        svc,
        "_get_config",
        lambda session: SimpleNamespace(
            snapshot_cleanup_enabled=False, snapshot_retention_days=7
        ),
    )
    assert svc.process_snapshot_cleanup() == 0
    assert harness["deleted"] == []
```

注意：`process_snapshot_cleanup` 內 `with Session(engine)` 需要 DB engine——為可測性，實作把 Session 開啟包在 `_get_config`/`_list_scan_batch`/`_audit_and_notify` 三個可 patch 的函式裡，主流程不直接開 Session（見 Step 3 實作）。若 `Session(engine)` 在 import 期不觸發連線（SQLModel 為 lazy），保留主流程開 Session 亦可，但測試須連同 patch `svc.Session`。以 Step 3 的寫法為準。

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/services/test_snapshot_cleanup_service.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: 實作 service**

`backend/app/services/governance/snapshot_cleanup_service.py`：

```python
"""快照自動清理（E8）：掃描學生 VM，刪除超過保留天數的一般快照。

資格判定在 ``snapshot_cleanup_policy`` 純函式。每 tick 至多掃
``SNAPSHOT_CLEANUP_BATCH_SIZE`` 台，以 module-level vmid 游標輪替，
掃完一輪歸零重來。刪除後寫 audit log 並 email 通知 VM 擁有者
（email 失敗吞掉，絕不使排程 task 崩潰）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from app.models import Resource, User, UserRole
from app.services.governance.snapshot_cleanup_policy import is_cleanup_eligible
from app.services.proxmox import proxmox_service
from app.services.user import audit_service
from app.utils import send_email

logger = logging.getLogger(__name__)

SNAPSHOT_CLEANUP_BATCH_SIZE = 20

_cursor_vmid: int = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _pve_resource_map() -> dict[int, dict[str, Any]]:
    return {
        int(r["vmid"]): r
        for r in proxmox_service.list_all_resources()
        if r.get("vmid") is not None
    }


def _get_config(session: Session) -> Any:
    from app.repositories import governance as governance_repo  # noqa: PLC0415

    return governance_repo.get_governance_config(session=session)


def _list_scan_batch(session: Session, cursor: int, limit: int) -> list[Resource]:
    """學生擁有、vmid 大於游標的資源，一批最多 limit 台。"""
    stmt = (
        select(Resource)
        .join(User, User.id == Resource.user_id)  # type: ignore[arg-type]
        .where(User.role == UserRole.student, Resource.vmid > cursor)
        .order_by(Resource.vmid)  # type: ignore[arg-type]
        .limit(limit)
    )
    return list(session.exec(stmt).all())


def _reset_cursor() -> None:
    global _cursor_vmid
    _cursor_vmid = 0


def _audit_and_notify(
    session: Session, resource: Resource, snapname: str, retention_days: int
) -> None:
    audit_service.log_action(
        session=session,
        user_id=None,
        vmid=resource.vmid,
        action="snapshot_delete",
        details=f"Auto-cleaned snapshot '{snapname}' (>{retention_days}d)",
        commit=False,
    )
    user = resource.user
    if user is None or not user.email:
        return
    try:
        send_email(
            email_to=str(user.email),
            subject=f"[SkyLab] 資源 VMID {resource.vmid} 的過期快照已自動清理",
            html_content=(
                f"<p>您的資源（VMID {resource.vmid}）快照 <b>{snapname}</b> "
                f"已超過保留天數（{retention_days} 天），系統已自動刪除。</p>"
                "<p>skylab-init 初始快照不受影響。</p>"
            ),
        )
    except Exception:
        logger.warning(
            "Failed to send snapshot cleanup email for vmid=%s", resource.vmid
        )


def process_snapshot_cleanup() -> int:
    """Scheduler tick：回傳本 tick 刪除的快照數。"""
    global _cursor_vmid
    try:
        deleted = 0
        now = _utc_now()
        from app.core.db import engine  # noqa: PLC0415 — 測試環境不一定有 DB

        with Session(engine) as session:
            config = _get_config(session)
            if not config.snapshot_cleanup_enabled:
                return 0
            batch = _list_scan_batch(
                session, _cursor_vmid, SNAPSHOT_CLEANUP_BATCH_SIZE
            )
            if not batch:
                _reset_cursor()
                return 0
            _cursor_vmid = int(batch[-1].vmid)
            pve_map = _pve_resource_map()

            for resource in batch:
                pve_info = pve_map.get(resource.vmid)
                if pve_info is None:
                    continue
                node = str(pve_info.get("node") or "")
                rtype = "lxc" if str(pve_info.get("type") or "") == "lxc" else "qemu"
                try:
                    snapshots = proxmox_service.list_snapshots(
                        node, resource.vmid, rtype
                    )
                    for snap in snapshots:
                        if not is_cleanup_eligible(
                            name=snap.get("name"),
                            snaptime=snap.get("snaptime"),
                            now=now,
                            retention_days=config.snapshot_retention_days,
                        ):
                            continue
                        proxmox_service.delete_snapshot(
                            node, resource.vmid, rtype, str(snap.get("name"))
                        )
                        _audit_and_notify(
                            session,
                            resource,
                            str(snap.get("name")),
                            config.snapshot_retention_days,
                        )
                        deleted += 1
                        session.commit()
                except Exception:
                    session.rollback()
                    logger.exception(
                        "Snapshot cleanup failed for vmid=%s", resource.vmid
                    )
        return deleted
    except Exception:
        logger.exception("process_snapshot_cleanup failed")
        return 0
```

實作時注意：測試 patch 了 `_get_config`/`_list_scan_batch`/`_audit_and_notify`，但主流程仍會 `Session(engine)`。`app.core.db.engine` 建立不需連線（lazy），`with Session(engine)` 在無查詢時不會開連線 —— patch 後所有 DB 存取都被替走，測試可過。若實測發現 `Session(engine)` 進入時即連線，把 `with Session(engine) as session:` 改為由可 patch 的 `_open_session()` helper 提供。

- [ ] **Step 4: 掛入 scheduler**

`backend/app/services/scheduling/coordinator.py`：

`run_scheduler` 的 tasks list（`process_mining_detection` 之後）加：

```python
            ScheduledTask(
                name="process_snapshot_cleanup",
                handler=process_snapshot_cleanup_task,
            ),
```

檔尾 wrapper 區（`process_mining_detection_task` 之後）加：

```python
def process_snapshot_cleanup_task() -> int:
    """Scheduler tick：快照自動清理（超過保留天數的一般快照）。"""
    from app.services.governance import (
        snapshot_cleanup_service,  # noqa: PLC0415 — 避免 import cycle
    )

    return snapshot_cleanup_service.process_snapshot_cleanup()
```

- [ ] **Step 5: governance schemas 加欄位**

`backend/app/schemas/monitoring.py`：

`GovernanceConfigPublic` 的 `provision_max_concurrency: int` 之後加：

```python
    snapshot_cleanup_enabled: bool
    snapshot_retention_days: int
    student_snapshot_max_count: int
```

`GovernanceConfigUpdate` 的 `provision_max_concurrency` 之後加：

```python
    snapshot_cleanup_enabled: bool | None = None
    snapshot_retention_days: int | None = Field(default=None, ge=1, le=90)
    student_snapshot_max_count: int | None = Field(default=None, ge=1, le=10)
```

- [ ] **Step 6: 跑測試確認通過**

Run: `cd backend && uv run pytest tests/services/test_snapshot_cleanup_service.py tests/services/test_snapshot_cleanup_policy.py -v && uv run python -c "from app.services.scheduling.coordinator import run_scheduler; print('ok')"`
Expected: 全部 PASS、`ok`

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/ backend/app/schemas/monitoring.py backend/tests/services/test_snapshot_cleanup_service.py
git commit -m "模組E教學體驗: 快照自動清理排程與治理欄位 (E8)"
```

---

### Task 12: E2 infrastructure guest.py（agent file-write / pct push）

**Files:**
- Create: `backend/app/infrastructure/proxmox/guest.py`
- Test: `backend/tests/infrastructure/test_guest_file_write.py`

**Interfaces:**
- Consumes: `get_proxmox_api`、`get_proxmox_settings`、`get_active_host`（`app.infrastructure.proxmox`）、`create_password_client`/`exec_command`（`app.infrastructure.ssh`）
- Produces:
  - `guest.MAX_CONFIG_FILE_BYTES = 1_048_576`
  - `guest.validate_target_path(path: str) -> None`（非絕對路徑/含 `..` → BadRequestError）
  - `guest.write_file_qemu(node: str, vmid: int, path: str, content: bytes) -> None`（先 agent ping；失敗 raise AppError 400 帶可讀訊息「guest agent 未回應」）
  - `guest.write_file_lxc(node: str, vmid: int, path: str, content: bytes, *, perms: str = "0644") -> None`（SSH + SFTP 暫存 + `pct push --perms` + 清理）

- [ ] **Step 1: 寫失敗測試**

`backend/tests/infrastructure/test_guest_file_write.py`：

```python
"""guest 檔案寫入測試（mock proxmoxer / SSH）。"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from app.exceptions import AppError, BadRequestError
from app.infrastructure.proxmox import guest


class _AgentApi:
    """記錄 agent 呼叫的假 proxmoxer 鏈。"""

    def __init__(self, calls: dict, ping_fails: bool = False) -> None:
        self._calls = calls
        self._ping_fails = ping_fails

    def nodes(self, node):
        self._calls["node"] = node
        return self

    def qemu(self, vmid):
        self._calls["vmid"] = vmid
        return self

    def agent(self, cmd):
        self._calls.setdefault("agent_cmds", []).append(cmd)
        self._current = cmd
        return self

    def post(self, **params):
        if self._current == "ping" and self._ping_fails:
            raise RuntimeError("agent not running")
        self._calls.setdefault("posts", []).append((self._current, params))
        return {}


def test_validate_target_path_rejects_relative() -> None:
    with pytest.raises(BadRequestError):
        guest.validate_target_path("etc/app.conf")


def test_validate_target_path_rejects_traversal() -> None:
    with pytest.raises(BadRequestError):
        guest.validate_target_path("/etc/../root/x")


def test_write_file_qemu_base64_and_encode_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict = {}
    monkeypatch.setattr(guest, "get_proxmox_api", lambda: _AgentApi(calls))
    guest.write_file_qemu("pve1", 101, "/etc/app.conf", b"hello")
    cmd, params = calls["posts"][-1]
    assert cmd == "file-write"
    assert params["file"] == "/etc/app.conf"
    assert base64.b64decode(params["content"]) == b"hello"
    assert params["encode"] == 0
    assert "ping" in calls["agent_cmds"]


def test_write_file_qemu_agent_down_readable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        guest, "get_proxmox_api", lambda: _AgentApi({}, ping_fails=True)
    )
    with pytest.raises(AppError) as exc_info:
        guest.write_file_qemu("pve1", 101, "/etc/app.conf", b"hello")
    assert "guest agent" in exc_info.value.message.lower()


def test_write_file_lxc_pushes_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[str] = []
    written: dict = {}

    class _Sftp:
        def file(self, path, mode):
            written["path"] = path

            class _F:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    return False

                def write(self_inner, data):
                    written["data"] = data

            return _F()

        def close(self) -> None: ...

    class _Client:
        def open_sftp(self):
            return _Sftp()

        def close(self) -> None:
            written["closed"] = True

    monkeypatch.setattr(guest, "_node_ssh_client", lambda: _Client())
    monkeypatch.setattr(
        guest,
        "exec_command",
        lambda client, cmd, timeout=None: executed.append(cmd) or (0, "", ""),
    )
    guest.write_file_lxc("pve1", 102, "/etc/app.conf", b"hello")
    assert written["data"] == b"hello"
    assert any("pct push 102" in cmd for cmd in executed)
    assert any(cmd.startswith("rm -f ") for cmd in executed)
    assert written.get("closed") is True


def test_write_file_lxc_nonzero_exit_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Sftp:
        def file(self, path, mode):
            class _F:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    return False

                def write(self_inner, data): ...

            return _F()

        def close(self) -> None: ...

    class _Client:
        def open_sftp(self):
            return _Sftp()

        def close(self) -> None: ...

    monkeypatch.setattr(guest, "_node_ssh_client", lambda: _Client())

    def _exec(client, cmd, timeout=None):
        if "pct push" in cmd:
            return 1, "", "CT 102 not running"
        return 0, "", ""

    monkeypatch.setattr(guest, "exec_command", _exec)
    with pytest.raises(AppError) as exc_info:
        guest.write_file_lxc("pve1", 102, "/etc/app.conf", b"hello")
    assert "not running" in exc_info.value.message
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/infrastructure/test_guest_file_write.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: 實作**

`backend/app/infrastructure/proxmox/guest.py`：

```python
"""Guest 內檔案寫入：QEMU 走 guest agent file-write，LXC 走 node SSH pct push。

- QEMU：POST /nodes/{node}/qemu/{vmid}/agent/file-write。內容自行 base64
  並帶 ``encode=0``（二進位安全）。前置 agent ping，失敗回可讀 400。
- LXC：SSH 至 active host（與 script_deploy_service 相同的單主機假設），
  SFTP 寫暫存檔 → ``pct push --perms`` → 清理暫存。
"""

from __future__ import annotations

import base64
import logging
import shlex
import uuid

from app.exceptions import AppError, BadRequestError
from app.infrastructure.proxmox import (
    get_active_host,
    get_proxmox_api,
    get_proxmox_settings,
)
from app.infrastructure.ssh import create_password_client, exec_command

logger = logging.getLogger(__name__)

MAX_CONFIG_FILE_BYTES = 1_048_576  # 1 MB


def validate_target_path(path: str) -> None:
    if not path.startswith("/"):
        raise BadRequestError("目標路徑必須為絕對路徑")
    if ".." in path.split("/"):
        raise BadRequestError("目標路徑不可包含 ..")


def _ping_agent(node: str, vmid: int) -> None:
    try:
        get_proxmox_api().nodes(node).qemu(vmid).agent("ping").post()
    except Exception as exc:
        raise AppError(
            f"VM {vmid} 的 QEMU guest agent 未回應（可能未安裝 agent 或 VM 未開機）",
            400,
        ) from exc


def write_file_qemu(node: str, vmid: int, path: str, content: bytes) -> None:
    validate_target_path(path)
    _ping_agent(node, vmid)
    encoded = base64.b64encode(content).decode("ascii")
    get_proxmox_api().nodes(node).qemu(vmid).agent("file-write").post(
        file=path, content=encoded, encode=0
    )
    logger.info("Wrote %d bytes to %s on VM %s via guest agent", len(content), path, vmid)


def _node_ssh_client():
    cfg = get_proxmox_settings()
    host = get_active_host()
    ssh_user = cfg.user.split("@")[0] if "@" in cfg.user else cfg.user
    return create_password_client(host, 22, ssh_user, cfg.password, timeout=30)


def write_file_lxc(
    node: str, vmid: int, path: str, content: bytes, *, perms: str = "0644"
) -> None:
    validate_target_path(path)
    client = _node_ssh_client()
    tmp_path = f"/tmp/skylab-push-{uuid.uuid4().hex}"
    try:
        sftp = client.open_sftp()
        try:
            with sftp.file(tmp_path, "wb") as handle:
                handle.write(content)
        finally:
            sftp.close()
        code, _out, err = exec_command(
            client,
            f"pct push {int(vmid)} {tmp_path} {shlex.quote(path)} --perms {perms}",
            timeout=60,
        )
        if code != 0:
            raise AppError(
                f"pct push 失敗（VMID {vmid}）：{(err or _out or '').strip()[:300]}",
                502,
            )
        logger.info("Pushed %d bytes to %s on CT %s", len(content), path, vmid)
    finally:
        try:
            exec_command(client, f"rm -f {tmp_path}", timeout=10)
        except Exception:
            logger.debug("Temp cleanup failed for %s", tmp_path)
        client.close()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd backend && uv run pytest tests/infrastructure/test_guest_file_write.py -v && uv run ruff check app/infrastructure/proxmox/guest.py`
Expected: 全部 PASS、ruff 乾淨

- [ ] **Step 5: Commit**

```bash
git add backend/app/infrastructure/proxmox/guest.py backend/tests/infrastructure/test_guest_file_write.py
git commit -m "模組E教學體驗: guest file-write 基礎設施 (E2)"
```

---

### Task 13: E2 config_push_service + /teaching/config-push API

**Files:**
- Create: `backend/app/services/teaching/config_push_service.py`
- Create: `backend/app/api/routes/teaching.py`
- Create: `backend/app/schemas/teaching.py`
- Modify: `backend/app/schemas/__init__.py`、`backend/app/api/main.py`
- Test: `backend/tests/services/test_config_push_service.py`

**Interfaces:**
- Consumes: Task 6 `require_vm_teaching_access`、Task 12 `guest`、`ExpiringStore`、`background_tasks.submit_factory`、`GovernanceConfig.provision_max_concurrency`
- Produces:
  - `config_push_service.start_push(session, *, content: bytes, file_name: str, target_path: str, vmids: list[int], user) -> str`
  - `config_push_service.get_push_status(task_id: str, user) -> PushTask`（非發起者且非 admin → PermissionDeniedError；不存在 → NotFoundError）
  - `POST /teaching/config-push`（multipart，InstructorUser）→ 202 `{task_id}`；`GET /teaching/config-push/{task_id}`
  - schemas：`ConfigPushAccepted(task_id)`、`ConfigPushItemPublic(vmid, status, error)`、`ConfigPushStatusPublic(task_id, file_name, target_path, items)`；status 值：`pending|running|ok|error`

- [ ] **Step 1: 寫失敗測試**

`backend/tests/services/test_config_push_service.py`：

```python
"""配置分發編排測試（mock guest 寫入與權限）。"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError, PermissionDeniedError
from app.services.teaching import config_push_service as svc

TEACHER = SimpleNamespace(id=uuid.uuid4(), email="t@campus.edu")


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"qemu": [], "lxc": []}
    monkeypatch.setattr(
        svc,
        "_resolve_targets",
        lambda session, vmids, user: [
            {"vmid": 101, "node": "pve1", "type": "qemu"},
            {"vmid": 102, "node": "pve1", "type": "lxc"},
        ],
    )
    monkeypatch.setattr(svc, "_max_concurrency", lambda: 4)
    monkeypatch.setattr(
        svc.guest,
        "write_file_qemu",
        lambda node, vmid, path, content: calls["qemu"].append(vmid),
    )
    monkeypatch.setattr(
        svc.guest,
        "write_file_lxc",
        lambda node, vmid, path, content: calls["lxc"].append(vmid),
    )
    return calls


def test_start_push_rejects_oversize(harness: dict) -> None:
    big = b"x" * (svc.guest.MAX_CONFIG_FILE_BYTES + 1)
    with pytest.raises(BadRequestError):
        svc.start_push(
            None, content=big, file_name="a.conf",
            target_path="/etc/a.conf", vmids=[101], user=TEACHER,
        )


def test_run_push_fans_out_and_records_results(harness: dict) -> None:
    task = svc._new_task(
        requested_by=TEACHER.id, file_name="a.conf", target_path="/etc/a.conf",
        targets=[
            {"vmid": 101, "node": "pve1", "type": "qemu"},
            {"vmid": 102, "node": "pve1", "type": "lxc"},
        ],
    )
    asyncio.run(
        svc._run_push(task.id, b"data", "/etc/a.conf", concurrency=2)
    )
    stored = svc._tasks.get(task.id)
    assert stored is not None
    assert {i.status for i in stored.items.values()} == {"ok"}
    assert harness["qemu"] == [101]
    assert harness["lxc"] == [102]


def test_run_push_single_failure_does_not_stop_batch(
    harness: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(node, vmid, path, content):
        raise RuntimeError("agent down")

    monkeypatch.setattr(svc.guest, "write_file_qemu", _fail)
    task = svc._new_task(
        requested_by=TEACHER.id, file_name="a.conf", target_path="/etc/a.conf",
        targets=[
            {"vmid": 101, "node": "pve1", "type": "qemu"},
            {"vmid": 102, "node": "pve1", "type": "lxc"},
        ],
    )
    asyncio.run(svc._run_push(task.id, b"data", "/etc/a.conf", concurrency=2))
    stored = svc._tasks.get(task.id)
    assert stored.items[101].status == "error"
    assert "agent down" in (stored.items[101].error or "")
    assert stored.items[102].status == "ok"


def test_get_push_status_requires_owner_or_admin(harness: dict) -> None:
    task = svc._new_task(
        requested_by=TEACHER.id, file_name="a.conf", target_path="/etc/a.conf",
        targets=[{"vmid": 101, "node": "pve1", "type": "qemu"}],
    )
    stranger = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="student")
    with pytest.raises(PermissionDeniedError):
        svc.get_push_status(task.id, stranger)
```

注意：最後一個測試依賴 `get_push_status` 用 `app.core.permissions.is_admin(user)` 判斷 —— stranger 物件需帶讓 `is_admin` 回 False 的欄位；實作前先看 `app/core/permissions.py` 的 `is_admin` 依據（`is_superuser` 或 `role`），把 SimpleNamespace 欄位補齊。

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/services/test_config_push_service.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: 實作 service**

`backend/app/services/teaching/config_push_service.py`：

```python
"""配置文件分發（E2）：逐 VM fan-out 寫入，任務狀態存 in-memory。

- 權限：逐 vmid 過 ``require_vm_teaching_access``（老師僅能選自己群組成員的 VM）。
- 併發：``asyncio.Semaphore``，上限沿用 ``GovernanceConfig.provision_max_concurrency``。
- 單台失敗不中斷整批；逐台記錄 ok / error + 原因。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session

from app.core.permissions import is_admin
from app.exceptions import BadRequestError, NotFoundError, PermissionDeniedError
from app.infrastructure.proxmox import guest
from app.infrastructure.worker import ExpiringStore, background_tasks
from app.services.proxmox import proxmox_service
from app.services.teaching.access import require_vm_teaching_access

logger = logging.getLogger(__name__)

TASK_TTL = timedelta(hours=2)


@dataclass
class PushItemResult:
    vmid: int
    status: str = "pending"  # pending | running | ok | error
    error: str | None = None


@dataclass
class PushTask:
    id: str
    requested_by: uuid.UUID
    file_name: str
    target_path: str
    created_at: datetime
    items: dict[int, PushItemResult] = field(default_factory=dict)
    targets: list[dict[str, Any]] = field(default_factory=list)


_tasks: ExpiringStore[PushTask] = ExpiringStore(
    ttl=TASK_TTL,
    is_expired=lambda task, now, ttl: now - task.created_at > ttl,
    now_factory=lambda: datetime.now(timezone.utc),
)


def _max_concurrency() -> int:
    from app.core.db import engine  # noqa: PLC0415 — 測試環境不一定有 DB
    from app.repositories import governance as governance_repo  # noqa: PLC0415

    with Session(engine) as session:
        return int(
            governance_repo.get_governance_config(
                session=session
            ).provision_max_concurrency
        )


def _resolve_targets(
    session: Session, vmids: list[int], user: Any
) -> list[dict[str, Any]]:
    """權限檢查 + PVE node/type 解析；任一台不合法整批 4xx。"""
    targets: list[dict[str, Any]] = []
    for vmid in dict.fromkeys(vmids):  # 去重保序
        require_vm_teaching_access(session, user, vmid)
        info = proxmox_service.find_resource(vmid)
        targets.append(
            {
                "vmid": int(vmid),
                "node": str(info["node"]),
                "type": "lxc" if str(info.get("type") or "") == "lxc" else "qemu",
            }
        )
    return targets


def _new_task(
    *,
    requested_by: uuid.UUID,
    file_name: str,
    target_path: str,
    targets: list[dict[str, Any]],
) -> PushTask:
    task = PushTask(
        id=uuid.uuid4().hex,
        requested_by=requested_by,
        file_name=file_name,
        target_path=target_path,
        created_at=datetime.now(timezone.utc),
        targets=targets,
        items={t["vmid"]: PushItemResult(vmid=t["vmid"]) for t in targets},
    )
    _tasks.upsert(task.id, task)
    return task


def _write_one(target: dict[str, Any], target_path: str, content: bytes) -> None:
    if target["type"] == "lxc":
        guest.write_file_lxc(target["node"], target["vmid"], target_path, content)
    else:
        guest.write_file_qemu(target["node"], target["vmid"], target_path, content)


async def _run_push(
    task_id: str, content: bytes, target_path: str, *, concurrency: int
) -> None:
    task = _tasks.get(task_id)
    if task is None:
        return
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(target: dict[str, Any]) -> None:
        item = task.items[target["vmid"]]
        async with semaphore:
            item.status = "running"
            try:
                await asyncio.to_thread(_write_one, target, target_path, content)
                item.status = "ok"
            except Exception as exc:
                item.status = "error"
                item.error = str(exc)[:300]
                logger.warning(
                    "Config push failed for vmid=%s: %s", target["vmid"], exc
                )

    await asyncio.gather(*(_one(t) for t in task.targets))
    logger.info(
        "Config push %s done: %d ok / %d total",
        task_id,
        sum(1 for i in task.items.values() if i.status == "ok"),
        len(task.items),
    )


def start_push(
    session: Session,
    *,
    content: bytes,
    file_name: str,
    target_path: str,
    vmids: list[int],
    user: Any,
) -> str:
    if not vmids:
        raise BadRequestError("至少需要選擇一台 VM")
    if len(content) > guest.MAX_CONFIG_FILE_BYTES:
        raise BadRequestError("檔案超過 1 MB 上限", )
    guest.validate_target_path(target_path)
    targets = _resolve_targets(session, vmids, user)
    task = _new_task(
        requested_by=user.id,
        file_name=file_name,
        target_path=target_path,
        targets=targets,
    )
    concurrency = _max_concurrency()
    background_tasks.submit_factory(
        lambda: _run_push(task.id, content, target_path, concurrency=concurrency),
        name="config-push",
        task_id=f"config-push-{task.id}",
    )
    return task.id


def get_push_status(task_id: str, user: Any) -> PushTask:
    task = _tasks.get(task_id)
    if task is None:
        raise NotFoundError("分發任務不存在或已過期")
    if task.requested_by != user.id and not is_admin(user):
        raise PermissionDeniedError("只能查看自己發起的分發任務")
    return task
```

注意：檔案超過 1 MB 設計上要回 413 —— `BadRequestError` 是 400；改為 `raise AppError("檔案超過 1 MB 上限", 413)`（import `AppError`）。實作以 413 為準，測試斷言 `AppError`。

- [ ] **Step 4: schemas**

`backend/app/schemas/teaching.py`：

```python
"""教學體驗 API schemas（E2/E3/E6）。"""

import uuid
from pydantic import BaseModel, Field


class ConfigPushAccepted(BaseModel):
    task_id: str


class ConfigPushItemPublic(BaseModel):
    vmid: int
    status: str  # pending | running | ok | error
    error: str | None = None


class ConfigPushStatusPublic(BaseModel):
    task_id: str
    file_name: str
    target_path: str
    items: list[ConfigPushItemPublic]


class HeatmapEntry(BaseModel):
    vmid: int
    name: str | None = None
    owner_id: uuid.UUID
    owner_name: str | None = None
    status: str
    cpu_percent: float
    mem_percent: float
    uptime_seconds: int
    activity: str  # running | idle | stale | stopped


class BatchSpecRequest(BaseModel):
    vmids: list[int] | None = None
    group_id: uuid.UUID | None = None
    cores: int | None = Field(default=None, ge=1, le=256)
    memory_mb: int | None = Field(default=None, ge=128, le=1048576)


class BatchSpecAccepted(BaseModel):
    task_id: str


class BatchSpecItemPublic(BaseModel):
    vmid: int
    status: str  # pending | running | ok | needs_restart | quota_exceeded | error
    error: str | None = None


class BatchSpecStatusPublic(BaseModel):
    task_id: str
    items: list[BatchSpecItemPublic]
```

`schemas/__init__.py` 匯出以上全部名稱。

- [ ] **Step 5: route**

`backend/app/api/routes/teaching.py`：

```python
"""教學體驗 API（E2 配置分發 / E3 熱圖 / E6 批次規格），InstructorUser 起跳。"""

import logging
import uuid

from fastapi import APIRouter, File, Form, UploadFile

from app.api.deps import InstructorUser, SessionDep
from app.schemas import (
    ConfigPushAccepted,
    ConfigPushItemPublic,
    ConfigPushStatusPublic,
)
from app.services.teaching import config_push_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teaching", tags=["teaching"])


@router.post(
    "/config-push", response_model=ConfigPushAccepted, status_code=202
)
async def start_config_push(
    session: SessionDep,
    current_user: InstructorUser,
    file: UploadFile = File(...),
    target_path: str = Form(...),
    vmids: list[int] = Form(...),
) -> ConfigPushAccepted:
    content = await file.read()
    task_id = config_push_service.start_push(
        session,
        content=content,
        file_name=file.filename or "config",
        target_path=target_path,
        vmids=vmids,
        user=current_user,
    )
    return ConfigPushAccepted(task_id=task_id)


@router.get("/config-push/{task_id}", response_model=ConfigPushStatusPublic)
def get_config_push_status(
    task_id: str, current_user: InstructorUser
) -> ConfigPushStatusPublic:
    task = config_push_service.get_push_status(task_id, current_user)
    return ConfigPushStatusPublic(
        task_id=task.id,
        file_name=task.file_name,
        target_path=task.target_path,
        items=[
            ConfigPushItemPublic(vmid=i.vmid, status=i.status, error=i.error)
            for i in sorted(task.items.values(), key=lambda x: x.vmid)
        ],
    )
```

`api/main.py`：import `teaching` 並在 `quotas` 之後 `api_router.include_router(teaching.router)`。

- [ ] **Step 6: 跑測試確認通過**

Run: `cd backend && uv run pytest tests/services/test_config_push_service.py -v && uv run python -c "from app.api.main import api_router; print('ok')" && uv run ruff check app/services/teaching/ app/api/routes/teaching.py app/schemas/teaching.py`
Expected: 全部 PASS、`ok`、ruff 乾淨

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/teaching/ backend/app/api/ backend/app/schemas/ backend/tests/services/test_config_push_service.py
git commit -m "模組E教學體驗: 配置文件分發服務與 API (E2)"
```

---

### Task 14: E3 progress_service + /teaching/heatmap

**Files:**
- Create: `backend/app/services/teaching/progress_service.py`
- Modify: `backend/app/api/routes/teaching.py`（加 heatmap 端點）
- Test: `backend/tests/services/test_progress_service.py`

**Interfaces:**
- Consumes: `require_group_access`、`group_repo.get_group_members`、`proxmox_service.list_all_resources`、Task 13 的 `HeatmapEntry` schema
- Produces:
  - `progress_service.classify_activity(*, status: str, cpu_percent: float, uptime_seconds: int) -> str`（純函式；`stopped`＝非 running；`stale`＝uptime>3600 且 cpu<1.0；`idle`＝cpu<10.0；否則 `running`）
  - `progress_service.get_heatmap(session, *, group_id, user, cluster_resources) -> list[HeatmapEntry]`
  - `GET /teaching/heatmap?group_id=`（InstructorUser；老師僅能查自己擁有的群組，admin 全部）

- [ ] **Step 1: 寫失敗測試**

`backend/tests/services/test_progress_service.py`：

```python
"""進度熱圖測試：activity 判定純函式 + 聚合。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.teaching import progress_service

STUDENT_ID = uuid.uuid4()


class TestClassifyActivity:
    def test_stopped(self) -> None:
        assert (
            progress_service.classify_activity(
                status="stopped", cpu_percent=0.0, uptime_seconds=0
            )
            == "stopped"
        )

    def test_stale_long_uptime_zero_cpu(self) -> None:
        assert (
            progress_service.classify_activity(
                status="running", cpu_percent=0.4, uptime_seconds=7200
            )
            == "stale"
        )

    def test_idle_low_cpu_short_uptime(self) -> None:
        assert (
            progress_service.classify_activity(
                status="running", cpu_percent=3.0, uptime_seconds=600
            )
            == "idle"
        )

    def test_running(self) -> None:
        assert (
            progress_service.classify_activity(
                status="running", cpu_percent=45.0, uptime_seconds=600
            )
            == "running"
        )


def test_get_heatmap_aggregates(monkeypatch: pytest.MonkeyPatch) -> None:
    group = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
    monkeypatch.setattr(
        progress_service, "_get_group_or_404", lambda session, group_id: group
    )
    monkeypatch.setattr(
        progress_service, "require_group_access", lambda user, owner_id: None
    )
    monkeypatch.setattr(
        progress_service.group_repo,
        "get_group_members",
        lambda **kwargs: [
            SimpleNamespace(id=STUDENT_ID, email="s@campus.edu", full_name="小明")
        ],
    )
    monkeypatch.setattr(
        progress_service,
        "_resources_for_users",
        lambda session, user_ids: [
            SimpleNamespace(vmid=101, user_id=STUDENT_ID)
        ],
    )
    cluster = [
        {
            "vmid": 101, "name": "stu-vm", "status": "running",
            "cpu": 0.42, "maxcpu": 2,
            "mem": 1024**3, "maxmem": 2 * 1024**3,
            "uptime": 7200,
        }
    ]
    entries = progress_service.get_heatmap(
        None, group_id=group.id, user=None, cluster_resources=cluster
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry.vmid == 101
    assert entry.owner_name == "小明"
    assert entry.cpu_percent == pytest.approx(42.0)
    assert entry.mem_percent == pytest.approx(50.0)
    assert entry.activity == "running"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/services/test_progress_service.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: 實作**

`backend/app/services/teaching/progress_service.py`：

```python
"""學生進度熱圖（E3）：聚合 cluster/resources，判定每台 VM 的活動狀態。

「stale（長期無動靜）」以當下狀態近似：uptime > 1h 且 CPU < 1%。
不查 RRD 歷史（控制成本，30 秒輪詢下已足夠）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlmodel import Session, col, select

from app.core.authorizers import require_group_access
from app.exceptions import NotFoundError
from app.models import Group, Resource
from app.repositories import group as group_repo
from app.schemas.teaching import HeatmapEntry

logger = logging.getLogger(__name__)

STALE_MIN_UPTIME_SECONDS = 3600
STALE_CPU_PERCENT = 1.0
IDLE_CPU_PERCENT = 10.0


def classify_activity(
    *, status: str, cpu_percent: float, uptime_seconds: int
) -> str:
    if status != "running":
        return "stopped"
    if uptime_seconds > STALE_MIN_UPTIME_SECONDS and cpu_percent < STALE_CPU_PERCENT:
        return "stale"
    if cpu_percent < IDLE_CPU_PERCENT:
        return "idle"
    return "running"


def _get_group_or_404(session: Session, group_id: uuid.UUID) -> Group:
    group = session.get(Group, group_id)
    if group is None:
        raise NotFoundError("Group not found")
    return group


def _resources_for_users(
    session: Session, user_ids: list[uuid.UUID]
) -> list[Resource]:
    if not user_ids:
        return []
    return list(
        session.exec(
            select(Resource).where(col(Resource.user_id).in_(user_ids))
        ).all()
    )


def get_heatmap(
    session: Session,
    *,
    group_id: uuid.UUID,
    user: Any,
    cluster_resources: list[dict[str, Any]],
) -> list[HeatmapEntry]:
    group = _get_group_or_404(session, group_id)
    require_group_access(user, group.owner_id)

    members = group_repo.get_group_members(session=session, group_id=group_id)
    member_by_id = {m.id: m for m in members}
    resources = _resources_for_users(session, list(member_by_id))
    listing: dict[int, dict[str, Any]] = {
        int(item["vmid"]): item
        for item in cluster_resources
        if item.get("vmid") is not None
    }

    entries: list[HeatmapEntry] = []
    for resource in resources:
        info = listing.get(resource.vmid, {})
        status = str(info.get("status") or "unknown")
        maxcpu = float(info.get("maxcpu") or 0) or 1.0
        maxmem = float(info.get("maxmem") or 0) or 1.0
        cpu_percent = round(float(info.get("cpu") or 0.0) * 100.0, 1)
        mem_percent = round(float(info.get("mem") or 0.0) / maxmem * 100.0, 1)
        uptime = int(info.get("uptime") or 0)
        member = member_by_id.get(resource.user_id)
        entries.append(
            HeatmapEntry(
                vmid=resource.vmid,
                name=info.get("name"),
                owner_id=resource.user_id,
                owner_name=(member.full_name or member.email) if member else None,
                status=status,
                cpu_percent=cpu_percent,
                mem_percent=mem_percent,
                uptime_seconds=uptime,
                activity=classify_activity(
                    status=status,
                    cpu_percent=cpu_percent,
                    uptime_seconds=uptime,
                ),
            )
        )
    return sorted(entries, key=lambda e: e.vmid)
```

注意：PVE `cluster/resources` 的 `cpu` 欄位是 0–1 的比例（相對於 maxcpu 顆數的使用率），`mem` 是 bytes——上面 cpu_percent 直接 ×100（整機百分比），mem 用 `mem/maxmem`。測試以此為準。

- [ ] **Step 4: route**

`backend/app/api/routes/teaching.py` 加：

```python
import asyncio
from typing import Any

from app.infrastructure.proxmox import operations as proxmox_ops
from app.schemas import HeatmapEntry
from app.services.teaching import progress_service


async def _safe_cluster_listing() -> list[dict[str, Any]]:
    try:
        return await asyncio.to_thread(proxmox_ops.list_all_resources)
    except Exception:
        logger.warning("Teaching: failed to list cluster resources", exc_info=True)
        return []


@router.get("/heatmap", response_model=list[HeatmapEntry])
async def get_heatmap(
    group_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> list[HeatmapEntry]:
    cluster_resources = await _safe_cluster_listing()
    return progress_service.get_heatmap(
        session,
        group_id=group_id,
        user=current_user,
        cluster_resources=cluster_resources,
    )
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd backend && uv run pytest tests/services/test_progress_service.py -v && uv run python -c "from app.api.main import api_router; print('ok')"`
Expected: 全部 PASS、`ok`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/teaching/progress_service.py backend/app/api/routes/teaching.py backend/tests/services/test_progress_service.py
git commit -m "模組E教學體驗: 學生進度熱圖服務與 API (E3)"
```

---

### Task 15: E6 batch_spec_service + /teaching/batch-spec

**Files:**
- Create: `backend/app/services/teaching/batch_spec_service.py`
- Modify: `backend/app/api/routes/teaching.py`
- Test: `backend/tests/services/test_batch_spec_service.py`

**Interfaces:**
- Consumes: Task 3 `quota_service.check_quota`、Task 6 `require_vm_teaching_access`、`proxmox_service.get_current_specs/update_config/get_status`、Task 13 的 ExpiringStore 模式與 `BatchSpec*` schemas
- Produces:
  - `batch_spec_service.start_batch_spec(session, *, vmids, group_id, cores, memory_mb, user) -> str`（vmids 與 group_id 二擇一，皆空 → BadRequestError；group_id 時解析為該群組全部成員 VM）
  - `batch_spec_service.get_batch_status(task_id, user) -> SpecTask`
  - `POST /teaching/batch-spec` → 202；`GET /teaching/batch-spec/{task_id}`
  - item status：`pending|running|ok|needs_restart|quota_exceeded|error`

- [ ] **Step 1: 寫失敗測試**

`backend/tests/services/test_batch_spec_service.py`：

```python
"""批次規格調整測試（mock PVE / quota）。"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError, ConflictError
from app.services.teaching import batch_spec_service as svc

TEACHER = SimpleNamespace(id=uuid.uuid4(), email="t@campus.edu")


def _targets() -> list[dict]:
    return [
        {"vmid": 101, "node": "pve1", "type": "qemu", "owner_id": uuid.uuid4()},
        {"vmid": 102, "node": "pve1", "type": "lxc", "owner_id": uuid.uuid4()},
    ]


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"config": [], "status": "running"}
    monkeypatch.setattr(
        svc.proxmox_service,
        "get_current_specs",
        lambda node, vmid, rtype: {"cpu": 2, "memory": 2048, "disk": 20},
    )
    monkeypatch.setattr(
        svc.proxmox_service,
        "update_config",
        lambda node, vmid, rtype, **params: calls["config"].append((vmid, params)),
    )
    monkeypatch.setattr(
        svc.proxmox_service,
        "get_status",
        lambda node, vmid, rtype: {"status": calls["status"]},
    )
    monkeypatch.setattr(svc, "_check_quota_for_owner", lambda owner_id, **d: None)
    return calls


def test_start_requires_targets() -> None:
    with pytest.raises(BadRequestError):
        svc.start_batch_spec(
            None, vmids=None, group_id=None, cores=4, memory_mb=None, user=TEACHER
        )


def test_start_requires_some_change() -> None:
    with pytest.raises(BadRequestError):
        svc.start_batch_spec(
            None, vmids=[101], group_id=None, cores=None, memory_mb=None,
            user=TEACHER,
        )


def test_qemu_running_marks_needs_restart(harness: dict) -> None:
    task = svc._new_task(requested_by=TEACHER.id, targets=_targets())
    asyncio.run(svc._run_batch(task.id, cores=4, memory_mb=4096, concurrency=2))
    stored = svc._tasks.get(task.id)
    assert stored.items[101].status == "needs_restart"  # qemu running
    assert stored.items[102].status == "ok"             # lxc 即時生效
    assert (101, {"cores": 4, "memory": 4096}) in harness["config"]


def test_quota_exceeded_marked_and_skips_apply(
    harness: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _deny(owner_id, **deltas):
        raise ConflictError("配額不足")

    monkeypatch.setattr(svc, "_check_quota_for_owner", _deny)
    task = svc._new_task(requested_by=TEACHER.id, targets=_targets())
    asyncio.run(svc._run_batch(task.id, cores=8, memory_mb=None, concurrency=2))
    stored = svc._tasks.get(task.id)
    assert {i.status for i in stored.items.values()} == {"quota_exceeded"}
    assert harness["config"] == []


def test_single_error_does_not_stop_batch(
    harness: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _update(node, vmid, rtype, **params):
        if vmid == 101:
            raise RuntimeError("pve error")
        harness["config"].append((vmid, params))

    monkeypatch.setattr(svc.proxmox_service, "update_config", _update)
    task = svc._new_task(requested_by=TEACHER.id, targets=_targets())
    asyncio.run(svc._run_batch(task.id, cores=4, memory_mb=None, concurrency=2))
    stored = svc._tasks.get(task.id)
    assert stored.items[101].status == "error"
    assert stored.items[102].status == "ok"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/services/test_batch_spec_service.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: 實作**

`backend/app/services/teaching/batch_spec_service.py`：

```python
"""批次動態資源調整（E6）：逐台過配額 → set config → 逐台結果。

LXC 的 cores/memory 更新即時生效；QEMU 更新 config 後若 VM 在跑，
標記 ``needs_restart``（重啟後生效）。單台失敗不中斷整批。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session

from app.core.permissions import is_admin
from app.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.infrastructure.worker import ExpiringStore, background_tasks
from app.services.proxmox import proxmox_service
from app.services.teaching.access import require_vm_teaching_access

logger = logging.getLogger(__name__)

TASK_TTL = timedelta(hours=2)


@dataclass
class SpecItemResult:
    vmid: int
    status: str = "pending"  # pending|running|ok|needs_restart|quota_exceeded|error
    error: str | None = None


@dataclass
class SpecTask:
    id: str
    requested_by: uuid.UUID
    created_at: datetime
    items: dict[int, SpecItemResult] = field(default_factory=dict)
    targets: list[dict[str, Any]] = field(default_factory=list)


_tasks: ExpiringStore[SpecTask] = ExpiringStore(
    ttl=TASK_TTL,
    is_expired=lambda task, now, ttl: now - task.created_at > ttl,
    now_factory=lambda: datetime.now(timezone.utc),
)


def _max_concurrency() -> int:
    from app.core.db import engine  # noqa: PLC0415
    from app.repositories import governance as governance_repo  # noqa: PLC0415

    with Session(engine) as session:
        return int(
            governance_repo.get_governance_config(
                session=session
            ).provision_max_concurrency
        )


def _check_quota_for_owner(owner_id: uuid.UUID, **deltas: int) -> None:
    from app.core.db import engine  # noqa: PLC0415
    from app.services.resource import quota_service  # noqa: PLC0415

    with Session(engine) as session:
        quota_service.check_quota(session, owner_id, **deltas)


def _resolve_targets(
    session: Session,
    *,
    vmids: list[int] | None,
    group_id: uuid.UUID | None,
    user: Any,
) -> list[dict[str, Any]]:
    resolved_vmids: list[int]
    if vmids:
        resolved_vmids = list(dict.fromkeys(vmids))
    elif group_id is not None:
        from app.repositories import group as group_repo  # noqa: PLC0415

        resolved_vmids = group_repo.get_member_vmids(
            session=session, group_id=group_id
        )
        if not resolved_vmids:
            raise BadRequestError("該群組成員沒有任何 VM")
    else:
        raise BadRequestError("必須提供 vmids 或 group_id")

    targets: list[dict[str, Any]] = []
    for vmid in resolved_vmids:
        resource = require_vm_teaching_access(session, user, vmid)
        info = proxmox_service.find_resource(vmid)
        targets.append(
            {
                "vmid": int(vmid),
                "node": str(info["node"]),
                "type": "lxc" if str(info.get("type") or "") == "lxc" else "qemu",
                "owner_id": resource.user_id,
            }
        )
    return targets


def _new_task(*, requested_by: uuid.UUID, targets: list[dict[str, Any]]) -> SpecTask:
    task = SpecTask(
        id=uuid.uuid4().hex,
        requested_by=requested_by,
        created_at=datetime.now(timezone.utc),
        targets=targets,
        items={t["vmid"]: SpecItemResult(vmid=t["vmid"]) for t in targets},
    )
    _tasks.upsert(task.id, task)
    return task


def _apply_one(
    target: dict[str, Any], *, cores: int | None, memory_mb: int | None
) -> tuple[str, str | None]:
    node, vmid, rtype = target["node"], target["vmid"], target["type"]
    current = proxmox_service.get_current_specs(node, vmid, rtype)
    delta_cores = (
        max(0, cores - int(current.get("cpu") or 0)) if cores is not None else 0
    )
    delta_memory = (
        max(0, memory_mb - int(current.get("memory") or 0))
        if memory_mb is not None
        else 0
    )
    try:
        _check_quota_for_owner(
            target["owner_id"],
            delta_cores=delta_cores,
            delta_memory_mb=delta_memory,
        )
    except ConflictError as exc:
        return "quota_exceeded", exc.message

    params: dict[str, int] = {}
    if cores is not None:
        params["cores"] = cores
    if memory_mb is not None:
        params["memory"] = memory_mb
    proxmox_service.update_config(node, vmid, rtype, **params)

    if rtype == "qemu":
        status = proxmox_service.get_status(node, vmid, rtype)
        if str(status.get("status") or "").lower() == "running":
            return "needs_restart", None
    return "ok", None


async def _run_batch(
    task_id: str, *, cores: int | None, memory_mb: int | None, concurrency: int
) -> None:
    task = _tasks.get(task_id)
    if task is None:
        return
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(target: dict[str, Any]) -> None:
        item = task.items[target["vmid"]]
        async with semaphore:
            item.status = "running"
            try:
                status, error = await asyncio.to_thread(
                    _apply_one, target, cores=cores, memory_mb=memory_mb
                )
                item.status = status
                item.error = error
            except Exception as exc:
                item.status = "error"
                item.error = str(exc)[:300]
                logger.warning(
                    "Batch spec failed for vmid=%s: %s", target["vmid"], exc
                )

    await asyncio.gather(*(_one(t) for t in task.targets))


def start_batch_spec(
    session: Session,
    *,
    vmids: list[int] | None,
    group_id: uuid.UUID | None,
    cores: int | None,
    memory_mb: int | None,
    user: Any,
) -> str:
    if cores is None and memory_mb is None:
        raise BadRequestError("至少需要指定 cores 或 memory_mb 其中之一")
    targets = _resolve_targets(session, vmids=vmids, group_id=group_id, user=user)
    task = _new_task(requested_by=user.id, targets=targets)
    concurrency = _max_concurrency()
    background_tasks.submit_factory(
        lambda: _run_batch(
            task.id, cores=cores, memory_mb=memory_mb, concurrency=concurrency
        ),
        name="batch-spec",
        task_id=f"batch-spec-{task.id}",
    )
    return task.id


def get_batch_status(task_id: str, user: Any) -> SpecTask:
    task = _tasks.get(task_id)
    if task is None:
        raise NotFoundError("批次任務不存在或已過期")
    if task.requested_by != user.id and not is_admin(user):
        raise PermissionDeniedError("只能查看自己發起的批次任務")
    return task
```

注意：`_run_batch` 是 keyword-only 參數，測試呼叫寫法 `svc._run_batch(task.id, cores=4, memory_mb=4096, concurrency=2)` —— 簽名第一參數 `task_id` 為 positional。`start_batch_spec` 的 targets 解析要在 API 執行緒內（帶 session），fan-out 內不再碰 request session。`get_member_vmids` 簽名以 `backend/app/repositories/group.py:193` 為準（實作前確認回傳型別）。

- [ ] **Step 4: route**

`backend/app/api/routes/teaching.py` 加：

```python
from app.schemas import (
    BatchSpecAccepted,
    BatchSpecItemPublic,
    BatchSpecRequest,
    BatchSpecStatusPublic,
)
from app.services.teaching import batch_spec_service


@router.post("/batch-spec", response_model=BatchSpecAccepted, status_code=202)
def start_batch_spec(
    body: BatchSpecRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> BatchSpecAccepted:
    task_id = batch_spec_service.start_batch_spec(
        session,
        vmids=body.vmids,
        group_id=body.group_id,
        cores=body.cores,
        memory_mb=body.memory_mb,
        user=current_user,
    )
    return BatchSpecAccepted(task_id=task_id)


@router.get("/batch-spec/{task_id}", response_model=BatchSpecStatusPublic)
def get_batch_spec_status(
    task_id: str, current_user: InstructorUser
) -> BatchSpecStatusPublic:
    task = batch_spec_service.get_batch_status(task_id, current_user)
    return BatchSpecStatusPublic(
        task_id=task.id,
        items=[
            BatchSpecItemPublic(vmid=i.vmid, status=i.status, error=i.error)
            for i in sorted(task.items.values(), key=lambda x: x.vmid)
        ],
    )
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd backend && uv run pytest tests/services/test_batch_spec_service.py -v && uv run python -c "from app.api.main import api_router; print('ok')"`
Expected: 全部 PASS、`ok`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/teaching/batch_spec_service.py backend/app/api/routes/teaching.py backend/tests/services/test_batch_spec_service.py
git commit -m "模組E教學體驗: 批次規格調整服務與 API (E6)"
```

---

### Task 16: E5 SessionMode.pair + 雙人輸入 + pair_service

**Files:**
- Modify: `backend/app/services/classroom/vnc_session_manager.py`（`SessionMode.pair` + `_subscriber_reader` 放行）
- Create: `backend/app/services/classroom/pair_service.py`
- Test: `backend/tests/services/test_pair_service.py`

**Interfaces:**
- Consumes: `vnc_session_manager.start_session/stop_session/get_session/on_session_end`、`GroupMember`/`Group` 查詢
- Produces:
  - `SessionMode.pair = "pair"`
  - pair mode 下 `_subscriber_reader` 轉發**所有**訂閱者輸入（不走 controller 單一控制權）
  - `pair_service.create_pair(session, user, *, vmid, invitee_user_id) -> PairSession`（owner 專屬；受邀者需同群組；一 VM 一 pair session）
  - `pair_service.list_mine(user) -> list[PairSession]`（我發起 + 邀請我的）
  - `pair_service.end_pair(user, session_id) -> None`（owner 或 admin）
  - `pair_service.is_participant(session_id: str, user_id) -> bool`（watch WS 用）
  - `PairSession(id, vmid, owner_id, invitee_id, created_at)`（frozen dataclass；`id` = 底層 classroom session id）

- [ ] **Step 1: 寫失敗測試**

`backend/tests/services/test_pair_service.py`：

```python
"""Pair Mode 協作測試（mock VncSessionManager 與群組查詢）。"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import ConflictError, PermissionDeniedError
from app.services.classroom import pair_service
from app.services.classroom.vnc_session_manager import SessionMode

OWNER = SimpleNamespace(id=uuid.uuid4(), email="o@campus.edu")
INVITEE_ID = uuid.uuid4()


class _FakeManager:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.stopped: list[str] = []

    async def start_session(self, *, vmid, mode, group_id, started_by):
        self.started.append({"vmid": vmid, "mode": mode})
        return SimpleNamespace(
            id=f"sess-{vmid}", vmid=vmid, mode=mode,
            group_id=group_id, started_by=started_by,
            controller_user_id=None, subscriber_count=0,
        )

    async def stop_session(self, session_id, *, reason="ended"):
        self.stopped.append(session_id)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch: pytest.MonkeyPatch):
    pair_service._sessions.clear()
    manager = _FakeManager()
    monkeypatch.setattr(pair_service, "vnc_session_manager", manager)
    monkeypatch.setattr(pair_service, "_share_group", lambda s, a, b: True)
    monkeypatch.setattr(
        pair_service,
        "_get_owned_resource",
        lambda session, user, vmid: SimpleNamespace(vmid=vmid, user_id=user.id),
    )
    monkeypatch.setattr(
        pair_service,
        "_get_active_user",
        lambda session, user_id: SimpleNamespace(id=user_id, is_active=True),
    )
    monkeypatch.setattr(pair_service, "is_admin", lambda user: False)
    yield manager
    pair_service._sessions.clear()


def test_create_pair_starts_session(clean_state: _FakeManager) -> None:
    ps = asyncio.run(
        pair_service.create_pair(
            None, OWNER, vmid=101, invitee_user_id=INVITEE_ID
        )
    )
    assert ps.vmid == 101
    assert ps.owner_id == OWNER.id
    assert ps.invitee_id == INVITEE_ID
    assert clean_state.started[0]["mode"] is SessionMode.pair
    assert pair_service.is_participant(ps.id, INVITEE_ID)
    assert not pair_service.is_participant(ps.id, uuid.uuid4())


def test_create_pair_one_per_vm(clean_state: _FakeManager) -> None:
    asyncio.run(
        pair_service.create_pair(None, OWNER, vmid=101, invitee_user_id=INVITEE_ID)
    )
    with pytest.raises(ConflictError):
        asyncio.run(
            pair_service.create_pair(
                None, OWNER, vmid=101, invitee_user_id=INVITEE_ID
            )
        )


def test_create_pair_requires_same_group(
    clean_state: _FakeManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pair_service, "_share_group", lambda s, a, b: False)
    with pytest.raises(PermissionDeniedError):
        asyncio.run(
            pair_service.create_pair(
                None, OWNER, vmid=101, invitee_user_id=INVITEE_ID
            )
        )


def test_list_mine_includes_invited(clean_state: _FakeManager) -> None:
    ps = asyncio.run(
        pair_service.create_pair(None, OWNER, vmid=101, invitee_user_id=INVITEE_ID)
    )
    invitee = SimpleNamespace(id=INVITEE_ID)
    assert [p.id for p in pair_service.list_mine(invitee)] == [ps.id]
    stranger = SimpleNamespace(id=uuid.uuid4())
    assert pair_service.list_mine(stranger) == []


def test_end_pair_owner_only(clean_state: _FakeManager) -> None:
    ps = asyncio.run(
        pair_service.create_pair(None, OWNER, vmid=101, invitee_user_id=INVITEE_ID)
    )
    stranger = SimpleNamespace(id=uuid.uuid4())
    with pytest.raises(PermissionDeniedError):
        asyncio.run(pair_service.end_pair(stranger, ps.id))
    asyncio.run(pair_service.end_pair(OWNER, ps.id))
    assert clean_state.stopped == [ps.id]
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd backend && uv run pytest tests/services/test_pair_service.py -v`
Expected: FAIL（module not found）

- [ ] **Step 3: vnc_session_manager 改動**

`backend/app/services/classroom/vnc_session_manager.py`：

`SessionMode` 加值：

```python
class SessionMode(str, Enum):
    monitor = "monitor"
    broadcast = "broadcast"
    pair = "pair"
```

`_subscriber_reader` 的轉發條件改為：

```python
                allowed = state.mode is SessionMode.pair or (
                    state.controller_user_id is not None
                    and state.controller_user_id == subscriber.user_id
                )
                if allowed:
                    await state.upstream.send(message)
```

（pair session 的訂閱者已在 WS 層限定為 owner/受邀者/admin，故放行全部成員輸入。）

- [ ] **Step 4: 實作 pair_service**

`backend/app/services/classroom/pair_service.py`：

```python
"""協作實驗室 Pair Mode（E5）：owner 邀請同群組成員共同操作一台 VM。

Session 記錄存 in-memory（與 VncSessionManager 一致）；底層 VNC session
結束（含上游斷線）時由 on_session_end 回呼清掉 pair 記錄。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.core.permissions import is_admin
from app.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.models import Group, GroupMember, Resource, User
from app.services.classroom.vnc_session_manager import (
    SessionMode,
    vnc_session_manager,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PairSession:
    id: str
    vmid: int
    owner_id: uuid.UUID
    invitee_id: uuid.UUID
    created_at: datetime


_sessions: dict[str, PairSession] = {}


def _share_group(session: Session, a: uuid.UUID, b: uuid.UUID) -> bool:
    """兩人是否同屬任一群組（皆為成員，或一方為群組 owner 另一方為成員）。"""
    a_groups = set(
        session.exec(
            select(GroupMember.group_id).where(GroupMember.user_id == a)
        ).all()
    ) | set(session.exec(select(Group.id).where(Group.owner_id == a)).all())
    b_groups = set(
        session.exec(
            select(GroupMember.group_id).where(GroupMember.user_id == b)
        ).all()
    ) | set(session.exec(select(Group.id).where(Group.owner_id == b)).all())
    return bool(a_groups & b_groups)


def _get_owned_resource(session: Session, user: User, vmid: int) -> Resource:
    resource = session.get(Resource, vmid)
    if resource is None:
        raise NotFoundError(f"Resource {vmid} not found")
    if resource.user_id != user.id:
        raise PermissionDeniedError("只有 VM 擁有者可以發起協作")
    return resource


def _get_active_user(session: Session, user_id: uuid.UUID) -> User:
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise NotFoundError("受邀使用者不存在或已停用")
    return user


async def create_pair(
    session: Session, user: User, *, vmid: int, invitee_user_id: uuid.UUID
) -> PairSession:
    if invitee_user_id == user.id:
        raise BadRequestError("不能邀請自己")
    _get_owned_resource(session, user, vmid)
    _get_active_user(session, invitee_user_id)
    if not _share_group(session, user.id, invitee_user_id):
        raise PermissionDeniedError("只能邀請同群組的成員")
    if any(p.vmid == vmid for p in _sessions.values()):
        raise ConflictError(f"VM {vmid} 已有進行中的協作 session")

    live = await vnc_session_manager.start_session(
        vmid=vmid, mode=SessionMode.pair, group_id=None, started_by=user.id
    )
    pair = PairSession(
        id=live.id,
        vmid=vmid,
        owner_id=user.id,
        invitee_id=invitee_user_id,
        created_at=datetime.now(timezone.utc),
    )
    _sessions[pair.id] = pair
    logger.info(
        "Pair session %s started (vmid=%s owner=%s invitee=%s)",
        pair.id, vmid, user.id, invitee_user_id,
    )
    return pair


def list_mine(user: User) -> list[PairSession]:
    return [
        p
        for p in _sessions.values()
        if p.owner_id == user.id or p.invitee_id == user.id
    ]


def get_pair(session_id: str) -> PairSession | None:
    return _sessions.get(session_id)


def is_participant(session_id: str, user_id: uuid.UUID) -> bool:
    pair = _sessions.get(session_id)
    return pair is not None and user_id in (pair.owner_id, pair.invitee_id)


async def end_pair(user: User, session_id: str) -> None:
    pair = _sessions.get(session_id)
    if pair is None:
        raise NotFoundError("協作 session 不存在")
    if pair.owner_id != user.id and not is_admin(user):
        raise PermissionDeniedError("只有發起者或管理員可以結束協作")
    await vnc_session_manager.stop_session(session_id)
    _sessions.pop(session_id, None)


async def _on_session_end(snapshot, _reason: str) -> None:
    """底層 VNC session 結束（含上游斷線）時清掉 pair 記錄。"""
    _sessions.pop(snapshot.id, None)


vnc_session_manager.on_session_end(_on_session_end)
```

- [ ] **Step 5: 跑測試確認通過（含既有 classroom 測試迴歸）**

Run: `cd backend && uv run pytest tests/services/test_pair_service.py tests/services/test_vnc_session_manager.py tests/services/test_classroom_service.py -v`
Expected: 全部 PASS（既有測試不因 SessionMode 新值與 reader 改動而壞）

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/classroom/ backend/tests/services/test_pair_service.py
git commit -m "模組E教學體驗: Pair Mode session 與雙人輸入 (E5)"
```

---

### Task 17: E5 pair_sessions API + watch WS 權限

**Files:**
- Create: `backend/app/api/routes/pair_sessions.py`
- Create: `backend/app/schemas/pair_session.py`
- Modify: `backend/app/schemas/__init__.py`、`backend/app/api/main.py`
- Modify: `backend/app/api/websocket/classroom.py`（pair 分支）

**Interfaces:**
- Consumes: Task 16 `pair_service`
- Produces: `POST /pair-sessions`、`GET /pair-sessions/mine`、`DELETE /pair-sessions/{session_id}`；watch WS（`/ws/classroom/{session_id}/watch`）對 pair session 放行 owner/受邀者/admin

- [ ] **Step 1: schemas**

`backend/app/schemas/pair_session.py`：

```python
"""Pair Mode API schemas。"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class PairSessionCreate(BaseModel):
    vmid: int
    invitee_user_id: uuid.UUID


class PairSessionPublic(BaseModel):
    id: str
    vmid: int
    owner_id: uuid.UUID
    invitee_id: uuid.UUID
    owner_name: str | None = None
    invitee_name: str | None = None
    created_at: datetime
```

`schemas/__init__.py` 匯出兩者。

- [ ] **Step 2: route**

`backend/app/api/routes/pair_sessions.py`：

```python
"""協作實驗室 Pair Mode API（E5）。"""

import logging
import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep
from app.models import User
from app.schemas import PairSessionCreate, PairSessionPublic
from app.schemas.common import Message
from app.services.classroom import pair_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pair-sessions", tags=["pair-sessions"])


def _display_name(session: SessionDep, user_id: uuid.UUID) -> str | None:
    user = session.get(User, user_id)
    if user is None:
        return None
    return user.full_name or user.email


def _to_public(session: SessionDep, pair) -> PairSessionPublic:
    return PairSessionPublic(
        id=pair.id,
        vmid=pair.vmid,
        owner_id=pair.owner_id,
        invitee_id=pair.invitee_id,
        owner_name=_display_name(session, pair.owner_id),
        invitee_name=_display_name(session, pair.invitee_id),
        created_at=pair.created_at,
    )


@router.post("", response_model=PairSessionPublic, status_code=201)
async def create_pair_session(
    body: PairSessionCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> PairSessionPublic:
    pair = await pair_service.create_pair(
        session, current_user, vmid=body.vmid, invitee_user_id=body.invitee_user_id
    )
    return _to_public(session, pair)


@router.get("/mine", response_model=list[PairSessionPublic])
def list_my_pair_sessions(
    session: SessionDep, current_user: CurrentUser
) -> list[PairSessionPublic]:
    return [_to_public(session, p) for p in pair_service.list_mine(current_user)]


@router.delete("/{session_id}", response_model=Message)
async def end_pair_session(
    session_id: str, current_user: CurrentUser
) -> Message:
    await pair_service.end_pair(current_user, session_id)
    return Message(message="Pair session ended")
```

`api/main.py`：import `pair_sessions`、`api_router.include_router(pair_sessions.router)`（classroom 之後）。

- [ ] **Step 3: watch WS 權限**

`backend/app/api/websocket/classroom.py` 的 `classroom_watch_proxy`：import 加 `from app.services.classroom import pair_service`，權限判斷改為三分支——在既有 `if session.mode is SessionMode.monitor:` 之前插入：

```python
        if session.mode is SessionMode.pair:
            if not (
                is_admin(user)
                or pair_service.is_participant(session_id, user.id)
            ):
                await safe_close_websocket(
                    websocket, code=1008, reason="Permission denied"
                )
                return
        elif session.mode is SessionMode.monitor:
```

（原本的 `if session.mode is SessionMode.monitor:` 改成 `elif`，broadcast 分支維持 `else`。）

- [ ] **Step 4: 驗證**

Run: `cd backend && uv run python -c "from app.api.main import api_router; from app.api.websocket import classroom; print('ok')" && uv run ruff check app/api/`
Expected: `ok`、ruff 乾淨

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/ backend/app/schemas/
git commit -m "模組E教學體驗: pair-sessions API 與 watch WS 權限 (E5)"
```

---

### Task 18: 後端收尾：全量檢查 + OpenAPI client 重生成

**Files:**
- Modify: 修 lint/type 錯誤所及之處
- Modify: `frontend/src/client/*`（自動生成）

- [ ] **Step 1: 後端全量 lint + type check**

Run: `cd backend && uv run ruff check . && uv run mypy .`
Expected: 乾淨。mypy 錯誤逐一修正（不加 `# type: ignore` 除非既有模式如此）。

- [ ] **Step 2: 後端全量測試**

Run: `cd backend && uv run pytest tests/ -x -q --ignore=tests/performance`
Expected: 全部 PASS（performance 層 1 需測試 DB，跳過；若環境有 Docker 測試 DB 則跑 `bash ./scripts/test.sh`）。

- [ ] **Step 3: 重新生成前端 client**

啟動後端（Docker：`docker compose up -d backend`，或本機 `cd backend && fastapi dev app/main.py` 背景執行），然後：

Run: `bash ./scripts/generate-client.sh`（專案根目錄）
Expected: `frontend/src/client/` 更新，含 `QuotasService`、`TeachingService`、`PairSessionsService` 與新 schemas。

- [ ] **Step 4: Commit**

```bash
git add backend/ frontend/src/client/
git commit -m "模組E教學體驗: 後端收尾與 API client 重生成"
```

---

### Task 19: 前端 — 快照分頁強化 + 一鍵重置（E1/E4）

**Files:**
- Modify: `frontend/src/components/ResourceDetail/SnapshotsTab.tsx`
- Modify: `frontend/src/locales/zh-TW/resourceDetail.json`、`frontend/src/locales/en/resourceDetail.json`、`frontend/src/locales/ja/resourceDetail.json`

**Interfaces:**
- Consumes: `ResourceDetailsService.listSnapshots/createSnapshot/deleteSnapshot/rollbackSnapshot`（既有）、`POST /resources/{vmid}/reset`、`POST /resources/{vmid}/init-snapshot`（新，用 `__request`）

- [ ] **Step 1: SnapshotsTab 強化**

`frontend/src/components/ResourceDetail/SnapshotsTab.tsx` 修改：

1. import 加：

```tsx
import { RefreshCcw, ShieldCheck } from "lucide-react"
import { OpenAPI } from "@/client/core/OpenAPI"
import { request as __request } from "@/client/core/request"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
```

（若專案無 `alert-dialog` 元件，改用既有 `Dialog` 做二次確認。）

2. 元件內加重置 mutation 與 state：

```tsx
  const [resetDialogOpen, setResetDialogOpen] = useState(false)

  const hasInitSnapshot = snapshots.some((s) => s.name === "skylab-init")

  const resetMutation = useMutation({
    mutationFn: () =>
      __request(OpenAPI, {
        method: "POST",
        url: "/api/v1/resources/{vmid}/reset",
        path: { vmid },
      }),
    onSuccess: () => {
      toast.success(t("snapshots.resetStarted"))
      setResetDialogOpen(false)
    },
    onError: (err: unknown) => {
      const detail = (err as { body?: { detail?: string } })?.body?.detail
      toast.error(detail || t("snapshots.resetError"))
    },
  })

  const initSnapshotMutation = useMutation({
    mutationFn: () =>
      __request(OpenAPI, {
        method: "POST",
        url: "/api/v1/resources/{vmid}/init-snapshot",
        path: { vmid },
      }),
    onSuccess: () => {
      toast.success(t("snapshots.initCreated"))
      queryClient.invalidateQueries({ queryKey: ["snapshots", vmid] })
    },
    onError: (err: unknown) => {
      const detail = (err as { body?: { detail?: string } })?.body?.detail
      toast.error(detail || t("snapshots.initError"))
    },
  })
```

注意：`__request` 的 URL 前綴以 `admin.configuration.tsx` 內既有呼叫為準（若該檔寫 `url: "/api/v1/..."` 就照抄樣式；若 OpenAPI.BASE 已含前綴則用 `"/resources/{vmid}/reset"`）。實作時先看該檔一眼再照抄。

3. CardHeader 的按鈕區（`<Dialog ...>` 之前）加重置按鈕：

```tsx
              <Button
                variant="outline"
                onClick={() => setResetDialogOpen(true)}
                disabled={!hasInitSnapshot || resetMutation.isPending}
                title={
                  hasInitSnapshot
                    ? undefined
                    : t("snapshots.resetUnavailable")
                }
              >
                <RefreshCcw className="h-4 w-4 mr-1" />
                {t("snapshots.reset")}
              </Button>
              {!hasInitSnapshot && (
                <Button
                  variant="ghost"
                  onClick={() => initSnapshotMutation.mutate()}
                  disabled={initSnapshotMutation.isPending}
                >
                  {t("snapshots.createInit")}
                </Button>
              )}
```

4. 重置二次確認 AlertDialog（元件 return 的最外層 `<div>` 內尾端）：

```tsx
      <AlertDialog open={resetDialogOpen} onOpenChange={setResetDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("snapshots.resetConfirmTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("snapshots.resetConfirmBody")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("common.cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={() => resetMutation.mutate()}>
              {t("snapshots.resetConfirmAction")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
```

5. 快照列表列：`skylab-init` 列顯示保護徽章並隱藏刪除鈕：

```tsx
                    <TableCell className="font-medium">
                      {snap.name}
                      {snap.name === "skylab-init" && (
                        <Badge variant="secondary" className="ml-2">
                          <ShieldCheck className="h-3 w-3 mr-1" />
                          {t("snapshots.protected")}
                        </Badge>
                      )}
                    </TableCell>
```

刪除按鈕外包 `{snap.name !== "skylab-init" && (...)}`。

- [ ] **Step 2: i18n 三語 key**

`frontend/src/locales/zh-TW/resourceDetail.json` 的 `snapshots` 物件加（en/ja 對應翻譯同步加）：

```json
{
  "reset": "還原初始狀態",
  "resetUnavailable": "此資源沒有初始快照，無法重置",
  "resetStarted": "重置任務已開始，完成後 VM 將回到初始狀態",
  "resetError": "重置失敗",
  "resetConfirmTitle": "確定要還原初始狀態？",
  "resetConfirmBody": "這會將 VM 回滾到 skylab-init 初始快照，快照之後的所有變更（含已安裝軟體與資料）都會遺失，且無法復原。",
  "resetConfirmAction": "確定重置",
  "createInit": "補建初始快照",
  "initCreated": "初始快照已建立",
  "initError": "初始快照建立失敗",
  "protected": "受保護"
}
```

- [ ] **Step 3: 驗證**

Run: `cd frontend && bun run build`
Expected: tsc + vite 無錯誤。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ResourceDetail/SnapshotsTab.tsx frontend/src/locales/
git commit -m "模組E教學體驗: 前端快照保護與一鍵重置 (E1/E4)"
```

---

### Task 20: 前端 — 老師教學面板（熱圖 / 分發 / 批次調整）

**Files:**
- Create: `frontend/src/routes/_layout/teaching.tsx`
- Create: `frontend/src/components/Teaching/HeatmapPanel.tsx`
- Create: `frontend/src/components/Teaching/ConfigPushPanel.tsx`
- Create: `frontend/src/components/Teaching/BatchSpecPanel.tsx`
- Modify: 側邊欄導航（先 `grep -rn "classroom" frontend/src/components --include="*.tsx" -l` 找到 sidebar 項目定義檔，在教室項目旁加「教學面板」項，限 teacher/admin 顯示，比照教室項的權限判斷）

**Interfaces:**
- Consumes: `GET /teaching/heatmap?group_id=`、`POST /teaching/config-push`（multipart）、`GET /teaching/config-push/{task_id}`、`POST /teaching/batch-spec`、`GET /teaching/batch-spec/{task_id}`、群組清單（既有 `GroupsService` 或 groups 頁使用的 service，照抄 `groups.tsx` 的取法）
- Produces: `/teaching` 頁（僅 teacher/admin；路由 guard 比照 `classroom.tsx` 的作法）

- [ ] **Step 1: route 骨架**

`frontend/src/routes/_layout/teaching.tsx`：

```tsx
import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"
import { OpenAPI } from "@/client/core/OpenAPI"
import { request as __request } from "@/client/core/request"
import BatchSpecPanel from "@/components/Teaching/BatchSpecPanel"
import ConfigPushPanel from "@/components/Teaching/ConfigPushPanel"
import HeatmapPanel from "@/components/Teaching/HeatmapPanel"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export const Route = createFileRoute("/_layout/teaching")({
  component: TeachingPage,
  // beforeLoad 權限 guard：比照 classroom.tsx（老師/管理員）；
  // 實作時開 classroom.tsx 抄同一個 guard。
})

interface GroupPublic {
  id: string
  name: string
}

function TeachingPage() {
  const [groupId, setGroupId] = useState<string>("")

  const { data: groups } = useQuery({
    queryKey: ["teaching-groups"],
    queryFn: () =>
      __request(OpenAPI, {
        method: "GET",
        url: "/api/v1/groups/",
      }) as Promise<GroupPublic[]>,
  })
  // 注意：群組清單端點與回傳形狀以 groups.tsx 實際用法為準（可能是
  // GroupsService.readGroups()，回傳 {data: [...]}）。實作時照抄 groups.tsx。

  return (
    <div className="container mx-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">教學面板</h1>
        <Select value={groupId} onValueChange={setGroupId}>
          <SelectTrigger className="w-64">
            <SelectValue placeholder="選擇群組" />
          </SelectTrigger>
          <SelectContent>
            {(groups ?? []).map((g) => (
              <SelectItem key={g.id} value={g.id}>
                {g.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {groupId ? (
        <Tabs defaultValue="heatmap">
          <TabsList>
            <TabsTrigger value="heatmap">學生進度熱圖</TabsTrigger>
            <TabsTrigger value="push">配置文件分發</TabsTrigger>
            <TabsTrigger value="spec">批次調整規格</TabsTrigger>
          </TabsList>
          <TabsContent value="heatmap">
            <HeatmapPanel groupId={groupId} />
          </TabsContent>
          <TabsContent value="push">
            <ConfigPushPanel groupId={groupId} />
          </TabsContent>
          <TabsContent value="spec">
            <BatchSpecPanel groupId={groupId} />
          </TabsContent>
        </Tabs>
      ) : (
        <div className="text-center py-16 text-muted-foreground">
          請先選擇一個群組
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: HeatmapPanel**

`frontend/src/components/Teaching/HeatmapPanel.tsx`：

```tsx
import { useQuery } from "@tanstack/react-query"
import { OpenAPI } from "@/client/core/OpenAPI"
import { request as __request } from "@/client/core/request"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

interface HeatmapEntry {
  vmid: number
  name: string | null
  owner_name: string | null
  status: string
  cpu_percent: number
  mem_percent: number
  uptime_seconds: number
  activity: "running" | "idle" | "stale" | "stopped"
}

function cellColor(entry: HeatmapEntry): string {
  if (entry.activity === "stopped") return "bg-gray-300 dark:bg-gray-700"
  if (entry.activity === "stale") return "bg-gray-500"
  if (entry.cpu_percent >= 80) return "bg-red-500"
  if (entry.cpu_percent >= 50) return "bg-orange-400"
  if (entry.cpu_percent >= 10) return "bg-green-500"
  return "bg-green-200"
}

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h > 0 ? `${h} 小時 ${m} 分` : `${m} 分`
}

export default function HeatmapPanel({ groupId }: { groupId: string }) {
  const { data: entries } = useQuery({
    queryKey: ["teaching-heatmap", groupId],
    queryFn: () =>
      __request(OpenAPI, {
        method: "GET",
        url: "/api/v1/teaching/heatmap",
        query: { group_id: groupId },
      }) as Promise<HeatmapEntry[]>,
    refetchInterval: 30_000,
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>學生進度熱圖（30 秒自動更新）</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex gap-3 text-xs text-muted-foreground mb-4">
          <span>■ 灰＝關機</span>
          <span className="text-green-600">■ 綠＝運行</span>
          <span className="text-orange-500">■ 橘/紅＝高 CPU</span>
          <span className="text-gray-500">■ 深灰＝長期無動靜</span>
        </div>
        <TooltipProvider>
          <div className="grid grid-cols-6 md:grid-cols-10 gap-2">
            {(entries ?? []).map((entry) => (
              <Tooltip key={entry.vmid}>
                <TooltipTrigger asChild>
                  <div
                    className={cn(
                      "aspect-square rounded flex items-center justify-center",
                      "text-[10px] text-white font-medium cursor-default",
                      cellColor(entry),
                    )}
                  >
                    {entry.vmid}
                  </div>
                </TooltipTrigger>
                <TooltipContent>
                  <div className="text-xs space-y-0.5">
                    <div>{entry.owner_name ?? "—"}（{entry.name ?? entry.vmid}）</div>
                    <div>狀態：{entry.status}</div>
                    <div>CPU：{entry.cpu_percent}%　RAM：{entry.mem_percent}%</div>
                    <div>開機時長：{formatUptime(entry.uptime_seconds)}</div>
                    {entry.activity === "stale" && <div>⚠ 長期無動靜</div>}
                  </div>
                </TooltipContent>
              </Tooltip>
            ))}
          </div>
        </TooltipProvider>
        {(entries ?? []).length === 0 && (
          <div className="text-center py-8 text-muted-foreground">
            此群組沒有學生 VM
          </div>
        )}
      </CardContent>
    </Card>
  )
}
```

- [ ] **Step 3: ConfigPushPanel**

`frontend/src/components/Teaching/ConfigPushPanel.tsx`：核心結構——用 heatmap 端點取得群組 VM 清單做多選表格（vmid / 擁有者 / 狀態 + Checkbox），檔案 `<Input type="file">` + 目標路徑輸入，送出用 `FormData`：

```tsx
import { useMutation, useQuery } from "@tanstack/react-query"
import { useRef, useState } from "react"
import { toast } from "sonner"
import { OpenAPI } from "@/client/core/OpenAPI"
import { request as __request } from "@/client/core/request"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"

interface HeatmapEntry {
  vmid: number
  name: string | null
  owner_name: string | null
  status: string
}

interface PushItem {
  vmid: number
  status: "pending" | "running" | "ok" | "error"
  error: string | null
}

interface PushStatus {
  task_id: string
  items: PushItem[]
}

export default function ConfigPushPanel({ groupId }: { groupId: string }) {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [targetPath, setTargetPath] = useState("")
  const [taskId, setTaskId] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const { data: vms } = useQuery({
    queryKey: ["teaching-heatmap", groupId],
    queryFn: () =>
      __request(OpenAPI, {
        method: "GET",
        url: "/api/v1/teaching/heatmap",
        query: { group_id: groupId },
      }) as Promise<HeatmapEntry[]>,
  })

  const { data: status } = useQuery({
    queryKey: ["config-push", taskId],
    enabled: taskId !== null,
    refetchInterval: (q) =>
      (q.state.data as PushStatus | undefined)?.items.some(
        (i) => i.status === "pending" || i.status === "running",
      )
        ? 2000
        : false,
    queryFn: () =>
      __request(OpenAPI, {
        method: "GET",
        url: "/api/v1/teaching/config-push/{task_id}",
        path: { task_id: taskId },
      }) as Promise<PushStatus>,
  })

  const pushMutation = useMutation({
    mutationFn: () => {
      const file = fileRef.current?.files?.[0]
      if (!file) throw new Error("no-file")
      const formData = new FormData()
      formData.append("file", file)
      formData.append("target_path", targetPath)
      for (const vmid of selected) formData.append("vmids", String(vmid))
      return __request(OpenAPI, {
        method: "POST",
        url: "/api/v1/teaching/config-push",
        body: formData,
        mediaType: "multipart/form-data",
      }) as Promise<{ task_id: string }>
    },
    onSuccess: (data) => {
      toast.success("分發任務已開始")
      setTaskId(data.task_id)
    },
    onError: (err: unknown) => {
      const detail = (err as { body?: { detail?: string } })?.body?.detail
      toast.error(detail || "分發啟動失敗（檔案上限 1 MB，路徑必須為絕對路徑）")
    },
  })
  // 注意：__request 的 FormData 傳法以生成的 client core 為準；若不支援，
  // 改用 fetch(`${OpenAPI.BASE}/api/v1/teaching/config-push`, {method:"POST",
  // headers:{Authorization:`Bearer ${localStorage.getItem("access_token")}`},
  // body: formData})——token 取得方式照抄專案內既有 fetch 用法。

  const toggle = (vmid: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(vmid)) next.delete(vmid)
      else next.add(vmid)
      return next
    })
  }

  const statusLabel: Record<PushItem["status"], string> = {
    pending: "等待中",
    running: "分發中",
    ok: "成功",
    error: "失敗",
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>配置文件分發</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label>配置檔案（上限 1 MB）</Label>
              <Input type="file" ref={fileRef} />
            </div>
            <div className="space-y-2">
              <Label>目標絕對路徑</Label>
              <Input
                value={targetPath}
                onChange={(e) => setTargetPath(e.target.value)}
                placeholder="/etc/nginx/nginx.conf"
              />
            </div>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10" />
                <TableHead>VMID</TableHead>
                <TableHead>名稱</TableHead>
                <TableHead>擁有者</TableHead>
                <TableHead>狀態</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(vms ?? []).map((vm) => (
                <TableRow key={vm.vmid}>
                  <TableCell>
                    <Checkbox
                      checked={selected.has(vm.vmid)}
                      onCheckedChange={() => toggle(vm.vmid)}
                    />
                  </TableCell>
                  <TableCell>{vm.vmid}</TableCell>
                  <TableCell>{vm.name ?? "-"}</TableCell>
                  <TableCell>{vm.owner_name ?? "-"}</TableCell>
                  <TableCell>{vm.status}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Button
            onClick={() => pushMutation.mutate()}
            disabled={
              selected.size === 0 ||
              !targetPath.startsWith("/") ||
              pushMutation.isPending
            }
          >
            分發到 {selected.size} 台 VM
          </Button>
        </CardContent>
      </Card>

      {status && (
        <Card>
          <CardHeader>
            <CardTitle>分發結果</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>VMID</TableHead>
                  <TableHead>結果</TableHead>
                  <TableHead>原因</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {status.items.map((item) => (
                  <TableRow key={item.vmid}>
                    <TableCell>{item.vmid}</TableCell>
                    <TableCell
                      className={
                        item.status === "ok"
                          ? "text-green-600"
                          : item.status === "error"
                            ? "text-red-600"
                            : ""
                      }
                    >
                      {statusLabel[item.status]}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {item.error ?? "-"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
```

- [ ] **Step 4: BatchSpecPanel**

`frontend/src/components/Teaching/BatchSpecPanel.tsx`：與 ConfigPushPanel 同構——VM 多選表格（同一 heatmap query）、cores/memory 數字輸入、送 `POST /api/v1/teaching/batch-spec`（JSON body `{vmids, cores, memory_mb}`）、輪詢 `GET /api/v1/teaching/batch-spec/{task_id}`。結果狀態文案：`ok`→「已生效」（綠）、`needs_restart`→「需重啟生效」（橘）、`quota_exceeded`→「超出配額」（紅）、`error`→「失敗」（紅）。`needs_restart` 的列提供「重啟」按鈕：呼叫既有電源操作端點（開 `frontend/src` 內 grep `"reboot"` 找既有 resources 電源 mutation，照抄呼叫方式）。結構照抄 Step 3 的元件，僅替換表單欄位與結果對映，此處不重複貼全文——實作時以 ConfigPushPanel 為模板逐段改。

- [ ] **Step 5: 側邊欄導航 + 驗證**

按本 Task 開頭的 grep 指令找到 sidebar 定義，在教室項旁加：標題「教學面板」、路徑 `/teaching`、圖示 `LayoutGrid`（lucide），權限判斷照抄教室項。

Run: `cd frontend && bun run build && bun run lint`
Expected: 無錯誤。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/
git commit -m "模組E教學體驗: 老師教學面板（熱圖/分發/批次） (E2/E3/E6)"
```

---

### Task 21: 前端 — 協作邀請 UI（E5）

**Files:**
- Create: `frontend/src/components/Teaching/PairInviteDialog.tsx`
- Create: `frontend/src/components/Teaching/PairInvitesCard.tsx`
- Modify: 資源詳情頁（`frontend/src/components/ResourceDetail/ResourceDetailPage.tsx`）加「邀請協作」按鈕
- Modify: 資源列表頁（`frontend/src/routes/_layout/my-resources.tsx` 或其主元件）頂部掛 `PairInvitesCard`

**Interfaces:**
- Consumes: `POST /pair-sessions`、`GET /pair-sessions/mine`、`DELETE /pair-sessions/{session_id}`；觀看畫面複用既有教室 watch 檢視器
- 前置探索（實作第一步執行，結果決定「加入協作」按鈕的導向）：
  - `grep -rn "watch" frontend/src/routes/_layout/classroom.tsx frontend/src/components --include="*.tsx" -l` 找到連 `/ws/classroom/{session_id}/watch` 的檢視器元件與其 props；
  - 同群組成員清單取法：開 `frontend/src` 內 groups 相關頁面找成員查詢端點照抄。

- [ ] **Step 1: PairInviteDialog**

`frontend/src/components/Teaching/PairInviteDialog.tsx`：

```tsx
import { useMutation, useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"
import { OpenAPI } from "@/client/core/OpenAPI"
import { request as __request } from "@/client/core/request"
import { Button } from "@/components/ui/button"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"

interface Member {
  id: string
  email: string
  full_name: string | null
}

interface Props {
  vmid: number
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (sessionId: string) => void
}

export default function PairInviteDialog({
  vmid, open, onOpenChange, onCreated,
}: Props) {
  const [inviteeId, setInviteeId] = useState("")

  // 同群組成員清單：實作時照抄 groups 頁的成員查詢（端點與回傳形狀
  // 以現有程式為準）；此處假設 GET /api/v1/groups/ 後逐群取成員，
  // 或既有 hook 可直接給「我的群組成員」。
  const { data: members } = useQuery({
    queryKey: ["pair-invite-candidates"],
    queryFn: async () => {
      const groups = (await __request(OpenAPI, {
        method: "GET",
        url: "/api/v1/groups/",
      })) as { id: string }[]
      const lists = await Promise.all(
        groups.map(
          (g) =>
            __request(OpenAPI, {
              method: "GET",
              url: "/api/v1/groups/{group_id}/members",
              path: { group_id: g.id },
            }) as Promise<Member[]>,
        ),
      )
      const seen = new Map<string, Member>()
      for (const m of lists.flat()) seen.set(m.id, m)
      return [...seen.values()]
    },
    enabled: open,
  })

  const createMutation = useMutation({
    mutationFn: () =>
      __request(OpenAPI, {
        method: "POST",
        url: "/api/v1/pair-sessions",
        body: { vmid, invitee_user_id: inviteeId },
        mediaType: "application/json",
      }) as Promise<{ id: string }>,
    onSuccess: (data) => {
      toast.success("協作邀請已送出，雙方可進入同一畫面")
      onOpenChange(false)
      onCreated(data.id)
    },
    onError: (err: unknown) => {
      const detail = (err as { body?: { detail?: string } })?.body?.detail
      toast.error(detail || "建立協作失敗")
    },
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>邀請協作（Pair Mode）</DialogTitle>
          <DialogDescription>
            邀請同群組成員與你共同操作這台 VM，雙方皆可輸入。
          </DialogDescription>
        </DialogHeader>
        <Select value={inviteeId} onValueChange={setInviteeId}>
          <SelectTrigger>
            <SelectValue placeholder="選擇同群組成員" />
          </SelectTrigger>
          <SelectContent>
            {(members ?? []).map((m) => (
              <SelectItem key={m.id} value={m.id}>
                {m.full_name || m.email}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => createMutation.mutate()}
            disabled={!inviteeId || createMutation.isPending}
          >
            送出邀請
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 2: PairInvitesCard**

`frontend/src/components/Teaching/PairInvitesCard.tsx`：

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Users } from "lucide-react"
import { OpenAPI } from "@/client/core/OpenAPI"
import { request as __request } from "@/client/core/request"
import useAuth from "@/hooks/useAuth"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface PairSession {
  id: string
  vmid: number
  owner_id: string
  invitee_id: string
  owner_name: string | null
  invitee_name: string | null
}

export default function PairInvitesCard({
  onJoin,
}: {
  onJoin: (sessionId: string) => void
}) {
  const { user } = useAuth()
  const queryClient = useQueryClient()

  const { data: sessions } = useQuery({
    queryKey: ["pair-sessions-mine"],
    queryFn: () =>
      __request(OpenAPI, {
        method: "GET",
        url: "/api/v1/pair-sessions/mine",
      }) as Promise<PairSession[]>,
    refetchInterval: 15_000,
  })

  const endMutation = useMutation({
    mutationFn: (sessionId: string) =>
      __request(OpenAPI, {
        method: "DELETE",
        url: "/api/v1/pair-sessions/{session_id}",
        path: { session_id: sessionId },
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["pair-sessions-mine"] }),
  })

  if (!sessions?.length) return null

  return (
    <Card className="border-primary/40">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Users className="h-4 w-4" />
          協作邀請
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {sessions.map((s) => {
          const isOwner = s.owner_id === user?.id
          return (
            <div
              key={s.id}
              className="flex items-center justify-between rounded border p-3"
            >
              <div className="text-sm">
                {isOwner
                  ? `你邀請 ${s.invitee_name ?? "成員"} 協作 VM ${s.vmid}`
                  : `${s.owner_name ?? "成員"} 邀請你協作 VM ${s.vmid}`}
              </div>
              <div className="space-x-2">
                <Button size="sm" onClick={() => onJoin(s.id)}>
                  加入
                </Button>
                {isOwner && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => endMutation.mutate(s.id)}
                  >
                    結束
                  </Button>
                )}
              </div>
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}
```

（`useAuth` 的 user 形狀以 `frontend/src/hooks/useAuth.ts` 為準，實作時確認欄位名。）

- [ ] **Step 3: 整合**

1. 資源詳情頁：在動作按鈕區加「邀請協作」按鈕（僅當前使用者為 owner 顯示——詳情頁已有 owner 資訊即可判斷；找不到就永遠顯示、由後端 403 擋），開 `PairInviteDialog`；`onCreated` 導向 watch 檢視器（前置探索找到的元件/路由，把 `session_id` 傳入）。
2. 資源列表頁頂部掛 `<PairInvitesCard onJoin={...} />`，`onJoin` 同上導向 watch 檢視器。
3. 若教室 watch 檢視器綁死在 classroom 頁內不可重用，建 `frontend/src/routes/_layout/pair.$sessionId.tsx` 包同一個 VNC watch 元件（該元件連 `/ws/classroom/{session_id}/watch`，pair 已由後端放行）。

- [ ] **Step 4: 驗證 + Commit**

Run: `cd frontend && bun run build && bun run lint`
Expected: 無錯誤。

```bash
git add frontend/src/
git commit -m "模組E教學體驗: 協作邀請 UI (E5)"
```

---

### Task 22: 前端 — admin 配額頁 + governance 欄位 + 用量條（E7/E8）

**Files:**
- Create: `frontend/src/routes/_layout/admin.quotas.tsx`
- Create: `frontend/src/components/Teaching/QuotaUsageBar.tsx`
- Modify: `frontend/src/components/Admin/GovernanceConfigTab.tsx`（三欄位）
- Modify: 資源列表頁掛 `QuotaUsageBar`；admin 導航加「配額管理」（照 admin.* 既有頁面的導航註冊方式）

- [ ] **Step 1: GovernanceConfigTab 加三欄位**

開 `frontend/src/components/Admin/GovernanceConfigTab.tsx`：

1. config interface 加：

```tsx
  snapshot_cleanup_enabled: boolean
  snapshot_retention_days: number
  student_snapshot_max_count: number
```

2. form reset/defaults 物件（約 line 97 一帶）同步加三鍵。
3. UI 區塊照挖礦區塊樣式新增「快照治理」段落：開關 `snapshot_cleanup_enabled`（沿用既有 switch helper，如 line 354 的用法）、`numberField("snapshot_retention_days", "保留天數", { min: 1, max: 90 })`、`numberField("student_snapshot_max_count", "學生快照上限", { min: 1, max: 10 })`——helper 名稱與參數以檔內既有寫法為準。

- [ ] **Step 2: admin.quotas.tsx**

`frontend/src/routes/_layout/admin.quotas.tsx`：admin guard 照抄 `admin.configuration.tsx`（`requireAdminUser`）。內容：

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Trash2 } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"
import { OpenAPI } from "@/client/core/OpenAPI"
import { request as __request } from "@/client/core/request"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { requireAdminUser } from "@/features/auth/guards"

export const Route = createFileRoute("/_layout/admin/quotas")({
  component: AdminQuotasPage,
  beforeLoad: requireAdminUser,
})
// 注意：beforeLoad 的掛法以 admin.configuration.tsx 實際寫法為準。

interface QuotaRow {
  id: string
  scope: "group" | "user"
  group_id: string | null
  user_id: string | null
  group_name: string | null
  user_email: string | null
  max_cpu_cores: number
  max_memory_mb: number
  max_disk_gb: number
  max_instances: number
}

const EMPTY_FORM = {
  scope: "group" as "group" | "user",
  target: "",
  max_cpu_cores: 8,
  max_memory_mb: 16384,
  max_disk_gb: 100,
  max_instances: 5,
}

function AdminQuotasPage() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)

  const { data: quotas } = useQuery({
    queryKey: ["quotas"],
    queryFn: () =>
      __request(OpenAPI, { method: "GET", url: "/api/v1/quotas" }) as Promise<
        QuotaRow[]
      >,
  })

  const { data: groups } = useQuery({
    queryKey: ["quota-groups"],
    queryFn: () =>
      __request(OpenAPI, { method: "GET", url: "/api/v1/groups/" }) as Promise<
        { id: string; name: string }[]
      >,
    // 群組/使用者清單端點以既有頁面為準（groups.tsx / admin 使用者頁）。
  })

  const createMutation = useMutation({
    mutationFn: () =>
      __request(OpenAPI, {
        method: "POST",
        url: "/api/v1/quotas",
        mediaType: "application/json",
        body: {
          scope: form.scope,
          group_id: form.scope === "group" ? form.target : null,
          user_id: form.scope === "user" ? form.target : null,
          max_cpu_cores: form.max_cpu_cores,
          max_memory_mb: form.max_memory_mb,
          max_disk_gb: form.max_disk_gb,
          max_instances: form.max_instances,
        },
      }),
    onSuccess: () => {
      toast.success("配額已建立")
      setDialogOpen(false)
      setForm(EMPTY_FORM)
      queryClient.invalidateQueries({ queryKey: ["quotas"] })
    },
    onError: (err: unknown) => {
      const detail = (err as { body?: { detail?: string } })?.body?.detail
      toast.error(detail || "建立失敗")
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      __request(OpenAPI, {
        method: "DELETE",
        url: "/api/v1/quotas/{quota_id}",
        path: { quota_id: id },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["quotas"] }),
  })

  const numberInput = (
    key: keyof typeof EMPTY_FORM,
    label: string,
  ) => (
    <div className="space-y-1">
      <Label>{label}</Label>
      <Input
        type="number"
        value={form[key] as number}
        onChange={(e) =>
          setForm({ ...form, [key]: Number(e.target.value) })
        }
      />
    </div>
  )

  return (
    <div className="container mx-auto p-6 space-y-6">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>資源配額管理</CardTitle>
          <Button onClick={() => setDialogOpen(true)}>新增配額</Button>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>範圍</TableHead>
                <TableHead>對象</TableHead>
                <TableHead>CPU</TableHead>
                <TableHead>記憶體 (MB)</TableHead>
                <TableHead>磁碟 (GB)</TableHead>
                <TableHead>台數</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(quotas ?? []).map((q) => (
                <TableRow key={q.id}>
                  <TableCell>{q.scope === "group" ? "群組" : "個人覆寫"}</TableCell>
                  <TableCell>{q.group_name ?? q.user_email ?? "-"}</TableCell>
                  <TableCell>{q.max_cpu_cores}</TableCell>
                  <TableCell>{q.max_memory_mb}</TableCell>
                  <TableCell>{q.max_disk_gb}</TableCell>
                  <TableCell>{q.max_instances}</TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => deleteMutation.mutate(q.id)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新增配額</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>範圍</Label>
              <Select
                value={form.scope}
                onValueChange={(v) =>
                  setForm({ ...form, scope: v as "group" | "user", target: "" })
                }
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="group">群組預設</SelectItem>
                  <SelectItem value="user">個人覆寫</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {form.scope === "group" ? (
              <div className="space-y-1">
                <Label>群組</Label>
                <Select
                  value={form.target}
                  onValueChange={(v) => setForm({ ...form, target: v })}
                >
                  <SelectTrigger><SelectValue placeholder="選擇群組" /></SelectTrigger>
                  <SelectContent>
                    {(groups ?? []).map((g) => (
                      <SelectItem key={g.id} value={g.id}>{g.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : (
              <div className="space-y-1">
                <Label>使用者 ID</Label>
                <Input
                  value={form.target}
                  onChange={(e) => setForm({ ...form, target: e.target.value })}
                  placeholder="使用者 UUID（或改為下拉，照 admin 使用者頁取清單）"
                />
              </div>
            )}
            <div className="grid grid-cols-2 gap-3">
              {numberInput("max_cpu_cores", "CPU cores")}
              {numberInput("max_memory_mb", "記憶體 (MB)")}
              {numberInput("max_disk_gb", "磁碟 (GB)")}
              {numberInput("max_instances", "實例數")}
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              取消
            </Button>
            <Button
              onClick={() => createMutation.mutate()}
              disabled={!form.target || createMutation.isPending}
            >
              建立
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
```

- [ ] **Step 3: QuotaUsageBar**

`frontend/src/components/Teaching/QuotaUsageBar.tsx`：

```tsx
import { useQuery } from "@tanstack/react-query"
import { OpenAPI } from "@/client/core/OpenAPI"
import { request as __request } from "@/client/core/request"
import { Card, CardContent } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"

interface Usage {
  used_cpu_cores: number
  used_memory_mb: number
  used_disk_gb: number
  used_instances: number
  quota: {
    max_cpu_cores: number
    max_memory_mb: number
    max_disk_gb: number
    max_instances: number
  }
}

function Meter({ label, used, max, unit }: {
  label: string; used: number; max: number; unit: string
}) {
  const pct = max > 0 ? Math.min(100, (used / max) * 100) : 0
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className={pct >= 90 ? "text-red-600 font-medium" : ""}>
          {used} / {max} {unit}
        </span>
      </div>
      <Progress value={pct} />
    </div>
  )
}

export default function QuotaUsageBar() {
  const { data } = useQuery({
    queryKey: ["my-quota-usage"],
    queryFn: () =>
      __request(OpenAPI, {
        method: "GET",
        url: "/api/v1/quotas/my-usage",
      }) as Promise<Usage>,
    staleTime: 60_000,
  })

  if (!data) return null

  return (
    <Card>
      <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-6">
        <Meter label="CPU" used={data.used_cpu_cores}
          max={data.quota.max_cpu_cores} unit="cores" />
        <Meter label="記憶體" used={Math.round(data.used_memory_mb / 1024)}
          max={Math.round(data.quota.max_memory_mb / 1024)} unit="GB" />
        <Meter label="磁碟" used={data.used_disk_gb}
          max={data.quota.max_disk_gb} unit="GB" />
        <Meter label="實例" used={data.used_instances}
          max={data.quota.max_instances} unit="台" />
      </CardContent>
    </Card>
  )
}
```

（若專案沒有 `Progress` 元件，改用 `div` + width % 的 Tailwind bar。）掛到資源列表頁（`my-resources.tsx` 主內容頂部）。

- [ ] **Step 4: admin 導航 + 驗證 + Commit**

admin 導航加「配額管理」項（照 admin.* 頁面的導航清單定義處，grep `"admin.configuration"` 或「系統設定」字樣定位）。

Run: `cd frontend && bun run build && bun run lint`
Expected: 無錯誤。

```bash
git add frontend/src/
git commit -m "模組E教學體驗: 配額管理頁/用量條/快照治理設定 (E7/E8)"
```

---

### Task 23: 最終驗證與收尾

- [ ] **Step 1: 後端全量**

Run: `cd backend && uv run ruff check . && uv run mypy . && uv run pytest tests/ -q --ignore=tests/performance`
Expected: 全部乾淨/PASS。

- [ ] **Step 2: 前端全量**

Run: `cd frontend && bun run lint && bun run build`
Expected: 無錯誤。

- [ ] **Step 3: Docker 冒煙（環境允許時）**

Run: `docker compose up -d --build backend && docker compose logs backend --tail 50`
Expected: migration `e01_teaching` 套用成功、scheduler 啟動 log 含 `process_snapshot_cleanup`、無 startup error。用 `http://localhost:8000/docs` 確認 `/quotas`、`/teaching/*`、`/pair-sessions`、`/resources/{vmid}/reset` 端點存在。

- [ ] **Step 4: 收尾 commit（如有殘留變更）**

```bash
git status
git add -A && git commit -m "模組E教學體驗: 收尾與驗證 (E1-E8)"
```

完成後使用 superpowers:finishing-a-development-branch 技能決定合併方式。

---

## Self-Review 結果（計畫作者已檢查）

1. **Spec coverage**：E1（Task 7/8/19）、E2（Task 12/13/20）、E3（Task 14/20）、E4（Task 9/19 + `student_snapshot_max_count` Task 1/11/22）、E5（Task 16/17/21）、E6（Task 15/20）、E7（Task 1–5/22）、E8（Task 10/11/22）。設計文件的「前端 governance 設定三欄位」在 Task 22 Step 1；「批次結果一鍵重啟」在 Task 20 Step 4。無遺漏。
2. **已知風險與緩解**：
   - `audit_service.log_action` 的 `action` 型別（str vs `AuditAction` enum）——多個 Task 已標註實作前確認。
   - `__request` 的 URL 前綴與 multipart 支援——Task 19/20 已標註以既有檔案為準。
   - `GovernanceConfigUpdate` 為 partial update（`exclude_unset`），新欄位不影響既有 PUT。
   - Task 5 的 create() 前置測試對函式內部順序敏感——測試備註已寫明放置位置。
3. **型別一致性**：`check_quota` 關鍵字參數（`delta_cores/delta_memory_mb/delta_disk_gb/delta_instances`）在 Task 3/5/15 一致；`INIT_SNAPSHOT_NAME = "skylab-init"` 在 reset_service 與 snapshot_service 各自定義同值常數（避免循環 import，Task 9 有註明）；`SessionMode.pair` 字串 `"pair"` 前後端一致；task 狀態字串（`ok/needs_restart/quota_exceeded/error`）在 Task 15 service 與 Task 20 前端對映一致。









