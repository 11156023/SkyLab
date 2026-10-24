# AI API 使用與部署手冊

本手冊適用於 SkyLab 主 Compose 整合 LiteLLM 的部署方式。一般使用者從 SkyLab
取得 `ccai_*` 金鑰；維運人員統一在專案根目錄管理 Docker，模型連線集中在
`vllm-service/models.json`。以下部署指令除特別標示外，均在 repository 根目錄執行。

## 1. 呼叫流程與檔案位置

```mermaid
flowchart LR
    U[使用者：ccai 金鑰] --> N[SkyLab nginx：8082]
    N --> B[Backend：驗證金鑰與限流]
    B -->|受限 service key| L[LiteLLM：Compose 內網 litellm:4000]
    L -->|本機 upstream key| V[本機 vLLM：8103／8104]
    L -->|各主機 upstream key| R[遠端 vLLM：IP 或網域]
    B -->|PgBouncer| C[(SkyLab DB)]
    L -->|直連 db:5432| D[(LiteLLM 專用 DB)]
```

LiteLLM 與 backend／worker 同在 Compose `skylab` 內網，backend 以 `http://litellm:4000` 呼叫；
LiteLLM 直連同一台 PostgreSQL 的專用資料庫（`db:5432`），不經 PgBouncer。主機上只有
`127.0.0.1:4000` 供健康檢查、金鑰核發與管理工具使用。LiteLLM 不接 nginx：使用者一律經
SkyLab `/api/v1/ai-proxy` 由 backend 驗證 `ccai_*` 金鑰與限流後轉送，LiteLLM 的管理 UI、
`/key/*` 與 health API 不對外公開；需要管理 UI 時從部署機本機或 SSH tunnel 連 `127.0.0.1:4000`。

| 檔案 | 用途 | 日常維護方式 |
| --- | --- | --- |
| `docker-compose.yml` | SkyLab 正式 Docker 入口；include 原 LiteLLM Compose | 根目錄 `docker compose` |
| `vllm-service/litellm/docker-compose.yml` | LiteLLM 服務定義；也保留獨立部署入口 | 修改一次，兩種啟動模式共用 |
| `.env` | SkyLab、backend/worker 到 LiteLLM 的 URL 與受限 service key | 保留現有值，不存 LiteLLM 管理／上游金鑰 |
| `vllm-service/litellm/.env` | LiteLLM master、salt、DB、各上游金鑰 | 僅注入 LiteLLM 容器 |
| `vllm-service/models.json` | 本機與遠端模型連線清單 | 新增／移除模型、調整 alias 或 IP |
| `vllm-service/litellm/config.template.yaml` | 共用 timeout、重試、健康檢查等政策 | 政策有變更才修改 |
| `vllm-service/litellm/config.yaml` | 由清單與 template 產生的 LiteLLM 路由 | 保留原位置；不要直接編輯 |
| `scripts/prepare-ai-stack.sh` | `--init-env` 補齊金鑰；預檢查、產生 production config；`--start` 自動建 DB、核發／同步 service key 並啟動 | 部署／改路由前執行 |

`config.yaml`、`models.json` 與實際 `.env` 均受 Git 忽略。範例與 template 可提交。
根目錄 `.dockerignore` 排除推論目錄與各層 `.env`，避免將模型權重／機密帶入 SkyLab build。
不將 LiteLLM `.env` 合併到主 `.env`：backend、worker、prestart 會讀取主 `.env`，
分開可讓高權限金鑰只進入 gateway。

## 2. 位址與金鑰怎麼填

主 `.env` 的 AI 區域：

```dotenv
AI_API_BASE_URL=http://litellm:4000
AI_API_API_KEY=<sk- 開頭的受限 service key；--init-env 自動產生>
LITELLM_RUNTIME_BASE_URL=http://litellm:4000
LITELLM_RUNTIME_API_KEY=<同一把受限 service key；--init-env 自動填入>
# 若原本已有這個欄位，須與 AI_API_API_KEY 一致；沒有可省略。
LITELLM_SERVICE_API_KEY=<同一把受限 service key>
AI_API_PUBLIC_BASE_URL=https://campus.example.edu
BACKEND_HOST_PORT=8000
REDIS_HOST_PORT=6379
```

