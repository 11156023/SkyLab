# AI PVE Placement Advisor

獨立的 FastAPI 服務，為 Proxmox VE（PVE）叢集提供「**規則 + 加權評分 + 可解釋輸出**」的工作負載放置建議；可選用 vLLM 產生自然語言解釋。

## 功能總覽

- 從 Proxmox API 直接讀取節點與 VM/LXC 即時資源狀態
- 在 Proxmox 不可用時，回退到靜態 JSON snapshot
- 可選串接 SkyLab backend 取得近期 VM 申請流量訊號
- 計算節點安全餘裕（headroom）、Guest 密度、使用者壓力修正
- 對候選節點以加權分數排序，輸出可解釋的放置決定
- 提供事件（events）與建議（recommendations），標示風險與原因
- 內建輕量 metrics（請求量、錯誤率、延遲、來源失敗次數）
- 支援 vLLM 自然語言解釋（沒有 vLLM 時自動使用 fallback 規則式回答）
- 內附 `static/index.html` Dashboard

## 目錄結構

```
ai-pve-placement-advisor/
├── main.py                                # uvicorn 入口
├── app/
│   ├── main.py                            # FastAPI app + 靜態檔
│   ├── core/config.py                     # Pydantic Settings
│   ├── api/routes/                        # 路由
│   ├── services/
│   │   ├── analytics_service.py           # 主流程：聚合來源 + 產出 AnalysisResponse
│   │   ├── aggregation_service.py         # 容量計算 / bin-pack / 評分
│   │   ├── ai_explainer_service.py        # vLLM 解釋（含 fallback）
│   │   ├── proxmox_source_service.py      # Proxmox API client（proxmoxer）
│   │   ├── backend_source_service.py      # SkyLab backend 流量
│   │   ├── snapshot_source_service.py     # 靜態 snapshot
│   │   └── metrics_service.py             # 執行期 metrics
│   └── schemas/analytics.py
├── static/index.html                      # Dashboard
├── tests/
├── requirements.txt
├── IMPLEMENTATION_STATUS.md
└── .env.example
```

## API

預設前綴 `/api/v1`（亦保留無前綴版本相容用）。

| Method | Path | 說明 |
| --- | --- | --- |
| GET | `/health` | 健康檢查（含 Proxmox 模式、AI 啟用狀態、snapshot 計數） |
| GET | `/ui-config` | 前端設定（API base、vLLM 狀態） |
| GET | `/api/v1/analyze` | 完整叢集分析（聚合 / 特徵 / 事件 / 建議 / 放置） |
| POST | `/api/v1/placement/recommend` | 對指定工作負載輸出放置建議 |
| GET | `/api/v1/sources/preview` | 預覽各資料來源原始內容 |
| GET | `/api/v1/metrics` | 服務 metrics |
| GET | `/` | Dashboard `static/index.html` |

### Placement 輸入欄位

```jsonc
{
  "machine_name": "lab-vm-01",
  "resource_type": "vm",          // vm | lxc
  "cores": 4,
  "memory_mb": 8192,
  "disk_gb": 80,
  "gpu_required": 0,
  "instance_count": 1,
  "estimated_users_per_instance": 30
}
```

## 分析根據

1. **Proxmox 即時資料**：節點 CPU / RAM / Disk、狀態、uptime、GPU 映射、Guest 狀態
2. **靜態 Snapshot**（Proxmox 失效時）：`NODES_SNAPSHOT_JSON`、`TOKEN_USAGE_SNAPSHOT_JSON`、`GPU_METRICS_SNAPSHOT_JSON`
3. **SkyLab Backend 流量**（可選）：近期新增 / pending / 核准 VM 申請統計

## 判斷邏輯

1. **安全餘裕（Safe Headroom）**：保留 `PLACEMENT_HEADROOM_RATIO` 比例後再評估
2. **Running-only Guest 密度**：只計算 `running` VM/LXC，避免 `stopped` 造成誤判
3. **使用者壓力修正**：以 `SAFE_USERS_PER_CPU` / `SAFE_USERS_PER_GIB` 推導較保守的有效需求
4. **GPU 硬條件**：`gpu_required > 0` 時僅選擇 `gpu_count >= gpu_required` 的節點
5. **加權評分**：以下權重的線性組合排序候選節點
   - `PLACEMENT_WEIGHT_CPU`
   - `PLACEMENT_WEIGHT_MEMORY`
   - `PLACEMENT_WEIGHT_DISK`
   - `PLACEMENT_WEIGHT_GUEST`

事件類型範例：`high_cpu` / `high_memory` / `high_disk` / `guest_overload` / `partial_fit` / `placement_blocked` / `backend_pending_high`。

## 主要環境變數

完整清單請見 `app/core/config.py` 與 `.env.example`。

```env
HOST=0.0.0.0
PORT=8011
API_V1_STR=/api/v1

# Proxmox
USE_DIRECT_PROXMOX=true
PROXMOX_HOST=192.168.x.x
PROXMOX_USER=ccapiuser@pve
PROXMOX_PASSWORD=...
PROXMOX_VERIFY_SSL=false
PROXMOX_API_TIMEOUT=30

# SkyLab Backend（可選）
BACKEND_API_BASE_URL=
BACKEND_API_TOKEN=
BACKEND_TRAFFIC_WINDOW_MINUTES=60
BACKEND_TRAFFIC_SAMPLE_LIMIT=200
BACKEND_PENDING_HIGH_THRESHOLD=20

# Snapshot fallback
NODES_SNAPSHOT_JSON=
TOKEN_USAGE_SNAPSHOT_JSON=
GPU_METRICS_SNAPSHOT_JSON=
BACKEND_NODE_GPU_MAP=

# 評分閾值與權重
CPU_HIGH_THRESHOLD=0.85
MEMORY_HIGH_THRESHOLD=0.85
DISK_HIGH_THRESHOLD=0.85
GUEST_PRESSURE_THRESHOLD=0.85
GUEST_PER_CORE_LIMIT=2.0
SAFE_USERS_PER_CPU=35
SAFE_USERS_PER_GIB=20
PLACEMENT_HEADROOM_RATIO=0.10
PLACEMENT_WEIGHT_CPU=1.0
PLACEMENT_WEIGHT_MEMORY=1.2
PLACEMENT_WEIGHT_DISK=1.0
PLACEMENT_WEIGHT_GUEST=0.8

# vLLM（解釋用，可選）
VLLM_BASE_URL=
VLLM_API_KEY=
VLLM_MODEL_NAME=
VLLM_TIMEOUT=10

# 來源快取與重試
SOURCE_CACHE_TTL_SECONDS=20
SOURCE_RETRY_ATTEMPTS=3
SOURCE_RETRY_BACKOFF_SECONDS=0.3
```

## 啟動

```bash
cd ai-pve-placement-advisor
cp .env.example .env
pip install -r requirements.txt
python main.py
```

預設位址：

- 服務：http://localhost:8011
- Swagger：http://localhost:8011/docs
- Dashboard：http://localhost:8011/

## 效能與可靠性設計

- 來源資料 TTL 快取（`SOURCE_CACHE_TTL_SECONDS`）
- Proxmox / vLLM 重試與退避（`SOURCE_RETRY_*`）
- async 路由中阻塞 I/O 轉至 thread executor
- 內建請求數 / 錯誤數 / 延遲 / 來源失敗 metrics

## 命名說明

本服務原名 `ai-log-analytics`，目前已聚焦於 PVE 配置建議，因此改名為 `ai-pve-placement-advisor`。
