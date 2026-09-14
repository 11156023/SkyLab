# AI PVE Guest 診斷 Tools 與固定輸出格式計畫

| 項目 | 內容 |
| --- | --- |
| 日期 | 2026-09-08 |
| 狀態 | 分析與實作計畫，尚未修改功能 |
| 主要入口 | `POST /api/v1/ai/pve-log/chat` |
| 共用入口 | `POST /api/v1/ai/pve-template/chat` |

## 1. 結論

AI PVE 維運助手應保留既有 PVE 原子工具與確認式 `ssh_exec`，並新增一個主要的
Guest 廣度診斷工具：

```text
get_guest_diagnostic_summary(vmid)
```

此工具一次彙整：

1. PVE VM/LXC 資源與狀態。
2. systemd service 摘要與 failed services。
3. CPU Top 10 與 RAM Top 10 processes。
4. 最近一小時、最多 100 筆 warning/error journal。

工具只負責**收集、解析、遮蔽與截斷**，不自行判斷根因；LLM 根據結構化結果負責
跨 PVE、VM、OS、Service 與 Application 層進行說明。

管理員看到的最終回答必須使用本文件第 8 節的固定 Markdown 格式，以
`✅／⚠️／❌／❓` 標記狀態，避免長篇敘述與 raw output。

## 2. 目標與範圍

### 2.1 目標

- 管理員用一句「VM 105 現在怎麼了？」即可取得第一輪跨層診斷資料。
- 常見查詢原則上只需要一次彙總 tool call。
- 正常 service、完整 process list 與完整 journal 不進入 LLM context。
- 保留既有 VMID 授權、SSH command guard、timeout、redaction 與人工確認邊界。
- 單一資料來源失敗時回傳 `partial`，不把缺少資料解讀成正常。
- 回覆格式固定、短而可掃讀，並保留足以支持結論的證據。

### 2.2 本次不做

- 不建立背景監控、告警、排程掃描或歷史趨勢系統。
- 不執行 HTTP health check、DNS 檢查或外部網站監控。
- 不讀取 shell `history`；它不是系統 log，且可能包含敏感內容。
- 不讓模型傳入任意 command、host、SSH user、port 或 key 給彙總工具。
- 不新增資料庫表、Alembic migration、模板 CRUD、plugin framework 或 DSL。
- 不在第一版加入 `basic/full` 模式。
- 不在第一版同時實作所有深入工具。

## 3. 現況基準

目前 `backend/app/ai/pve_log/chat.py` 註冊的工具只有：

- `get_resources`
- `get_nodes`
- `get_storage`
- `get_resource_detail`
- `get_cluster`
- `ssh_exec`

`get_resource_detail` 由 `PveToolContext` 取得 PVE 層摘要、即時狀態、設定與網路介面；
它沒有 systemd service、process 或 journal 等 Guest OS 資料。

目前 Guest OS 資料只能透過 `ssh_exec`：

- 一般 AI PVE 管理員入口：AI 提出的 SSH 指令需要人工確認。
- AI PVE template 入口：只有 `command_policy.py` 內 server-owned known-read 指令可自動執行。
- 未知或自訂 shell 指令仍需人工確認；hard-deny 指令永遠攔截。

兩個入口共用同一個 `pve_log.chat` agent loop，因此新工具必須同時維持：

- 一般入口的 `AdminUser` 邊界。
- template 入口在 LLM 前完成的 VMID authorization。
- `allowed_vmids`、requester、scope 與 confirmation history 驗證。

## 4. 方案比較與決策

| 方案 | 優點 | 主要問題 | 決策 |
| --- | --- | --- | --- |
| 每條 Linux 指令各自成為 tool | 單一能力容易理解 | schema 與 tool round 增加，模型容易重複查詢 | 不採用 |
| 一個 tool 回傳所有 raw output | 實作直接 | service、process、journal 會塞滿 context，也增加敏感資訊風險 | 不採用 |
| 彙總工具 + 必要時深入 | 一次取得廣度、輸出可控、保留深入能力 | 需要穩定 parser 與 partial semantics | 採用 |

採用「原子工具保留 + 一個彙總工具」：

```text
PVE / VM tools
├── get_resource_detail                 既有，PVE 層
├── get_guest_diagnostic_summary        新增，第一輪廣度診斷
├── get_service_detail                  第二階段
├── get_recent_logs                     第二階段
└── ssh_exec                            既有，特殊工作的確認式 fallback
```

