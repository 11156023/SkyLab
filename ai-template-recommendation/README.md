# AI Template Recommendation

獨立的 FastAPI 服務，依使用者課程情境與既有 Proxmox 節點容量，由 vLLM 推薦最合適的 VM/LXC 模板與資源規格。

## 功能總覽

- 從 `frontend/src/json/`（或 `TEMPLATES_DIR` 指向的目錄）載入模板目錄
- 從 SkyLab backend 或內建 snapshot 取得即時節點容量
- 對話式蒐集使用者意圖（goal、課程情境、Budget、是否需要 GPU / 資料庫 / Windows / Web 等）
- 由 vLLM 產出推薦模板、機器拆分、CPU/RAM/Disk 與優先放置節點
- 後端正規化 AI 輸出：模板 slug 必須存在於目錄、規格不得低於模板預設
- 內附 `static/index.html` 操作介面

## 目錄結構

```
ai-template-recommendation/
├── main.py                                # uvicorn 入口
├── app/
│   ├── main.py                            # FastAPI app + 模板目錄載入
│   ├── core/config.py                     # Pydantic Settings
│   ├── api/routes/
│   │   ├── recommendation.py              # /chat /recommend
│   │   ├── resources.py                   # /resources/nodes
│   │   └── catalog.py                     # /catalog/preview
│   ├── services/
│   │   ├── recommendation_service.py      # 主流程：意圖萃取 → vLLM → 正規化
│   │   ├── prompt.py                      # System / User prompt 模板
│   │   ├── catalog_service.py             # 模板目錄載入與比對
│   │   ├── backend_nodes_service.py       # 從 backend / snapshot 取節點
│   │   ├── proxmox_templates_service.py   # 直接從 Proxmox 取模板列表
│   │   └── resource_options_service.py    # 預設可選 CPU/RAM/Disk/GPU 選項
│   └── schemas/
│       ├── recommendation.py
│       └── resource.py
├── static/index.html
├── requirements.txt
└── test_vllm_output.txt
```

## API

預設前綴 `/api/v1`，亦保留無前綴版本。

| Method | Path | 說明 |
| --- | --- | --- |
| POST | `/api/v1/chat` | 純對話（不觸發推薦），回傳 reply 與 token 用量 |
| POST | `/api/v1/recommend` | 從對話萃取意圖 + 載入節點 / 模板 → 產生推薦結果 |
| GET | `/api/v1/resources/nodes` | 取得目前可用的 Proxmox 節點 |
| GET | `/api/v1/catalog/preview` | 預覽已載入的模板目錄 |
| GET | `/health` | 健康檢查 |
| GET | `/ui-config` | 前端設定 |
| GET | `/` | 前端 UI |

## Schema 重點

- **`DeviceNode`**：節點名稱、CPU / RAM / GPU 容量與使用率
- **`ChatMessage` / `ChatRequest` / `ChatResponse`**：對話訊息與 token 用量
- **`ExtractedIntent`**：goal_summary / role / course_context / budget_mode + flags（need_gpu / need_database / need_windows / need_web）
- **`RecommendationRequest`**：完整推薦請求（含 intent + nodes + resource options）
- **`NodeSchema`**：節點狀態、CPU、RAM、uptime

## 主要流程

1. 前端送 `/recommend` 帶入對話訊息
2. `recommendation_service` 呼叫 vLLM 萃取使用者意圖
3. 載入即時節點資料（`backend_nodes_service` 或 snapshot）
4. 載入模板目錄與資源選項
5. 將 intent + 節點 + 模板 + 資源選項組成 prompt 再次呼叫 vLLM
6. 後端對 AI 輸出做正規化：
   - 模板 slug 必須存在於目錄
   - 部署型別與模板型別對齊
   - CPU / RAM / Disk 不得低於模板預設

## 主要環境變數

完整見 `app/core/config.py`：

```env
HOST=127.0.0.1
PORT=8010

# 模板目錄
TEMPLATES_DIR=../frontend/src/json

# Backend 整合
BACKEND_AUTH_EMAIL=
BACKEND_AUTH_PASSWORD=
USE_INTERNAL_NODES_API=true
NODES_SNAPSHOT_JSON=
BACKEND_NODE_GPU_MAP=

# Proxmox（用於模板列表）
PROXMOX_HOST=
PROXMOX_USER=
PROXMOX_PASSWORD=
PROXMOX_NODE=pve
PROXMOX_ISO_STORAGE=local
PROXMOX_VERIFY_SSL=false
PROXMOX_API_TIMEOUT=15

# vLLM
VLLM_BASE_URL=http://localhost:8000/v1
VLLM_API_KEY=
VLLM_MODEL_NAME=
VLLM_ENABLE_THINKING=false
VLLM_TIMEOUT=30

VLLM_TEMPERATURE=0.6
VLLM_CHAT_TEMPERATURE=0.9
VLLM_TOP_P=0.95
VLLM_TOP_K=20
VLLM_MIN_P=0.0
VLLM_MAX_TOKENS=1600
VLLM_CHAT_MAX_TOKENS=2048
VLLM_PRESENCE_PENALTY=0.0
VLLM_REPETITION_PENALTY=1.0
```

## 啟動

```bash
cd ai-template-recommendation
cp .env.example .env
pip install -r requirements.txt
python main.py
```

預設位址：

- 服務：http://127.0.0.1:8010
- Swagger：http://127.0.0.1:8010/docs
- 前端 UI：http://127.0.0.1:8010/

## 主要依賴

```
fastapi
uvicorn
httpx
pydantic
pydantic-settings
```

## 設計重點

- **AI 是規劃者**，但「可選用的模板」必須來自前端 catalog；後端僅做驗證與正規化
- **節點資料**來自 backend API / snapshot，AI 看到的容量與正式系統一致
- **規格 Floor**：AI 提案 CPU / RAM / Disk 不會低於模板 `install_methods[].resources` 的預設值