`AI_API_PUBLIC_BASE_URL` 是使用者可以連線的 SkyLab 根網址（`.env.example` 填的是
`http://localhost:8082`）。API 完整 base URL 為該網址加上 `/api/v1/ai-proxy`。
如果 backend 直接跑在主機上，兩個 gateway URL 改用 `http://127.0.0.1:4000`；
主 Compose 模式一律用服務名稱 `litellm`（預檢查會擋 `127.0.0.1` 與舊的 `host.docker.internal`）。

`AI_API_API_KEY` 由部署端決定、LiteLLM 依它登記：`--start` 會用 master key 在 LiteLLM
以這個值建立 Virtual Key（別名 `campus-ai-api-service`），已存在時則把模型白名單同步成
`models.json` 目前的 alias。沿用既有 LiteLLM 資料庫時，把當初核發給 SkyLab 的那把 key 填進來即可。

LiteLLM `.env`：

```dotenv
LITELLM_MASTER_KEY=<gateway 管理金鑰；--init-env 自動產生>
VLLM_UPSTREAM_API_KEY=<推論主機 vLLM 的 API_KEY；本機模型與 .env.API 相同>
DATABASE_URL=postgresql://litellm:<已 URL 編碼的密碼>@db:5432/litellm
LITELLM_SALT_KEY=<第一次部署建立、後續固定保留的隨機金鑰；--init-env 自動產生>
# 使用 api_key_env 的遠端模型才需要；使用 apikeys 時不需要此變數。
REMOTE_LAB_API_KEY=<遠端 vLLM 的 API_KEY>
# 可選：主機端埠（只綁 127.0.0.1），供健康檢查與管理工具。
# LITELLM_HOST_PORT=4000
# 可選：填已驗證的 tag 或 digest，供可重現的升級／回滾。
# LITELLM_IMAGE=litellm/litellm:<tested-tag>
```

`--init-env` 只補缺少或仍為範例值（`replace-with-*`、`ai-api-secret-*`）的項目，既有真實值一律不動，
所以可以重複執行。上游 vLLM 金鑰無法產生，本機模型會從 `.env.API` 複製，遠端主機的 key 需手動填入。

| 金鑰 | 使用者／服務 | 授權範圍 |
| --- | --- | --- |
| `ccai_*` | 一般使用者 → SkyLab | SkyLab 核准的個人 API 存取 |
| `AI_API_API_KEY` | SkyLab → LiteLLM | LiteLLM Virtual Key 允許的模型 |
| `LITELLM_MASTER_KEY` | 維運人員 → LiteLLM | gateway 管理與 Virtual Key 核發 |
| `VLLM_UPSTREAM_API_KEY`、遠端 key | LiteLLM → 推論主機 | 各上游的模型 API |

不要互相替代這幾類金鑰。一般使用者取得的是 `ccai_*`，不需知道推論主機 IP 或服務金鑰。
`LITELLM_RUNTIME_API_KEY` 未設定時，管理端 runtime 觀測功能維持關閉。

### DATABASE_URL 與 LITELLM_SALT_KEY

`DATABASE_URL` 是資料庫連線字串，不是紀錄內容本身。LiteLLM 透過它保存 Virtual Key
設定／驗證資料、用量與花費紀錄、使用者／團隊設定，以及自己的 schema。用量紀錄
是否包含請求內容取決於 LiteLLM 記錄設定；不要假設它只保存 token 數。
資料庫須使用獨立 DB 與登入帳號，不可指向 SkyLab 的應用程式 DB，也不可對它執行 SkyLab Alembic。