第一版暫緩 `get_process_detail(pid)`。PID 可能很快消失或重用，而彙總工具的 CPU/RAM
Top processes 已足以回答多數初步排查；特殊需求先使用確認式 `ssh_exec`。

## 5. Runtime 流程

```text
管理員問題
    ↓
LLM 判斷需要單機廣度排查
    ↓
get_guest_diagnostic_summary(vmid)
    ├── PVE：重用 PveToolContext.get_resource_detail
    └── Guest：server-owned read-only probes
          ├── service summary
          ├── failed services
          ├── CPU / RAM top processes
          └── bounded recent journal
    ↓
後端解析、遮蔽、限制筆數、組成固定 JSON
    ↓
LLM 依固定 Markdown 模板輸出分層結論
    ↓
有明確異常才呼叫深入工具；特殊需求走 ssh_exec + confirmation
```

### 5.1 執行順序

1. 驗證 `vmid` 型別與 `allowed_vmids`。
2. 呼叫 PVE `get_resource_detail`。
3. 若 PVE 明確顯示 VM/LXC 為 `stopped`，Guest sections 標記 `unavailable` 並停止 SSH。
4. 若 PVE 資料取得失敗但 VM 未被確認為 stopped，可嘗試使用已授權的 DB/IP/SSH 資訊收集
   Guest 資料；PVE section 維持 `error` 或 `partial`。
5. 使用一條 SSH connection 依序執行固定 probes。
6. 各 section 獨立解析；其中一項失敗不得清空其他成功結果。
7. 組成固定 JSON 後回傳 agent loop。

第一版對多 VM 的多個彙總 tool calls 維持循序執行，不共用 route DB Session 做平行 SSH，
也避免一次對多台 VM 形成連線突波。取得實際延遲數據後再評估最多三台並行。

## 6. Tool 輸入與固定 JSON 契約

### 6.1 輸入

第一版只接受 `vmid`：

```json
{
  "vmid": 105
}
```

模型不能指定：

- shell command
- host 或 IP
- SSH user、port 或 private key
- journal 無上限時間範圍
- process 或 service 無上限筆數

### 6.2 Section 狀態

每個 section 的 `collection_status` 只能是：

| 值 | 意義 |
| --- | --- |
| `ok` | 指令成功且資料完成解析 |
| `partial` | 有可用資料，但發生截斷或部分 probe 失敗 |
| `unavailable` | 目標狀態不允許收集，例如 VM stopped 或沒有 systemd |
| `error` | 嘗試收集但失敗，沒有可信資料 |

整體 `collection_status` 規則：

- 所有必要 sections 都是 `ok`：`ok`。
- 至少一項有資料、但另有 `partial/unavailable/error`：`partial`。
- 所有必要 sections 都沒有可信資料：`error`。

### 6.3 固定輸出

```json
{
  "vmid": 105,
  "collected_at": "2026-09-08T14:00:00Z",
  "collection_duration_ms": 1380,
  "collection_status": "partial",
  "resource": {
    "collection_status": "ok",
    "data": {}
  },
  "services": {
    "collection_status": "ok",
    "total": 83,
    "by_active_state": {
      "active": 68,
      "inactive": 13,
      "failed": 2
    },
    "by_sub_state": {
      "running": 47,
      "exited": 21,
      "failed": 2
    },
    "failed_services": [
      {
        "name": "student-app.service",
        "description": "Student Application"
      }
    ]
  },
  "processes": {
    "collection_status": "ok",
    "limit_per_list": 10,
    "top_cpu": [],
    "top_memory": []
  },
  "recent_logs": {
    "collection_status": "partial",
    "window_minutes": 60,
    "minimum_priority": "warning",
    "returned_entries": 100,
    "limit": 100,
    "may_be_truncated": true,
    "entries": []
  },
  "warnings": [
    "最近系統記錄已達 100 筆上限，可能還有未回傳項目。"
  ]
}
```

### 6.4 契約規則

- 欄位名稱與型別固定，不依 Linux 發行版任意改變。
- `warnings` 只放資料缺口、截斷或相容性說明，不放 AI 推論。
- 不加入 `healthy: true/false`；工具不負責健康判定。
- 不把 raw stdout/stderr 放進成功回應。
- probe 失敗時可保留精簡 `error_code` 與安全錯誤摘要，不回傳 secret、IP、key 或完整 stack。
- PVE disk 數值不等於 Guest filesystem `df` 使用率。第一版未取得 `df` 時，LLM 不得宣稱
  VM 內檔案系統空間正常。

