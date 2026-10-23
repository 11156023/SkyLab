# LiteLLM 部署

`vllm-service/litellm/` 放的是 SkyLab 對外 AI API 背後的 LiteLLM 閘道。根目錄的
`docker-compose.yml` 會 include 這裡的 `docker-compose.yml`，它也能單獨執行
（獨立模式）。兩種模式用同一份設定與同一個主機 port 4000，同一時間只能跑其中一個。

本頁是這個目錄的簡要參考。完整的設定步驟、遠端路由、資料庫與金鑰操作、接管獨立
閘道以及使用者 API 範例，請見 [AI API 使用手冊](/zh-TW/guides/ai-api-user-manual)。

## 檔案與機密邊界

```text
vllm-service/litellm/
├── docker-compose.yml       # 由根目錄 Compose include；也是獨立模式的入口
├── config.template.yaml     # 納入版控的靜態路由政策
├── config.yaml              # 由 ../models.json 產生（Git 忽略，以 0600 權限寫入）
├── .env.example             # .env 的範本
└── .env                     # master、上游、資料庫、salt 與遠端金鑰（Git 忽略）
```

`config.yaml` 要留在這個目錄。模型連線改 `../models.json`，共用政策改範本；手動
修改產生出來的設定檔會被覆蓋。根目錄 `.env` 放的是 `AI_API_*` 與
`LITELLM_RUNTIME_*`，用的是受限的 LiteLLM service key。絕不要把 LiteLLM master key
或上游金鑰交給 backend，也絕不要把 SkyLab 的 service key 注入 LiteLLM 容器。

## 整合部署

在 repo 根目錄執行 `bash scripts/prepare-ai-stack.sh --init-env` 補齊缺少的密鑰，
再執行 `bash scripts/prepare-ai-stack.sh --start`：它會檢查設定、產生 `config.yaml`、
建立 LiteLLM 資料庫、註冊 SkyLab service key，然後啟動整個 stack。改了模型路由之後
再跑一次 `--start`。完整流程見 AI API 使用手冊的
[正式啟動](/zh-TW/guides/ai-api-user-manual#_4-正式啟動與既有獨立-gateway-接管)。

## 網路

閘道加入根目錄的 `skylab` 網路：backend／worker 呼叫 `http://litellm:4000`，LiteLLM
則以 `db:5432` 直接連 PostgreSQL（不經 PgBouncer）。port 4000 只發布在 `127.0.0.1`，
供健康檢查、核發金鑰與管理工具使用（管理介面請走 SSH tunnel）。它不經 nginx 轉發；
使用者只能透過 SkyLab 的 `/api/v1/ai-proxy` 使用模型。本機推論引擎
（`deployment: local`）以 `host.docker.internal:<port>` 連線，所以 `.env.API` 必須設
`API_HOST=0.0.0.0`，並用防火牆限制推論引擎的 port。

## 獨立部署

在這個目錄、準備好 `.env` 與產生出來的 `config.yaml` 之後：

```bash
docker compose up -d
docker compose ps
docker compose stop litellm
```

交接時先停掉舊專案的閘道，再啟動另一個專案的閘道。停止容器或換 Compose 專案都不會
搬移或刪除外部資料庫。請保留原本的 `DATABASE_URL` 與 `LITELLM_SALT_KEY`。獨立模式
沒有 `db` 服務，所以它的 `DATABASE_URL` 必須指向外部連得到的 PostgreSQL 主機。

## 健康檢查

`/health/liveliness` 是容器的健康檢查。`/health/readiness` 回報閘道是否就緒、資料庫
是否已連線。需要驗證的 `/health` 回傳的是 LiteLLM 背景模型健康檢查的結果，這個背景
檢查每 60 秒會真的對上游模型發出請求（範本中的 `health_check_interval`）。

部署之後可以用 `scripts/verify-litellm-staging.sh`（需要 `jq`，並先 export
`LITELLM_MASTER_KEY`）檢查 liveliness 與 readiness、公開模型清單是否與 `models.json`
一致、每個部署是否健康，以及 backend 是否連得到 LiteLLM；見
[維運驗證](/zh-TW/guides/ai-api-user-manual#維運驗證)。
