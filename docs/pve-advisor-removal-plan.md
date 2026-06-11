# PVE Advisor 移除與 Placement 服務拆分方案

## 目標

移除目前的 `ai/pve_advisor` AI 建議入口與 `/api/v1/ai/pve-advisor/recommend` 路由，同時保留正常 VM 申請、預約模式、立即模式、GPU 節點判斷、placement 檢查所需的共用能力。

目前不能直接刪除整個 `backend/app/ai/pve_advisor`，因為正常申請流程仍大量引用其中的 placement schema 與資源判斷函式。建議先把共用 placement service 搬出 AI namespace，再刪 AI route 與 advisor 專用程式碼。

## 現況依賴

### 可以視為 AI Advisor 專用

- `backend/app/api/routes/ai_pve_advisor.py`
  - 提供 `/ai/pve-advisor/recommend`
  - 直接呼叫 `generate_recommendation`
- `backend/app/infrastructure/ai/pve_advisor.py`
  - VLLM/OpenAI-compatible client
- `backend/app/ai/pve_advisor/prompt.py`
  - AI prompt template
- `backend/app/ai/pve_advisor/recommendation_service.py`
  - `generate_recommendation`
  - `_generate_ai_decision`
  - `_build_response_from_plan`
  - `_build_ai_plan_from_decision`
  - `_build_machine_recommendations`
  - `_build_current_status`
  - `_build_reply_from_plan`
  - `_load_backend_traffic_snapshot`
  - `_load_audit_signal_snapshot`
- `backend/app/ai/pve_advisor/schemas.py`
  - `AiMetrics`
  - `RecommendedMachine`
  - `MachineCurrentStatus`
  - `PlacementAdvisorResponse`
- `backend/tests/api/routes/test_ai_pve_advisor.py`
  - AI advisor route 與 recommendation 測試

### 不能直接刪，需先搬給 Placement 使用

目前正常 VM request、availability、provisioning 仍依賴 `app.ai.pve_advisor`：

- `backend/app/services/vm/vm_request_availability_service.py`
- `backend/app/services/vm/placement_support.py`
- `backend/app/services/vm/placement_service.py`
- `backend/app/services/proxmox/provisioning_service.py`
- `backend/app/domain/placement/storage.py`
- `backend/app/domain/placement/scorer.py`
- `backend/tests/test_backend_workflows.py`
- `backend/tests/api/routes/test_vm_request_availability.py`

共用 schema：

- `ResourceType`
- `PlacementRequest`
- `NodeSnapshot`
- `ResourceSnapshot`
- `NodeCapacity`
- `PlacementDecision`
- `PlacementPlan`

共用函式：

- `_load_cluster_state`
- `_build_node_capacities`
- `_build_rule_based_plan`
- `_build_warnings`
- `_build_rationale`
- `_build_summary_text`
- `_decide_resource_type`
- `_resource_type_summary`
- `_resource_type_reason_from_choice`
- `_request_label`
- `_choose_node`
- `_fit_count`
- `_weighted_headroom_score`
- `_can_fit`
- `_effective_cpu_cores`
- `_effective_memory_bytes`
- `_guest_soft_limit`
- `_guest_pressure_ratio`
- `_raw_available_cpu`
- `_raw_available_bytes`
- `_safe_available_float`
- `_safe_available_int`
- `_ratio`
- `_optional_int`
- `_parse_loadavg_1`

## 建議新結構

把 placement 能力搬到非 AI namespace，讓一般申請與審核流程不再依賴 AI package。

```text
backend/app/domain/placement/
  schemas.py          # ResourceType, PlacementRequest, NodeCapacity, PlacementPlan...
  capacity.py         # Proxmox cluster state, node capacity, cache, GPU map
  rules.py            # can_fit, effective cpu/memory, resource type decision
  planner.py          # rule-based placement plan, warnings, rationale, summary
  scorer.py           # existing scorer, 改引用 domain placement schema
  storage.py          # existing storage, 改引用 domain placement schema
```

或若要維持目前 service 分層，也可以放在：

```text
backend/app/services/vm/placement_capacity.py
backend/app/services/vm/placement_rules.py
backend/app/services/vm/placement_planner.py
```

我會比較建議放在 `domain/placement`，因為這些 schema 與規則已經不只是一個 VM service helper，而是跨 availability、provisioning、review 都會用到的 placement domain。

## 設定拆分

目前 `backend/app/ai/pve_advisor/config.py` 混合了兩種設定：

- AI/VLLM 設定：應隨 AI advisor 移除
- placement 設定：應保留並搬到 placement config

建議保留的 placement 設定：

- `source_cache_ttl_seconds`
- `backend_node_gpu_map`
- `guest_pressure_threshold`
- `guest_per_core_limit`
- `cpu_headroom_weight`
- `memory_headroom_weight`
- `load_headroom_weight`
- `guest_headroom_weight`
- `disk_headroom_weight`

建議移除的 AI/VLLM 設定：

- `VLLM_BASE_URL`
- `VLLM_API_KEY`
- `VLLM_MODEL_NAME`
- `VLLM_TEMPERATURE`
- `VLLM_MAX_TOKENS`
- `VLLM_TIMEOUT_SECONDS`
- advisor prompt/model 相關設定

`backend/config/system-ai.json` 目前有 `pve_advisor` section。建議分兩階段處理：