## 7. 固定 Guest Probes

### 7.1 Service

```bash
systemctl list-units --type=service --all --no-legend --plain --no-pager
systemctl --failed --type=service --no-legend --plain --no-pager
```

後端只保留：

- service 總數
- active/sub-state 統計
- failed service 名稱與描述

完整正常 service 清單不得送入 LLM。

### 7.2 Process

```bash
ps -eo pid=,user=,comm=,%cpu=,%mem=,etimes=,args= --sort=-%cpu
ps -eo pid=,user=,comm=,%cpu=,%mem=,etimes=,args= --sort=-%mem
```

- CPU 與 RAM 分開排序，各保留 Top 10。
- 使用數字 `etimes`，避免 `etime` 格式隨運行時間改變。
- `args` 必須限制長度並遮蔽 `--password`、`--token`、`--api-key`、URI credential 等內容。
- PID、CPU、RAM 或 elapsed 解析失敗的單行應略過並留下 warning，不讓整個 section 失敗。

### 7.3 Journal

```bash
journalctl --since "-1 hour" -p warning -n 100 --no-pager -o json
```

- 禁止使用無時間、priority 與筆數限制的裸 `journalctl`。
- 後端逐行解析 JSON，只保留 timestamp、unit、priority、message。
- 每筆 message 限制長度並套用敏感資訊遮蔽。
- 回傳剛好 100 筆時設定 `may_be_truncated=true`，不可宣稱一小時內只有 100 筆。
- 系統沒有 systemd journal 時回傳 `unavailable`，不要偽裝成「沒有錯誤」。

## 8. 管理員固定 Markdown 輸出

### 8.1 狀態標記

最終回答只使用以下四種主要狀態：

| 標記 | 固定文字 | 使用條件 |
| --- | --- | --- |
| `✅` | 正常 | 已取得必要資料，且未發現明確異常 |
| `⚠️` | 注意 | 有偏高、可疑或需要觀察的證據，但尚未證實故障 |
| `❌` | 異常 | 有 failed、非零 exit、明確錯誤或無法提供必要服務 |
| `❓` | 未取得 | 資料無法取得、工具失敗或不能安全下結論 |

分層標籤固定使用：

```text
[PVE] [VM] [OS] [Service] [Application]
```

不得自行產生同義標記，例如「良好」、「危險」、「嚴重」或不同 emoji。

### 8.2 整體狀態規則

1. 有明確 failed service、VM stopped 或具體 error evidence：`❌ 異常`。
2. 沒有明確故障，但資源偏高或出現 warning：`⚠️ 注意`。
3. 沒有異常證據，但必要 section 未取得：`❓ 資料不足`，不得顯示正常。
4. 必要資料皆取得且沒有明顯異常：`✅ 未發現明顯異常`。

這些規則只約束呈現與證據門檻，不在 tool collector 內硬編完整健康判斷。

### 8.3 固定回答模板

```md
## 診斷結論

**整體狀態：⚠️ 注意**

VM 105 正在運行；PVE 資源沒有明確故障，但發現 1 個失敗服務，問題較可能位於應用服務層。

## 分層結果

| 層級 | 狀態 | 關鍵結果 |
| --- | --- | --- |
| [PVE] | ✅ 正常 | VM running，PVE 資源資料可取得 |
| [VM] | ✅ 正常 | CPU 24%，記憶體 71% |
| [OS] | ⚠️ 注意 | 最近一小時有 4 筆 warning/error |
| [Service] | ❌ 異常 | `student-app.service` failed |
| [Application] | ❓ 未取得 | 尚未讀取服務專屬 log |

## 主要證據

- `student-app.service`：`failed`
- 最近錯誤：`Main process exited, status=1/FAILURE`
- CPU 最高程序：`python3 main.py`，82%

## 建議下一步

1. 讀取 `student-app.service` 狀態與最近 100 筆專屬 log。
2. 若 log 指向學生程式錯誤，只標明證據，不直接修改程式。

## 資料缺口

- 尚未取得 Guest filesystem 使用率，不能判定 VM 內磁碟是否已滿。
```

### 8.4 簡潔限制

- 「診斷結論」最多 3 句。
- 分層結果固定使用表格；沒有證據的層級標記 `❓ 未取得`。
- 「主要證據」最多 5 點，只放支持結論的數值、failed item 或 log 摘要。
- 最終回答最多顯示 3 個 failed services、3 個 processes、3 筆 log 證據；其餘以數量摘要。
- 「建議下一步」最多 3 項，按優先順序排列。
- 只有存在資料缺口時才顯示「資料缺口」。
- 不貼完整 JSON、完整 service list、完整 process list、完整 journal 或內部固定 command。
- 不顯示 chain-of-thought；只說結論、證據、限制與下一步。
- 沒有實際資料時不得寫「正常」、「已確認」或具體數值。