`LITELLM_SALT_KEY` 用於 LiteLLM 保存部分敏感設定／上游憑證時的加解密，不是使用者 API key。
固定保留，與 DB 一起備份、一起還原；直接換掉會使既有加密資料無法解密。
它不會將所有日誌自動加密。詳見 [LiteLLM 加密說明](https://docs.litellm.ai/docs/proxy/security_encryption_faq)。

同機部署時 LiteLLM 與主 Compose PostgreSQL 在同一內網，URL 主機填 `db:5432`，與 `POSTGRES_HOST_PORT`
無關；不可填 `pgbouncer`（LiteLLM 的 Prisma 需要 session 語意），也不可填 `127.0.0.1`（容器內指向自己）。
主機為 `db` 時，`--start` 會以 URL 內的帳號、密碼、資料庫名稱自動建立專用角色與資料庫（已存在則把角色
密碼對齊 URL），並驗證該角色對 SkyLab 資料庫沒有建表權限；LiteLLM 啟動時自行跑 schema migration。
使用遠端 DB 時填該主機的 IP／網域及連線參數，保留既有 DB 與 salt 即可；外部 DB 由其管理者建立，腳本不會碰。
整合 Compose 不會自動搬移資料庫。

## 3. 本機與遠端模型清單

範例設定（`vllm-service/models.json.example`）定義兩個本機模型 `gemma-4-31b`（8103）與
`qwen3-14b`（8104），另有下方示範的兩個遠端項目；本手冊出現的模型名稱都是範例，實際對外名稱以
`models.json` 與 `/models` 回應為準。以下為欄位範例，請合併到現有
JSON 陣列，保留原有模型的 GPU、context、parser 等調校參數。

本機項目預設 `deployment` 為 `local`：

```json
{
  "alias": "gpt-oss-20B",
  "deployment": "local",
  "served_model_name": "gpt-oss-20B",
  "model_name": "./AImodels/gpt-oss-20B",
  "api_port": 8103,
  "gpu_memory_utilization": 0.4,
  "litellm": {"rpm": 10},
  "capabilities": {"chat": true}
}
```

遠端 vLLM 項目不需要 `model_name`、`api_port` 或本機 GPU 配置：

```json
{
  "alias": "remote-lab-chat",
  "deployment": "remote",
  "served_model_name": "lab-chat-model",
  "api_base": "http://192.0.2.20:8103/v1",
  "api_key_env": "REMOTE_LAB_API_KEY",
  "litellm": {"rpm": 10},
  "capabilities": {"chat": true}
}
```

若要讓某個遠端模型直接保存自己的 key，可改用 literal `apikeys`：

```json
{
  "alias": "remote-lab-private",
  "deployment": "remote",
  "served_model_name": "lab-private-model",
  "api_base": "http://192.0.2.21:8103/v1",
  "apikeys": "<該遠端模型的 API key>",
  "litellm": {"rpm": 10},
  "capabilities": {"chat": true}
}
```

`192.0.2.20` 是文件示例位址，須換成實際主機。遠端主機應先啟動模型，監聽 gateway
可達的介面，並允許 gateway 的來源連線；確認它的 `/v1/models` 確實提供
`lab-chat-model`。未指定 `apikeys` 或 `api_key_env` 時沿用 `VLLM_UPSTREAM_API_KEY`；
指定 `api_key_env` 時從 LiteLLM `.env` 讀取，指定 `apikeys` 時則直接寫入生成的
`litellm/config.yaml`。`apikeys` 與 `api_key_env` 不可同時設定。因 `apikeys` 是明文，
`models.json` 與生成後的 `config.yaml` 都不可提交或複製到不受信任的位置。產生器寫出
`config.yaml` 時一律設為 `0600`（只有擁有者可讀寫）；`models.json` 也請比照
（`chmod 600 vllm-service/models.json`）。

`alias` 是呼叫端 `model` 欄位使用的名稱，所有項目都必須唯一；本機
`served_model_name`、`api_port` 也要唯一。不同遠端主機可使用相同上游模型名稱／埠，
但公開 alias 要不同。`api_base` 須含 `/v1`，不可把帳密寫入 URL。
目前產生器使用 `hosted_vllm` provider，這個範例針對遠端 vLLM；雲端原生 provider
或不同協定需另外擴充產生器，填 IP 不會自動轉換協定。
`capabilities` 是描述資料，仍需模型及 vLLM parser 實際支援才能啟用工具、推理或多模態功能。

本機 launcher 會略過 `remote`，不消耗本機 GPU。全遠端部署不需跑 cluster launcher。
本機與遠端模型的路由都由 LiteLLM 管理。

## 4. 正式啟動與既有獨立 gateway 接管

需要 Linux Docker Engine、Docker Compose 2.20 以上，以及包含 `PyYAML`、
`python-dotenv` 的 Python。腳本優先使用 `vllm-service/.venv/bin/python`，
也可用 `AI_STACK_PYTHON=/path/to/python` 指定。
Compose 的相對掛載路徑以被 include 的檔案目錄解析，詳見
[Docker include 說明](https://docs.docker.com/reference/compose-file/include/)。

### 部署三步驟（全新或既有部署共用）

```bash
# 1. 補齊金鑰與位址：缺少或仍為範例值才寫入，既有真實值不動（可重複執行）
bash scripts/prepare-ai-stack.sh --init-env
# 2. 填入 --init-env 提示的上游金鑰（例如 DGX 的 VLLM_UPSTREAM_API_KEY），並備妥 models.json
# 3. 預檢查、產生 config、建 DB、啟動 LiteLLM、核發／同步 service key，最後啟動主 Compose
bash scripts/prepare-ai-stack.sh --start
```

主 `.env` 需先由 `.env.example` 建立並填好 SkyLab 必要參數；LiteLLM `.env` 不存在時
`--init-env` 會由範本建立。`--init-env` 會自動產生：

- LiteLLM `LITELLM_MASTER_KEY`（`sk-` 開頭）、`LITELLM_SALT_KEY`；
- `DATABASE_URL`（`litellm` 帳號、隨機密碼、`db:5432/litellm`）；
- 主 `.env` 的 `AI_API_API_KEY` 與 `LITELLM_RUNTIME_API_KEY`（同一把 `sk-` key），
  並把兩個 gateway URL 設為 `http://litellm:4000`。

`--start` 依序執行：

1. 核對 root／gateway 金鑰隔離、service key 一致性、local upstream key 與 `.env.API`
   一致性、DB 名稱／帳號隔離、必要遠端 key（不查詢上游；需要時另跑 `--check-only --check-upstreams`）；
2. 產生 production `config.yaml` 與 Prometheus 的 vLLM 抓取目標
   （`monitoring/prometheus/targets/vllm.json`）；
3. `DATABASE_URL` 主機為 `db` 時，啟動主 Compose PostgreSQL，等它接受 TCP 連線後
   建立（或對齊密碼）專用角色與資料庫；
4. 重建 LiteLLM 以載入新路由，等 `/health/readiness` 回報資料庫已連線（首次會先跑 migration）；
5. 以 master key 在 LiteLLM 登記 `AI_API_API_KEY`（別名 `campus-ai-api-service`），
   已存在則把模型白名單同步成 `models.json` 目前的 alias；
6. `docker compose up -d --build` 啟動主專案。

它不會自動停止其他專案的 gateway；若另有獨立 `campus-litellm` 在跑，腳本會取消啟動並保留既有服務，
先停它再重跑：

```bash
docker compose -f vllm-service/litellm/docker-compose.yml \
  --env-file vllm-service/litellm/.env stop litellm
bash scripts/prepare-ai-stack.sh --start
docker compose ps
curl -fsS http://127.0.0.1:4000/health/readiness
```

只驗證、不改檔：

```bash
bash scripts/prepare-ai-stack.sh --check-only --check-upstreams
```

本機模型若尚未執行，先 `bash vllm-service/start_multi_model_cluster.sh`；全遠端部署略過。
LiteLLM 在容器內，本機 vLLM 須監聽 Docker 可達的介面：`.env.API` 設 `API_HOST=0.0.0.0`，
並以防火牆限制 8103／8104 只供本機與 Docker 網段，預檢查會擋只綁 loopback 的設定。

### 沿用既有 LiteLLM 資料庫

保留原本的 `LITELLM_MASTER_KEY`、`LITELLM_SALT_KEY` 與 `DATABASE_URL`（外部主機填其 IP／網域，
腳本不會嘗試建立），並把當初核發給 SkyLab 的 service key 填入主 `.env` 的 `AI_API_API_KEY`、
`LITELLM_RUNTIME_API_KEY`。`--init-env` 看到真實值就不會改動；`--start` 只同步該 key 的模型白名單。

### 從舊版 host network 部署升級

舊設定的 `AI_API_BASE_URL=http://host.docker.internal:4000` 與
`DATABASE_URL=...@127.0.0.1:5433/...` 在內網架構下連不到。執行一次 `--init-env`，它會把 gateway URL
改成 `http://litellm:4000`、把資料庫主機改成 `db:5432`（帳號密碼不變），再 `--start`。

### 手動部署 workflow

workflow 本身（`.github/workflows/deploy-pve-test.yml`）見
[部署](/zh-TW/guides/deployment#_8-持續部署-測試環境)。AI stack 的部分：runner 需要
`/opt/skylab/.env`、`/opt/skylab/vllm-service/models.json`，有本機模型時還需要
`/opt/skylab/vllm-service/.env.API`。workflow 先對 `/opt/skylab` 執行 `--init-env`，讓產生的金鑰寫回
該處、跨次部署保留（缺少的 LiteLLM `.env` 由範本建立）；上游金鑰仍缺時在此步驟失敗。接著把檔案複製進
checkout 並執行 `--start`。runner 需能使用 `python3 -m venv`，首次補金鑰時還要對 `/opt/skylab` 有寫入權限。
GPU 模型程序請在部署主機上獨立管理，不要放在可能被 checkout 清除的 runner 工作目錄。

## 5. 修改連線、重啟與回滾

新增模型／改 IP：修改 `models.json`，在 LiteLLM `.env` 加入對應 key，再執行：

```bash
bash scripts/prepare-ai-stack.sh --start
```

`--start` 會重建 LiteLLM 載入新路由，並把 SkyLab service Virtual Key 的模型白名單同步成新的
alias 清單，不必另外呼叫 `/key/update`。只用 `docker compose up -d --force-recreate litellm`
重建時白名單不會同步，新模型對 SkyLab 使用者仍不可用。
改模型本體、GPU 或本機監聽埠時，也需要重啟本機推論 cluster。
改主 `.env` 後，用 `docker compose up -d --force-recreate backend worker` 讓容器讀到新值。
單純 `restart` 不會重新注入 `.env`。

| 操作 | 根目錄指令／影響 |
| --- | --- |
| 查看狀態 | `docker compose ps` |
| gateway 日誌 | `docker compose logs --tail 100 -f litellm` |
| 只停止 gateway | `docker compose stop litellm` |
| 重建 gateway | `docker compose up -d --force-recreate litellm` |
| 停止主 Docker stack | `docker compose down`，包含已接管的 LiteLLM |

**監控**：「資源監控 → 系統健康」會列出 AI Gateway（LiteLLM）與每個模型的狀態，模型的上游推論服務
（例如 DGX）連不到時發系統告警（治理設定開啟告警 Email 時另寄信給管理員）。有啟用監控 stack 時，Grafana「SkyLab AI」儀表板顯示
SkyLab 請求量／錯誤／延遲、LiteLLM 與 vLLM 引擎指標；vLLM 的抓取目標由 `--start` 依 `models.json`
自動產生，遠端主機防火牆要放行部署機連推論埠（與 LiteLLM 同一條規則）。細節見
[系統監控](/zh-TW/guides/monitoring#ai-模組監控)。

`down` 不停止主機 vLLM 程序，也不刪除外部 LiteLLM DB。不要使用 `down -v` 作為日常停止指令。
缺少 config 時 Compose 的 bind mount 會直接失敗，不會誤建 `config.yaml/` 目錄。

退回獨立部署：先在 root `docker compose stop litellm`，再從
`vllm-service/litellm/` 執行 `docker compose up -d`，salt 與 config 沿用同一份。獨立模式沒有 `db` 服務，
`DATABASE_URL` 必須改指向該容器連得到的 PostgreSQL 主機，不能再用 `db:5432`。
LiteLLM 是唯一的 AI API gateway，沒有其他回退入口。

## 6. 一般使用者申請與呼叫 API

使用者在 AI API 頁面申請金鑰；操作步驟、欄位與各身分可選的有效期限見
[AI API 頁面手冊](/zh-TW/manual/ai-api)。直接呼叫 API 時要注意：

- key 清單只回傳前綴；明文只提供給擁有者本人。
- 透過 API 申請時，學生必須明確送出 `duration`（預設值「永久」學生不能用）。有效期限從核准時起算。
- 核准時會依申請人目前的身分重新檢查期限，期限已不被允許的待審申請只能駁回後重新申請。
- 輪替後舊 key 立即失效，使用它的程式要同步更新。

管理申請的 `/api/v1/ai-api/*` 使用 SkyLab 登入 bearer token；下列推論端點使用 `ccai_*`。
兩者不是同一種認證。SDK 或相容客戶端的 `base_url` 設為
`https://campus.example.edu/api/v1/ai-proxy`，不再加一段 `/v1`。

| 方法／端點（相對 base URL） | 用途 |
| --- | --- |
| `GET /models` | 查詢目前 service key 可用的模型 ID |
| `POST /chat/completions` | messages 對話，可使用 SSE 串流 |
| `POST /completions` | prompt 文字生成，需上游支援 |
| `POST /responses` | Responses 格式，需上游支援 |
| `GET /rate-limit/status` | 這把 key 目前的額度；不消耗生成配額 |
| `GET /usage/my` | key 擁有者的用量統計（預設最近 30 天） |

SkyLab 不轉送 LiteLLM 的管理、key、DB 或 health API。embedding 等其他 endpoint
目前不在 SkyLab proxy 的公開範圍。

### curl 模型清單與對話

```bash
export CAMPUS_AI_BASE_URL='http://localhost:8082/api/v1/ai-proxy'
read -rsp 'SkyLab ccai key: ' CAMPUS_AI_API_KEY; echo
export CAMPUS_AI_API_KEY
curl --fail --silent --show-error \
  --config <(printf 'header = "Authorization: Bearer %s"\n' "$CAMPUS_AI_API_KEY") \
  "$CAMPUS_AI_BASE_URL/models"

curl --fail --silent --show-error \
  --config <(printf 'header = "Authorization: Bearer %s"\n' "$CAMPUS_AI_API_KEY") \
  -H 'Content-Type: application/json' \
  --data '{"model":"gpt-oss-20B","messages":[{"role":"user","content":"請用繁體中文簡述你的功能。"}],"max_tokens":512}' \
  "$CAMPUS_AI_BASE_URL/chat/completions"
```

把 `model` 換成 `/models` 回傳的 ID 可切換模型。串流時加 `"stream":true`，curl 加
`--no-buffer`，逐行接收 `data:` 事件。推理模型可能先耗用 reasoning tokens；若輸出
`content` 為空且 `finish_reason=length`，先增加 token 預算並核對模型 parser，不能僅據此判定連線故障。

Python 可直接透過 HTTP 呼叫，不依賴特定 SDK：

```python
import json
import os
from urllib.request import Request, urlopen

base = os.environ["CAMPUS_AI_BASE_URL"].rstrip("/")
request = Request(
    base + "/chat/completions",
    data=json.dumps({
        "model": "gpt-oss-20B",
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 512,
    }).encode(),
    headers={
        "Authorization": "Bearer " + os.environ["CAMPUS_AI_API_KEY"],
        "Content-Type": "application/json",
    },
)
with urlopen(request, timeout=120) as response:
    payload = json.load(response)
print(payload["choices"][0]["message"])
```

### 限流如何判讀

| 層級 | 程式預設／契約 | 實際含義 |
| --- | --- | --- |
| SkyLab 生成 | 每個核准申請 20 次／60 秒滑動視窗 | 金鑰的 `rate_limit` 優先，未設定才用全站預設（`AI_API_RATE_LIMIT_PER_MINUTE`）；所有模型與三種生成 endpoint 共用 |
| 模型清單 | 每個核准申請 30 次／60 秒 | `/models` 獨立額度，不消耗生成配額 |
| 限流狀態 | 不消耗生成配額 | `/rate-limit/status` 回報憑證當下額度；`disabled=true` 或 `error` 需分別判讀 |
| Backend admission | 每個 process 全域 active 20、每個模型 active 10、等待 40 | 併發與排隊限制，不是 RPM；等待／cooldown 預算 70 秒、整次請求 deadline 115 秒 |
| LiteLLM deployment | generator 的每個 deployment `rpm` 預設 10 | source of truth 是 `vllm-service/models.json` 的 `litellm.rpm`；生成設定只供 runtime 使用 |
| AI 導覽 | 每個登入帳號 30 次／60 秒 | `/resolve`、`/intake` 共用 `ai-navigation` scope；intake 不打模型，但也占額度 |
| AI PVE 助手 | chat route 沒有另外設定 RPM dependency | 不表示無限容量；仍受登入權限、System AI transport 與上游限制，不能套用公開 API 的 20 RPM |

生成配額 identity 是核准申請 `request_id`，同一申請輪替金鑰不會重置；不同模型也不會
各自獲得 20 次。一般與串流各占一次；限流檢查在 body／model 驗證前，所以部分無效請求
也會占用額度。上游重試由 relay 處理，同一 logical request 不再次消耗 SkyLab RPM。
滑動視窗不是整點重置；目前 status 的 `reset_at` 是查詢時間加上一個視窗，不是精確的
下一個 slot 釋放時間，需要剩餘值時請重新查詢。

`20/60`、`30/60` 與 LiteLLM `10 RPM` 是程式預設值，不是實測容量。憑證的實際上限以 status 回應為準。
status 不會揭露生成視窗秒數（`AI_API_RATE_LIMIT_WINDOW_SECONDS`）與 LiteLLM RPM，部署調整過時請向維運人員確認。
backend 多 process 的總併發相加，單次順序檢查不能證明容量。

### 維運驗證

```bash
bash scripts/prepare-ai-stack.sh --check-only --check-upstreams
curl -fsS http://127.0.0.1:4000/health/liveliness
curl -fsS http://127.0.0.1:4000/health/readiness
# 已啟動 SkyLab backend 時，使用管理 key 驗證 gateway 清單、上游與 backend 連線。
read -rsp 'LiteLLM master key: ' LITELLM_MASTER_KEY; echo
export LITELLM_MASTER_KEY
bash scripts/verify-litellm-staging.sh
unset LITELLM_MASTER_KEY

# 使用已核准的 SkyLab 測試 key 對公開 API 做煙霧測試。
export AI_API_SMOKE_KEY="$CAMPUS_AI_API_KEY"
AI_API_PUBLIC_BASE_URL=http://localhost:8082/api/v1 bash scripts/verify-ai-api-cutover.sh
unset AI_API_SMOKE_KEY
```

`verify-litellm-staging.sh` 需要 curl 與 jq，會核對 gateway 的模型清單是否等於 `models.json` 的 alias、
每個 deployment 是否健康，以及 backend 容器能否連到 gateway。`verify-ai-api-cutover.sh` 需要 curl 與
python3，會列出這把 key 看得到的模型，對每個模型送一次簡短的非串流 chat completion，因此會產生少量
模型用量（請用專用測試 key）。它不測串流、`/completions` 與 `/responses`。操作結束可 `unset CAMPUS_AI_API_KEY`。

## 7. 常見問題

| 現象 | 檢查與處理 |
| --- | --- |
| `4000` 已占用／有兩個 gateway | `docker ps` 核對 Compose project；停止舊 gateway 再接管；或設 `LITELLM_HOST_PORT` |
| backend 無法連線 gateway | `AI_API_BASE_URL` 應為 `http://litellm:4000`（舊的 `host.docker.internal` 已不適用，跑 `--init-env` 改寫） |
| 本機上游 401 | `.env.API` 的 `API_KEY` 要與 LiteLLM 上游 key 相同 |
| 本機上游連不到 | `.env.API` 的 `API_HOST` 須為 `0.0.0.0`（容器經 `host.docker.internal` 連入），並以防火牆限制引擎埠 |
| 遠端模型連線失敗 | IP／網域、監聽介面、防火牆、`/v1`、served model name、key 是否一致 |
| SkyLab 401／403 | 檢查 `ccai_*` 是否核准、過期、撤銷；service Virtual Key 是否有效／允許模型（`--start` 會重新登記並同步） |
| 模型清單少了新模型 | 用 `--start` 重新部署：會重產 config、重建 LiteLLM 並同步 service key 白名單 |
| `--start` 卡在 LiteLLM 就緒 | `docker compose logs litellm`；多半是 `DATABASE_URL` 帳密／主機錯誤或首次 migration 仍在跑 |
| key 核發回 HTTP 400 | 已有別把 key 用了別名 `campus-ai-api-service`：把那把 key 填進 `AI_API_API_KEY`，或在 LiteLLM 撤銷後重跑 |
| 429 | SkyLab 限流或 LiteLLM RPM 限制；依 `Retry-After` 延後重試 |
| 413／415 | 請求超過 `AI_API_MAX_REQUEST_BODY_BYTES`，或 Content-Type 不是 JSON |
| 502／503 | 檢查 gateway、上游與 DB 是否可用，再看 backend／LiteLLM 日誌 |
| DB 認證／migration 失敗 | 核對專用角色、密碼 URL 編碼、DB 主機與埠；不要改用 SkyLab DB |
| salt 更換後不能解密 | 還原原 salt 與對應 DB 備份，不能以新 salt 修復舊密文 |
| 修改 `.env` 沒生效 | 用 `up -d --force-recreate` 重建相關容器 |
| `8000` 或 `6379` 衝突 | 設 `BACKEND_HOST_PORT`／`REDIS_HOST_PORT`；容器間服務埠保持原值 |

LiteLLM 只在主機 `127.0.0.1:4000` 開埠，外部網路連不到；容器間一律走 Compose 內網。
使用者的正常入口是 SkyLab（nginx → backend），LiteLLM 不經 nginx 對外。完整 LiteLLM 功能與 key 管理請參考
[Virtual Keys](https://docs.litellm.ai/docs/proxy/virtual_keys) 與
[vLLM provider](https://docs.litellm.ai/docs/providers/vllm)。
