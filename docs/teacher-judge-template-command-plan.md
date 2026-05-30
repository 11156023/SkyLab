# Teacher Judge Template Command Catalog Plan

## 背景

AI 情境分析目前的 Teacher Judge 流程是：

1. 老師上傳評分表。
2. 後端解析 DOCX / PDF。
3. LLM 分析 rubric，產出可自動偵測、部分偵測、人工評閱的初步結果。
4. 前端顯示結果，老師可再對話調整。

接下來要補上的能力不是直接執行 SSH 指令，而是讓 LLM 在分析評分表時知道「目前選定的教學環境 template 有哪些可用檢查指令」，並產出可執行的評分計劃書。

本階段先不依賴既有 resource context 或實際 `service_template_slug`，因為目前模板上下文功能不穩。前端先提供固定 template 選鈕：

- `linux`：一般 Linux / LXC
- `python`：Python 開發環境
- `n8n`：n8n 服務環境

## 目標

- 建立一張後端資料表，保存 Teacher Judge 可使用的 template command catalog。
- 前端 AI 情境分析頁提供固定 template 選擇。
- 上傳 rubric 時一併送出 `template_key`。
- 後端依 `template_key` 查出可用 command catalog，注入 LLM judge prompt。
- LLM 產出評分計劃書時只能引用既有 `command_key`，不可自行產生 shell command。
- 本階段只產生計劃書，不執行 command、不做實際評分。

## 非目標

- 不在本階段執行 SSH 指令。
- 不在本階段計算實際得分。
- 不把評分判斷規則放入 command table。
- 不處理目前壞掉或不穩定的實際 resource template context。
- 不引入 skills 作為 command 權威來源。
- 不新增獨立 database instance；沿用既有後端資料庫，新增 migration/table。

## 核心流程

```text
老師選擇 template
-> 老師上傳評分表
-> /rubric/upload 接收 file + template_key
-> 後端解析文件
-> 後端查 teacher_judge_template_commands
-> 將可用 command catalog 注入 Teacher Judge prompt
-> LLM 分析 rubric
-> LLM 產出含 check_steps 的評分計劃書
-> 後端驗證 command_key 是否存在且 enabled
-> 前端顯示評分計劃書
```

## 資料表設計

表名：

```text
teacher_judge_template_commands
```

欄位：

```text
id                    UUID primary key
template_key          string, indexed
command_key           string
command_label         string
category              string
command_template      text
description           text
risk_level            string
requires_confirmation bool
enabled               bool
created_at            datetime
updated_at            datetime
```

建議限制：

```text
unique(template_key, command_key)
```

欄位語意：

- `template_key`：前端選到的 template，例如 `linux`、`python`、`n8n`。
- `command_key`：LLM 計劃書引用的穩定 ID，例如 `python.version`。
- `command_label`：前端或管理介面可顯示的名稱。
- `category`：分類，例如 `system`、`service`、`port`、`process`、`runtime`。
- `command_template`：未來 executor 實際執行的 shell command。
- `description`：給 LLM 理解這個指令用途，只描述能查到什麼，不描述如何給分。
- `risk_level`：`read_only`、`low`、`write`、`dangerous`。
- `requires_confirmation`：未來執行前是否需要使用者確認。
- `enabled`：可停用單一 command。

設計原則：

- DB 只保存「可執行檢查能力」。
- 評分判斷、證據解讀、給分邏輯交給最後 LLM 統整。
- 第一版不支援參數化 command，避免 `{port}` / `{service}` 類型的注入與驗證問題。
- 非 shell 指令可直接由 LLM 處理，不放進此表。

## 初始 Seed Data

### linux

```text
linux.os_info          cat /etc/os-release
linux.kernel           uname -a
linux.disk_usage       df -h
linux.memory_usage     free -m
linux.listening_ports  ss -lntp
linux.processes        ps aux
```

### python

```text
python.version         python3 --version
python.pip_list        python3 -m pip list
python.processes       ps aux | grep -E 'python|uvicorn|flask|django'
python.listening_ports ss -lntp
```

### n8n

```text
n8n.port_check         ss -lntp | grep ':5678'
n8n.http_check         curl -I --max-time 5 http://127.0.0.1:5678
n8n.process_check      ps aux | grep -i n8n
n8n.docker_check       docker ps --format '{{.Names}} {{.Status}} {{.Ports}}' | grep -i n8n
```

第一版建議全部設為：

```text
risk_level = read_only
requires_confirmation = true
enabled = true
```

## 後端改動

### Model / Migration

新增 SQLModel model：

```text
backend/app/models/teacher_judge_template_command.py
```

新增 Alembic migration 建立資料表與 unique constraint。

### Service

新增 service：

```text
backend/app/ai/teacher_judge/template_command_service.py
```

責任：

```text
get_enabled_template_commands(session, template_key)
format_template_commands_for_prompt(commands)
validate_check_steps(template_key, items, commands)
```