### 8.5 無異常範例

```md
## 診斷結論

**整體狀態：✅ 未發現明顯異常**

VM 105 正在運行；目前取得的 PVE、service、process 與最近系統記錄沒有顯示明確故障。

## 分層結果

| 層級 | 狀態 | 關鍵結果 |
| --- | --- | --- |
| [PVE] | ✅ 正常 | VM running，資源資料可取得 |
| [VM] | ✅ 正常 | CPU 12%，記憶體 48% |
| [OS] | ✅ 正常 | 最近一小時未發現 warning/error |
| [Service] | ✅ 正常 | 0 個 failed services |
| [Application] | ❓ 未取得 | 本次未執行應用專屬檢查 |

## 主要證據

- failed services：0
- CPU Top process：最高 8%
- 最近一小時 warning/error：0

## 建議下一步

1. 若仍有特定症狀，請提供服務名稱或發生時間以縮小範圍。
```

## 9. 安全與授權設計

`get_guest_diagnostic_summary` 可自動執行，但必須符合以下條件：

1. 模型唯一可控參數是 `vmid`。
2. 所有 shell 指令由後端固定產生，不從 DB prompt 或模型內容取得。
3. VMID scope 必須在 PVE 與 SSH I/O 前驗證。
4. 不接受 client-controlled host、SSH user、port 或 credential。
5. 固定 probes 仍通過 command guard，作為 defense in depth。
6. 每個 probe 都有 timeout、最大輸出 bytes、最大回傳 rows 與文字長度限制。
7. stdout、stderr、process args 與 journal message 都先 redaction，再進 parser/result。
8. 所有失敗都保留 section status；空資料不等於正常。

不要把一條巨大複合 command 加入 `command_policy.py`。那會讓模型仍能控制
`ssh_exec.command`，並把「受控能力」退化成字串比對。

既有 `ssh_exec` 繼續負責未知、特殊或管理操作：

- unknown/custom command：人工確認。
- hard-deny command：直接阻擋。
- confirmation token：維持 requester、scope、VMID set 與 tool call 綁定。
- 使用者拒絕後：不得重試等價指令。

## 10. 程式修改計畫

### 階段 1：固定契約與 parser

新增：

```text
backend/app/ai/pve_log/guest_diagnostics.py
backend/tests/test_ai_pve_log_guest_diagnostics.py
```

內容：

- 固定 probe definitions。
- systemctl、ps、journal parsers。
- section status 與整體 status 組裝。
- row、字串與 output byte limits。
- process/journal redaction。
- deterministic fixture tests。

### 階段 2：共用 SSH batch runner

修改 `backend/app/ai/pve_log/ssh_exec.py`：

- 新增私有 server-owned batch runner。
- 沿用既有 VM/IP/key resolution、host-key policy、timeout 與 redaction。
- 一次建立 SSH connection，逐項執行固定 probes。
- 回傳每個 probe 的 exit code、stdout/stderr、truncated 狀態。
- 不修改既有公開 `ssh_exec()`、`confirm_exec()` 或 REST schema。

### 階段 3：註冊 Tool 與 agent dispatch

修改 `backend/app/ai/pve_log/chat.py`：

- 在 `_TOOLS` 新增 `get_guest_diagnostic_summary`，只接受 required `vmid`。
- 新增 async dispatch，不把此工具塞進只處理 PVE snapshot 的 `_execute_tool_sync`。
- 重用 request-local `PveToolContext` 取得 resource detail。
- `tools_called` 保留固定 tool name、args、result 與 `tool_call_id`。
- 不影響既有 SSH pending/deferred confirmation barrier。

### 階段 4：更新兩套固定 Prompt

修改：

```text
backend/app/ai/pve_log/chat.py
backend/app/ai/pve_template/prompts.py
```

加入規則：

- 「現在怎麼了／快速檢查／為什麼很慢」等廣度問題，優先呼叫彙總工具。
- 彙總結果已含 PVE resource 時，不重複呼叫 `get_resource_detail`。
- 發現特定 service 或 log 線索後才深入。
- 必須使用第 8 節固定 Markdown 結構與狀態標記。
- 缺少 section 時不得宣稱該層正常。
- 不在最終回答傾倒 raw tool output。

