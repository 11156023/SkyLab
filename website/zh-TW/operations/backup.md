# 備份與還原

SkyLab 伺服器上要備份什麼、怎麼備、怎麼還原。機器本身在 SkyLab 裡備份（逐台機器備份到 Proxmox storage）；本頁講的是平台本身。

## 重要的項目

| 項目 | 位置 | 原因 |
| --- | --- | --- |
| PostgreSQL 資料庫 `app`（`POSTGRES_DB`） | Docker volume `<project>_app-db-data`（checkout 在 `/opt/skylab/app` 時是 `app_app-db-data`），服務 `db` | 使用者、申請單、機器、班級、課程、防火牆規則、設定、稽核日誌、加密的憑證。沒有它 SkyLab 對你的機器一無所知。 |
| `.env` | repo 根目錄 | `SECRET_KEY` 用來解密資料庫裡的憑證。用不同的 key 還原備份等於沒用。 |
| LiteLLM 資料庫 `litellm` 與 `vllm-service/litellm/.env` | 同一個 PostgreSQL；檔案在 compose include 旁 | AI API 金鑰與模型路由，有用 AI stack 時才需要。 |
| `vllm-service/models.json` | repo | `prepare-ai-stack.sh` 的模型路由輸入。 |
| 閘道 VM | 你提供的 VM | SkyLab 會依資料庫重寫 nginx 規則（「同步規則到 Gateway」）並重放 WireGuard peer；HTTPS 憑證與私鑰是你自己的，SkyLab 不保存。 |
| 監控 volume（`prometheus-data`、`grafana-data`、`loki-data`、`influxdb-data`） | Docker volume（帶同樣的專案前綴） | 選用。只有指標歷史；儀表板從 repo 佈建。 |

Redis 只放速率限制、已撤銷 token、佇列與快取，不需備份。

## 備份資料庫

透過 `db` 容器匯出，stack 運行中也可以。指令用單引號包起來，取的是容器自己的 `POSTGRES_USER`／`POSTGRES_DB`，不必先載入 `.env`：

```bash
cd /opt/skylab/app
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "skylab-$(date +%F).dump"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc litellm' > "litellm-$(date +%F).dump"   # 只有用 AI stack 時
```

`-Fc` 產生壓縮的自訂格式，`pg_restore` 可以選擇性重放。LiteLLM 資料庫名稱以 `vllm-service/litellm/.env` 的 `DATABASE_URL` 為準（預設 `litellm`）。把匯出檔連同 `.env` 與 `vllm-service/litellm/.env` 複製到伺服器以外的地方，而且 `.env` 要和匯出檔一樣小心保管：它裡面有金鑰。

校園部署用主機上的每日 cron 就夠了：

```cron
30 3 * * * cd /opt/skylab/app && docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > /var/backups/skylab/skylab-$(date +\%F).dump
```

或者停掉 stack 直接複製 volume；volume 複製只有在資料庫停止時才一致。volume 名稱帶有 Compose 專案前綴，先用 `docker volume ls` 查出來：

```bash
docker run --rm -v app_app-db-data:/data -v "$PWD":/out alpine tar czf /out/app-db-data.tgz -C /data .
```

## 還原資料庫

1. 還原 `.env`（同一把 `SECRET_KEY`）與 repo checkout，版本要等於或新於匯出時的 commit。
2. 只啟動資料庫：

   ```bash
   docker compose up -d db
   ```

3. 替換資料庫內容：

   ```bash
   docker compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
   docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' < skylab-2026-10-18.dump
   ```

4. 有用 AI stack 的話，`litellm` 資料庫也要還原。它必須維持由自己的 role 擁有，所以先用 `DATABASE_URL` 裡的密碼（URL 解碼後）重建空資料庫與 role，還原時**不要**加 `--no-owner`。`DATABASE_URL` 用的名稱不是 `litellm` 時，腳本要加 `--database`／`--role`：

   ```bash
   docker compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" --if-exists litellm'
   LITELLM_DB_PASSWORD='<DATABASE_URL 裡的密碼>' bash scripts/init-litellm-db.sh
   docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d litellm' < litellm-2026-10-18.dump
   ```

5. 啟動其餘服務（有 AI stack 時改跑 `bash scripts/prepare-ai-stack.sh --start`，取代第一行指令）。`prestart` 會套用比匯出檔更新的 migration，然後後端才起來：

   ```bash
   docker compose up -d --scale litellm=0
   docker compose logs -f prestart
   ```

6. 登入後開「PVE 管理」，對每條連線按**測試**。PVE 明明連得到、測試卻全部失敗時，到 `docker compose logs backend` 找密碼解密錯誤：那代表 `.env` 的 `SECRET_KEY` 不是匯出時加密用的那把。

## 還原之後

- Proxmox 上的機器不受平台還原影響，但匯出到還原之間建立或刪除的機器會不同步：匯出檔裡有、PVE 上已經不存在的機器會顯示為狀態不明；匯出之後才建好的機器（包括匯出後核准、因此遺失的申請單建出的機器）在 SkyLab 裡沒有紀錄。這兩種管理員都能從資源管理頁刪除。
- 只要 `SECRET_KEY` 沒變，既有登入照常有效；匯出之後改過密碼或被強制登出的帳號例外，要用匯出當時的密碼重新登入。
- 到「閘道 VM」頁的「安裝服務」分頁按「同步規則到 Gateway」，讓 nginx 規則和還原後的資料庫一致。WireGuard peer 由後端的 reconciler 重放到閘道（預設每 60 秒一輪）。

## 機器備份

逐台機器的備份從機器詳情頁建立，存在管理員替每條 PVE 連線指定的備份 storage（沒指定就不提供這個功能）。它們是帶 SkyLab 標記的 Proxmox `vzdump` 封存檔，會隨機器刪除一起清掉。快照則留在機器自己的磁碟 storage 上。兩者都不屬於平台備份；請把 Proxmox 備份 storage 納入你的 Proxmox 備份計畫。

## 災難復原檢查清單

1. 新伺服器裝好 Docker，主機名稱相同或更新 `FRONTEND_HOST`。
2. clone repo，還原 `.env` 與 LiteLLM 檔案。
3. 還原資料庫（見上）、啟動 stack、檢查 `GET /api/v1/utils/health-check/ready`。
4. 測試每條 PVE 連線並同步。
5. 把規則同步到閘道，確認平台入口的 HTTPS 正常。
6. 有用模型的話重跑 AI stack 輔助腳本。

## 相關

- [升級與 migration](/zh-TW/operations/upgrade)：升級前一律先匯出
- [部署指南](/zh-TW/guides/deployment#密鑰)：為什麼 `SECRET_KEY` 要跟著匯出檔走
- [機器詳情](/zh-TW/manual/resource-detail)：逐台機器的備份與還原