### API

調整：

```text
POST /api/v1/rubric/upload
```

目前只接 `file`，改成 multipart form：

```text
file
template_key
```

行為：

- 未傳 `template_key` 時預設 `linux`。
- 傳入未知 `template_key` 時建議回 400，避免默默產出錯誤計劃。
- 查不到 enabled commands 時仍可分析 rubric，但 prompt 要明確告知「沒有 template command catalog」。

### Teacher Judge

調整：

```text
analyze_rubric(raw_text, template_key, template_commands)
```

Prompt 新增內容：

- 目前選定 template。
- 可用 command catalog 清單。
- LLM 不得發明 `command_key`。
- LLM 不得輸出 shell command。
- `check_steps` 只引用 `template_key` + `command_key`。
- `checked` 仍預設 false，因為本階段尚未執行檢查。

## Schema 調整

`RubricItem` 新增可選欄位：

```json
{
  "check_steps": [
    {
      "template_key": "n8n",
      "command_key": "n8n.port_check"
    }
  ]
}
```

輸出範例：

```json
{
  "id": "item-1",
  "title": "n8n 服務可正常啟動",
  "description": "學生需完成 n8n 部署並能開啟 Web UI",
  "checked": false,
  "detectable": "auto",
  "detection_method": "可透過 n8n template 的 port 與 HTTP 檢查輔助判斷",
  "check_steps": [
    {
      "template_key": "n8n",
      "command_key": "n8n.port_check"
    },
    {
      "template_key": "n8n",
      "command_key": "n8n.http_check"
    }
  ],
  "fallback": "若無法自動檢查，請學生提供 n8n Web UI 截圖"
}
```

後端 normalize 原則：

- 舊資料沒有 `check_steps` 時保持相容。
- AI 輸出不存在的 `command_key` 時移除該 step。
- 若 `detectable=auto` 但沒有任何有效 `check_steps`，可降級為 `partial` 或保留 auto 但清楚標示 detection_method；實作時需選一種策略並加測試。

## 前端改動

目標檔案：

```text
frontend/src/features/ai-judge/components/AiJudgeContent.tsx
frontend/src/features/ai-judge/api.ts
```

在「上傳情境評估表」區塊新增 template 選鈕：

```text
評分環境
[一般 Linux/LXC] [Python] [n8n]
```

互動：

- 預設 `linux`。
- 使用 segmented control 或三個 toggle buttons。
- 上傳 rubric 時送出 `template_key`。
- 上傳後結果中顯示本次使用的 template label。

前端顯示計劃書：

- 每個 rubric item 仍顯示可偵測性、偵測方式、fallback。
- 若有 `check_steps`，顯示 command label / command key。
- 不顯示 raw shell command，避免老師誤以為已經執行。

## 驗證計劃

後端：

```text
pytest backend/tests/test_teacher_judge_boundaries.py
pytest backend/tests/test_rubric_template_commands.py
```

建議新增測試：

- migration/model 建立 command catalog。
- `template_key=linux` 可查到 seed commands。
- `/rubric/upload` 未傳 template_key 時使用 `linux`。
- `/rubric/upload` 傳未知 template_key 時回 400。
- LLM payload 包含 command catalog，但不包含不相關 template。
- normalize 保留合法 `check_steps`。
- normalize 移除不存在或 disabled 的 `command_key`。

前端：

```text
cd frontend
bunx biome check src/features/ai-judge
bun run test:unit -- src/features/ai-judge
```

建議新增測試：

- 預設選到 `linux`。
- 切換 `python` / `n8n` 後 upload body 帶正確 `template_key`。
- 有 `check_steps` 時可顯示 command key / label。

手動驗證：

- 在 AI 情境分析頁選 `n8n`。
- 上傳包含「n8n 服務啟動、Web UI 可存取」的 rubric。
- 確認產出的評分計劃書引用 `n8n.port_check` / `n8n.http_check`。
- 確認頁面沒有宣稱已完成實際檢查。

## 風險

- LLM 可能把「可用檢查」誤寫成「已完成檢查」。Prompt 與 UI 都要強調這是計劃書。
- `n8n` 部署方式可能是 systemd 或 Docker，第一版先用多個 read-only 候選檢查，不保證全部環境適用。
- 如果未來加入參數化 command，需要新增 `params_schema` 與嚴格驗證，不應直接讓 LLM 填 shell 字串。
- 若後續接上真實 `service_template_slug`，要保留前端手動 template override，方便模板上下文壞掉時仍可使用。

## 建議實作順序

1. 新增 model + migration + seed commands。
2. 新增 template command service。
3. 調整 rubric upload 接 `template_key`。
4. 調整 Teacher Judge prompt 與 schema，產出 `check_steps`。
5. 加後端 normalization / validation 測試。
6. 前端新增 template 選鈕與 upload payload。
7. 前端顯示 check steps。
8. 做端到端手動驗證。