### 階段 5：深入工具

第一版彙總工具穩定後再新增：

```text
get_service_detail(vmid, service_name)
get_recent_logs(vmid, service_name?, priority?, since_minutes?)
```

約束：

- `service_name` 使用嚴格 unit-name validation 與安全 quoting。
- `priority` 使用 enum。
- `since_minutes` 設定上下限。
- `get_recent_logs` 必須保留筆數、bytes 與 message 長度限制。
- 深入工具仍只收結構化參數，不接受自由 shell command。

## 11. 不需修改的介面

第一版不新增 REST route。既有 `ChatResponse` 已能承接任意：

```text
tools_called[].name
tools_called[].args
tools_called[].result
```

目前前端 `AiPveChat` 也會以 tool badge 顯示 tool name，因此第一版不需要修改：

- `frontend/src/services/aiPveLog.js`
- `frontend/src/components/AiPveChat/AiPveChat.jsx`
- DB schema 或 Alembic migrations

只有後續要把固定分層結果做成獨立視覺卡片時，才需要新增前端 structured renderer；
本次先使用安全 Markdown renderer 呈現固定格式。

## 12. 測試與驗收

### 12.1 Parser 與輸出契約

- systemctl 正常、failed、空輸出、locale 差異、非 systemd。
- ps CPU/RAM 排序、長 args、已退出 PID、破損列。
- journal JSON 正常、破損列、100 筆上限、單筆過長。
- password、token、API key、URI credential 遮蔽。
- `ok/partial/unavailable/error` 與整體狀態組合。
- JSON 欄位、型別、筆數上限固定。

### 12.2 授權與執行

- 非法或 scope 外 VMID 在 PVE/SSH 前被拒絕。
- stopped VM 不嘗試 SSH。
- 模型無法傳入 shell command 或覆寫固定 probes。
- 單一 probe 失敗仍回傳其他成功 sections。
- 彙總工具不產生 confirmation token。
- 自訂 `ssh_exec` 仍維持 pending、hard-deny 與確認後重驗證。

### 12.3 Agent 與輸出

- 廣度問題優先使用 `get_guest_diagnostic_summary`。
- 已有 summary 時不重複查 `get_resource_detail`。
- 失敗資料不被回答為正常。
- 最終回答包含固定標題、狀態 emoji、分層表格與最多三項下一步。
- 最終回答不包含 raw JSON、完整 journal 或未遮蔽敏感資訊。

### 12.4 Focused regression

從 `backend/` 執行：

```powershell
uv run python -m pytest tests/test_ai_pve_log_guest_diagnostics.py tests/test_ai_pve_log_f06.py tests/test_ai_pve_log_ssh_exec_scope.py tests/test_ai_pve_log_history.py tests/test_ai_pve_template.py tests/api/routes/test_ai_pve_log_session_forwarding.py -q
uv run ruff check app/ai/pve_log app/ai/pve_template tests/test_ai_pve_log_guest_diagnostics.py
uv run mypy --ignore-missing-imports app/ai/pve_log app/ai/pve_template
```

### 12.5 Live acceptance

使用一台可丟棄、已註冊 SSH key 的 Linux VM 做唯讀驗收：

1. 正常 systemd VM。
2. 含 failed service 的 VM。
3. journal 超過 100 筆的 VM。
4. systemd/journal 不可用或 SSH 無法連線。
5. PVE detail 成功但 Guest 部分失敗。

記錄：

- 第一次可用回答的 tool rounds。
- 彙總 JSON bytes 與實際模型 input tokens。
- 端到端耗時及各 probe 耗時。
- free-form `ssh_exec` 與 confirmation 次數。
- 回答是否正確區分 PVE、VM、OS、Service、Application。

測試、lint 與 build 通過只能證明程式契約；在完成上述 live acceptance 前，不宣稱已驗證
真實 PVE、SSH、Linux command 或 vLLM 端到端行為。

## 13. 完成條件

- 管理員詢問單台 VM 現況時，能以一次彙總 tool call 取得第一輪診斷資料。
- Tool JSON 符合第 6 節固定契約，沒有 raw dump 與未遮蔽 secret。
- AI 回覆符合第 8 節固定 Markdown 樣式。
- VMID scope、SSH guard、timeout、confirmation 與 history validation 沒有退化。
- 部分失敗會明確標示資料缺口，不會產生假正常結論。
- Focused regression 全部通過，並清楚區分自動測試與 live acceptance 邊界。
