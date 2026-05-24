# PVE Resource Simulator

獨立的 FastAPI 原型工具，用來離線測試 Proxmox VE 風格的資源放置策略。提供 Web UI 讓使用者定義叢集 / 工作負載，並逐步展示放置決定與分數依據；可選用 Proxmox API 拉取月度歷史資料以校準策略。

## 適用情境

- 容量規劃：「再加入 N 台 VM，叢集還放得下嗎？」
- 策略調校：嘗試不同 CPU 超分配比例 / RAM 安全水位 / 評分權重
- 離線重播：用歷史資料驗證放置策略是否合理

> 與 [`ai-pve-placement-advisor`](../ai-pve-placement-advisor/README.md) 不同：本服務是**離線啟發式模擬器**，可手動調整所有常數，並有完整 24 小時模擬流程；advisor 則作為線上 advisory 服務串接 SkyLab。

## 功能總覽

- 自由定義任意數量的伺服器與其 CPU / RAM / Disk / GPU 容量
- 為每台伺服器設定既有占用
- 可加入多個 VM template；以 `count` 欄位模擬大量同型 VM
- 自動放置直到沒有任何 enabled template 可放
- 以「**加權主導占比 + 衝突懲罰**」選擇目標伺服器
- 允許 CPU 超分配，同時保留 RAM 安全水位
- 若有月度分析，使用同型同小時的加權平均與 P95 peak 校準有效需求
- 匯出 historical `average` / `trend` / `peak` 訊號
- 直接放不下時，先嘗試最多 2 步本地 rebalance
- 透過 `static/` UI 視覺化每一個放置步驟
- `monthly-analytics.html` 連線到 Proxmox 顯示歷史 utilization

## 目錄結構

```
pve_ resource_simulator/
├── main.py                       # uvicorn 入口（127.0.0.1:8012）
├── app/
│   ├── main.py                   # FastAPI app + 靜態檔
│   ├── api/routes.py             # /simulate /placement-step /analytics/monthly
│   ├── services/
│   │   ├── simulator_service.py        # 放置邏輯 / 24h 模擬 / rebalance
│   │   └── proxmox_analytics_service.py# 月度分析資料拉取
│   ├── schemas.py
│   └── static/
│       ├── index.html                # 模擬器 UI
│       └── monthly-analytics.html    # 月度分析 dashboard
├── docs/allocation-logic.md      # 放置演算法說明
├── tests/
│   ├── test_simulator.py
│   ├── test_proxmox_analytics.py
│   └── conftest.py
├── requirements.txt
└── .env.example
```

## 放置模型

模擬器將歷史訊號分為三層：

- `average_*`：月度加權平均使用率
- `trend_*`：以時間順序加權樣本算出的 EWMA 趨勢
- `peak_*`：加權 P95 高峰使用率

### 有效需求

針對 CPU / RAM 取「同小時 hourly、profile trend、profile average」三者的最大值作為基準率，再以保守 margin 計算：

| 資源 | margin | 下限 | 上限 |
| --- | --- | --- | --- |
| CPU | 1.4 | 35% × requested | requested |
| RAM | 1.15 | 50% × requested | requested |
| Disk / GPU | — | 直接使用 requested | — |

### 硬性 Fit Gate（必須先通過）

- CPU：`used + requested ≤ total × CPU_OVERCOMMIT_RATIO`（預設 2.0）
- RAM：`used + requested ≤ total × RAM_USABLE_RATIO`（預設 0.9）
- Disk：硬上限
- GPU：硬上限

### 評分

```
weighted_dominant_share =
  max(
    cpu_share * 1.0,
    ram_share * 1.2,
    disk_share * 1.5,
    gpu_share * 3.0,
  )
```

額外懲罰：

- CPU contention（physical CPU share > 0.7）
- Disk contention（disk share > 0.75）
- RAM 超出可用水位 → 重懲（5×）
- 主機 loadavg 過高 → 軟懲

Tie-breaker：average weighted share → physical CPU share → 已放置數 → server name。

### Rebalance

`allow_rebalance=true` 且沒有節點直接 fit 時，會搜尋最多 `LOCAL_REBALANCE_MAX_MOVES = 2` 個本地搬移；對 rebalance 目標套用 `MIGRATION_COST = 0.15` 懲罰。

### Peak 風險

放置完成後，再用 peak 訊號計算 `peak_cpu_risk` / `peak_ram_risk`。若超過閾值會在輸出標示高 peak 風險。

> 完整數學與常數請見 [`docs/allocation-logic.md`](docs/allocation-logic.md)。

## 參數調校建議

- 結構性常數（`EPSILON`、margin、floor、`LOCAL_REBALANCE_MAX_MOVES`、`MIGRATION_COST`）請保持穩定
- 真正值得校準的是反映校園運營政策的常數：`CPU_OVERCOMMIT_RATIO`、`RAM_USABLE_RATIO`、`*_SAFE_SHARE`、loadavg 閾值、`*_WEIGHT`
- 若未來想做自動最佳化，建議先用歷史資料離線重播，再考慮自動搜尋

## 啟動

```bash
cd "pve_ resource_simulator"
pip install -r requirements.txt
python main.py
```

開啟：

- 模擬器 UI：http://127.0.0.1:8012/
- 月度分析：http://127.0.0.1:8012/monthly-analytics
- Swagger：http://127.0.0.1:8012/docs

放置邏輯文件：[`docs/allocation-logic.md`](docs/allocation-logic.md)

## 環境變數（可選）

僅 monthly analytics 頁面需要 Proxmox 連線；可從 process env、repo 根目錄 `.env`，或 `pve_ resource_simulator/.env` 讀取，命名與 backend 一致：

```env
PROXMOX_HOST=192.168.100.2
PROXMOX_USER=ccapiuser@pve
PROXMOX_PASSWORD=...
PROXMOX_VERIFY_SSL=false
PROXMOX_API_TIMEOUT=20
PROXMOX_ISO_STORAGE=ISO
PROXMOX_DATA_STORAGE=data-ssd-2
PVE_ANALYTICS_TIMEZONE=Asia/Taipei
```

## 測試

```bash
cd "pve_ resource_simulator"
pytest
```

主要測試：

- `tests/test_simulator.py` — 放置 / score / rebalance 邏輯
- `tests/test_proxmox_analytics.py` — Proxmox 分析資料整合