1. 先新增 `placement` section，並讓程式優先讀 `placement`。
2. 暫時保留 `pve_advisor` 作為 fallback，避免部署環境尚未更新設定時 placement 失效。
3. 一個版本後再移除 `pve_advisor` fallback 與 example config。

## 執行步驟

### Phase 1：搬 schema，保留相容 re-export

1. 新增 `backend/app/domain/placement/schemas.py`。
2. 將共用 schema 搬入新檔。
3. 暫時在 `backend/app/ai/pve_advisor/schemas.py` re-export 共用 schema。
4. 先更新 domain 與 service import，讓新程式碼不再從 `app.ai.pve_advisor.schemas` 取 placement schema。

驗證：

```powershell
rg "from app\.ai\.pve_advisor\.schemas" backend
```

### Phase 2：搬 capacity/rules/planner

1. 將 `_load_cluster_state`、`_build_node_capacities` 與 cache 搬到 `domain/placement/capacity.py`。
2. 將 `_decide_resource_type`、`_can_fit`、`_effective_cpu_cores`、`_effective_memory_bytes` 搬到 `domain/placement/rules.py`。
3. 將 `_build_rule_based_plan`、`_choose_node`、summary/rationale/warnings 搬到 `domain/placement/planner.py`。
4. 將 `advisor_service` alias 改成清楚的 placement module，例如：
   - `placement_capacity`
   - `placement_rules`
   - `placement_planner`

驗證：

```powershell
rg "advisor_service|app\.ai\.pve_advisor\.recommendation_service" backend
```

### Phase 3：更新正常申請與審核流程

更新以下檔案的 import 與 monkeypatch path：

- `backend/app/services/vm/vm_request_availability_service.py`
- `backend/app/services/vm/placement_support.py`
- `backend/app/services/vm/placement_service.py`
- `backend/app/services/proxmox/provisioning_service.py`
- `backend/tests/test_backend_workflows.py`
- `backend/tests/api/routes/test_vm_request_availability.py`

這一步完成後，一般申請、管理者自動核准、GPU availability、預約模式與立即模式都不應再依賴 AI advisor。

### Phase 4：移除 AI route

1. 從 `backend/app/api/main.py` 移除 `ai_pve_advisor` import。
2. 從 `backend/app/api/main.py` 移除 `api_router.include_router(ai_pve_advisor.router)`。
3. 刪除 `backend/app/api/routes/ai_pve_advisor.py`。
4. 刪除或改寫 `backend/tests/api/routes/test_ai_pve_advisor.py`。
5. 若有 OpenAPI client 產生流程，重新產生 client，移除舊 frontend client 裡的 `/api/v1/ai/pve-advisor/recommend`。

驗證：

```powershell
rg "ai_pve_advisor|pve-advisor" backend frontend frontend_new
```

### Phase 5：刪除 AI advisor 殘留

在確定沒有引用後，再刪除：

- `backend/app/infrastructure/ai/pve_advisor.py`
- `backend/app/ai/pve_advisor/prompt.py`
- `backend/app/ai/pve_advisor/recommendation_service.py` 內的 AI-only function
- `PlacementAdvisorResponse` 等 AI-only response schema
- VLLM advisor 設定

最後可視情況刪除整個 `backend/app/ai/pve_advisor` package；前提是：

```powershell
rg "app\.ai\.pve_advisor|pve_advisor|pve-advisor" backend frontend frontend_new
```

沒有任何仍需保留的 runtime 引用。

## 需要特別注意的風險

- 不能一次刪 `backend/app/ai/pve_advisor`，否則正常申請與 provisioning 會斷。
- 測試中有多個 monkeypatch 指向 `advisor_service._load_cluster_state`，搬移後要同步更新。
- `backend_node_gpu_map` 目前雖然放在 AI config，但實際上是 placement/GPU 可用性判斷需要的設定，不能跟 AI route 一起刪。
- 舊版 `frontend/src/client` 仍有 generated AI advisor endpoint；`frontend_new` 目前沒有直接呼叫，但若 OpenAPI schema 變更，client 最好同步更新。
- GPU 申請被拒絕或顯示無節點時，需確認新 placement module 使用的是實際 Proxmox node/GPU mapping，而不是被移除設定牽連。

## 建議驗證清單

後端：

```powershell
cd backend
pytest tests/api/routes/test_vm_request_availability.py tests/test_backend_workflows.py
```

全域搜尋：

```powershell
rg "advisor_service|app\.ai\.pve_advisor|ai_pve_advisor|pve-advisor" backend frontend frontend_new
```

前端若有重新產生 client 或碰到 `frontend_new`：

```powershell
cd frontend_new
npm run build
```

## 建議結論

可以移除 `ai/pve_advisor` 的 AI route 與 VLLM advisor 能力，但不能直接刪掉整個 package。

正確順序是：

1. 先把 placement schema、capacity、rules、planner 搬到 `domain/placement`。
2. 讓一般申請、審核、availability、provisioning 全部改用新 placement module。
3. 確認 `frontend_new` 不依賴 AI advisor endpoint。
4. 再刪 `/ai/pve-advisor/recommend` route、AI prompt、VLLM client、AI-only schema 與測試。

這樣可以達到「移除 AI advisor」的目標，同時保留一般 VM 申請與 GPU placement 的核心判斷。
