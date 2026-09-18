# Teacher Judge：VM 483 SSH 認證失敗與 LXC 範本公鑰注入缺口整理

> 日期：2026-09-17
> 範圍：`backend/app/ai/teacher_judge/script_executor_service.py`、`backend/app/infrastructure/ssh/client.py`、`backend/app/services/template/clone_service.py`、`backend/app/services/proxmox/provisioning_service.py`、`backend/app/services/vm/batch_provision_service.py`、`backend/app/services/scheduling/recurrence_scheduler.py`、前端班級／資源頁
> 狀態：唯讀調查，未修改程式，未連線 runtime，未讀取私鑰內容
> 目的：回答「Teacher Judge 執行機器腳本出現 `Authentication failed` 是什麼問題」，以及「DB 明明有公私鑰，為何仍失敗」。

---

## 1. 原始錯誤

```text
02:10:36 WARNING app.ai.teacher_judge.script_executor_service
  - Teacher Judge target execution failed run=0e27e1b2-... vmid=483
...
File "backend/app/ai/teacher_judge/script_executor_service.py", line 242, in _execute_target_script
  client = create_key_client(...)
File "backend/app/infrastructure/ssh/client.py", line 135, in create_key_client
  client.connect(...)
paramiko.ssh_exception.AuthenticationException: Authentication failed.
```

關鍵判斷：

- 失敗點是 **SSH 連線階段**，腳本尚未上傳、尚未執行。
- 不是模型生成、SFTP、腳本 `exit code`、逾時問題。
- 目前 executor 把這類例外一律記為 `executor_error`（`script_executor_service.py:598-608`），沒有細分 `ssh_auth_failed`。

---

## 2. 結論先行

1. **DB 有配對正確的公私鑰，與本案不衝突。**
   缺的可能是 **LXC 容器內 `/root/.ssh/authorized_keys` 是否有同一把 DB 公鑰**。
2. **班級 LXC 範本複製路徑存在原生缺口：**
   班級建立帶週期排程時以 `start=False` 建立，公鑰只存 DB、不寫入容器；
   之後排程首次開機也沒有補注入。
3. **「建立機器回應沒有回傳公私鑰」是正常契約，不是主因。**
   建立回應本來就不含私鑰；金鑰要另外經授權 API 從 DB 讀取。
4. **只應注入公鑰，私鑰不應放進 VM/LXC。**
   私鑰留在後端加密保存，供 Teacher Judge 登入時使用。
5. **VM 483 是否正好命中上述缺口尚未端到端確認，**
   仍需比對容器內公鑰與 DB 公鑰。

---

## 3. 金鑰模型釐清：DB、容器、前端各自是什麼

| 位置／流程 | 實際內容 |
| --- | --- |
| DB `resources.ssh_public_key` | 平台產生的 OpenSSH 公鑰 |
| DB `resources.ssh_private_key_encrypted` | 同一組金鑰的私鑰，Fernet 加密保存 |
| 容器 `/root/.ssh/authorized_keys` | root 允許登入的公鑰清單，**應包含 DB 公鑰的副本**，不是另一組新金鑰 |
| 資源詳情頁顯示的金鑰 | 從 **DB 讀取**，不是從容器讀取 |
| Teacher Judge 執行時使用的金鑰 | 從 **DB 解密私鑰**，以 `root` 登入目標 IP |

對應程式：

```python
# backend/app/ai/teacher_judge/script_executor_service.py:225-231
"private_key_pem": decrypt_value(resource.ssh_private_key_encrypted),
```

```python
# backend/app/infrastructure/ssh/client.py:135-142
client.connect(
    ...
    pkey=pkey,
    allow_agent=False,
    look_for_keys=False,
)
```

因此以下兩個陳述可以同時成立：

- DB 的公私鑰配對正確、沒有缺少。
- 容器不接受該私鑰登入，因為容器內沒有對應公鑰。

---

## 4. 前端與建立回應契約

### 4.1 沒有獨立 `/machine` 路由

前端路由只有：

- `/class-management`
- `/class-management/:classId`
- `/class-management/:classId/:section`
- `/class-management/:classId/ai`

見 `frontend/src/App.jsx:226-236`。

使用者所說的「`/machine`」實際上是班級工作頁的 machines 區段，不是獨立建立機器 API。

### 4.2 班級建立只送出開通請求

班級工作頁按鈕只呼叫：

```js
// frontend/src/pages/course-operations/class-workspace/ClassWorkspacePage.jsx:874-885
TeachingClassesService.provision(classId)
```

對應：

```js
// frontend/src/services/teachingClasses.js:63
provision(classId) { return apiPost(`/api/v1/teaching-classes/${classId}/provision`, {}); }
```

前端不產生、不保管、不注入 SSH 金鑰。

### 4.3 建立回應本來就不回傳私鑰

```python
# backend/app/schemas/resource.py:183-199
class LXCCreateResponse(BaseModel):
    vmid: int | None = None
    upid: str | None = None
    task_id: str | None = None
    message: str
```

`VMCreateResponse` 結構相同。

金鑰另外經授權 API 讀取：

```python
# backend/app/api/routes/resources.py:293-317
@router.get("/{vmid}/ssh-key", response_model=SSHKeyResponse)
```

```python
# backend/app/schemas/resource.py:272-278
class SSHKeyResponse(BaseModel):
    vmid: int
    ssh_public_key: str | None = None
    ssh_private_key: str | None = None
    login_password: str | None = None
```

前端資源詳情頁也是先讀資源，再有條件讀金鑰：

```js
// frontend/src/pages/personal/resources/detail/OverviewTab.jsx:217-225
ResourcesService.get(vmid).then((r) => {
  if (r.ssh_public_key || r.has_login_password) {
    ResourcesService.getSshKey(vmid)...
```

所以「建立時沒看到公私鑰回傳」不能推論「後端沒保存」。

---

## 5. 後端金鑰儲存與注入矩陣

### 5.1 一般建立路徑：有保存，也有嘗試注入

一般 LXC 建立：

```python
# backend/app/services/proxmox/provisioning_service.py:452-454
private_key_pem, public_key = generate_ed25519_keypair()
```

```python
# backend/app/services/proxmox/provisioning_service.py:460-475
"ssh-public-keys": public_key,
```

```python
# backend/app/services/proxmox/provisioning_service.py:484-495
ssh_private_key_encrypted=encrypt_value(private_key_pem),
ssh_public_key=public_key,
```

一般申請／排程建立也是由 plan 攜帶金鑰後寫 DB：

- `provisioning_service.py:761, 804-805, 1177-1178`
- `backend/app/services/scheduling/coordinator.py:257-258`

QEMU 路徑另有 cloud-init `sshkeys`／`ciuser` 變數，
但登入帳號是否與 Teacher Judge 固定的 `root` 一致，仍需按實際範本驗證，
不能一概視為正常。

### 5.2 範本複製 QEMU：有寫 cloud-init 公鑰

```python
# backend/app/services/template/clone_service.py:415
private_key_pem, public_key = generate_ed25519_keypair()
```

```python
# backend/app/services/template/clone_service.py:224-227
"sshkeys": quote(public_key, safe=""),
```

DB 同樣保存公私鑰：`clone_service.py:490-506`。

### 5.3 範本複製 LXC：`start=True` 才注入，`start=False` 只存 DB

```python
# backend/app/services/template/clone_service.py:469-481
if start:
    proxmox_ops.control(node, new_vmid, resource_type, "start")
    ...
    if resource_type == "lxc":
        _inject_lxc_platform_key(node, new_vmid, public_key)
```

```python
# backend/app/services/template/clone_service.py:482-487
elif resource_type == "lxc":
    logger.warning(
        "CT %s not started at clone time; platform SSH key recorded in DB "
        "only, guest authorized_keys must be synced after first boot",
        new_vmid,
    )
```

注入實作本身是開機後以 `pct exec` 寫 `/root/.ssh/authorized_keys`：

```python
# backend/app/services/template/clone_service.py:305-340
def _inject_lxc_platform_key(...)
```

相關修補 commit：

```text
a0fdb711 fix(template): LXC 克隆後以 pct exec 注入平台公鑰至 authorized_keys
```

該修補解決了「立即開機 LXC 範本機完全不注入」的問題，
但 `start=False` 分支仍是明確記錄「DB only」，留待首次開機同步。

### 5.4 Course Lab 經 `provisioning_service` 的 LXC 範本 clone：同樣值得懷疑

```python
# backend/app/services/proxmox/provisioning_service.py:942-993
if plan.get("lxc_clone"):
    clone_service.clone_with_fallback(...)
    ...
    return new_vmid, actual_node
```

該分支只重設 `cores`、`memory`、`net0`／`nameserver`，
在已讀程式範圍內沒有呼叫 `_inject_lxc_platform_key`，
卻走同一個 plan 把 DB 金鑰寫入。

這是第二條可能漏注入的 LXC 範本路徑，
是否實際被 VM 483 使用，需用該資源的 `template_id`／`batch_job_id` 判定。

---

## 6. 班級流程為何走到 `start=False`

批次開通是否立即開機，取決於是否有週期排程：

```python
# backend/app/services/vm/batch_provision_service.py:303
start_on_create = job.recurrence_rule is None
```

班級節點若使用範本：

```python
# backend/app/api/routes/teaching_classes.py:992-994
if node.source_type == "template" and node.source_template_id:
    return {"vm_template_id": str(node.source_template_id)}
```

班級送 job 時帶週期排程：

```python
# backend/app/api/routes/teaching_classes.py:1008-1042
rule, duration = _recurrence(item)
...
recurrence_rule=rule,
recurrence_duration_minutes=duration,
```

再進入範本 clone：

```python
# backend/app/services/vm/batch_provision_service.py:538-559
if params.get("vm_template_id"):
    ...
    clone_result = clone_service.run_clone_task(uuid.uuid4(), payload)
```

因此典型班級 LXC 範本流程是：

```text
class-management 建立
  -> batch job（含 recurrence_rule）
  -> start=False
  -> LXC 範本 clone
  -> DB 保存公私鑰
  -> 跳過容器 authorized_keys 注入
  -> 等排程首次開機
```

而排程首次開機目前只有 `start`，沒有補注入：

```python
# backend/app/services/scheduling/recurrence_scheduler.py:459-468
proxmox_service.control(spec.node, spec.vmid, spec.resource_type, "start")
```

---

## 7. 為什麼 DB 有金鑰仍會 `Authentication failed`

完整對照：

```text
平台產生一組公私鑰
  ├─ 私鑰 → 加密存 DB → Teacher Judge 登入時使用
  └─ 公鑰 → 存 DB
          └─ 應同時寫入容器 authorized_keys → 缺口發生在這裡
```

Teacher Judge 的前置檢查只看 DB 是否有 key：

```python
# backend/app/ai/teacher_judge/script_executor_service.py:219-223
if not resource.ssh_private_key_encrypted:
    raise TargetExecutionError(..., "missing_ssh_key")
```

DB 有 key 可以通過檢查，但 SSH 仍可能因容器缺少對應公鑰而失敗。
這也解釋了使用者觀察到的現象：

- 資源頁看得到公私鑰。
- Teacher Judge 仍報 `Authentication failed`。
- 不需要重置或換機器也會發生。

---

## 8. 已確認與未確認

### 已確認（以實檔為準）

- 錯誤發生在 SSH 公鑰認證階段。
- DB 模型允許金鑰為空，但班級／範本流程目前都會寫入兩欄。
- `start=False` 的 LXC 範本 clone 明確只存 DB。
- 排程首次開機沒有金鑰同步。
- 建立回應不含私鑰是正常設計。
- 私鑰不應注入容器。

### 未確認（需實際 VM 483 證據）

- VM 483 的建立來源是否為班級 LXC 範本。
- 容器 `/root/.ssh/authorized_keys` 是否缺少 DB 公鑰。
- 實際連線 IP 是否屬於 VM 483。
- `root` 登入、sshd 設定、檔案權限、防火牆／網路是否另有問題。
- QEMU 範本的 cloud-init 使用者是否與 Teacher Judge 的 `root` 不一致。

在比對完成前，不應宣稱 VM 483 已端到端定因，
也不應直接輪替金鑰、重建機器或放寬 root 登入。

---

## 9. 建議的唯讀驗證順序

1. 查 VM 483 的 DB 紀錄：
   - `template_id`
   - `batch_job_id`
   - `ssh_public_key` 是否存在
   - 所屬班級與批次任務是否有 `recurrence_rule`
2. 查 Proxmox 任務／後端日誌：
   - clone 是否以 `start=False` 執行
   - 是否出現 `platform SSH key recorded in DB only`
   - `_inject_lxc_platform_key` 是否成功或失敗
3. 比對公鑰指紋（不要貼出私鑰）：
   - DB `ssh_public_key`
   - 容器 `/root/.ssh/authorized_keys`
4. 若兩邊公鑰一致，再查：
   - 實際目標 IP
   - `PermitRootLogin`
   - `/root/.ssh` 與 `authorized_keys` 權限
   - sshd 認證日誌
   - 防火牆與連通性

---

## 10. 修正方向（尚未實作，待確認）

- 在排程首次啟動 LXC 時補做一次平台公鑰同步。
- 或在 Teacher Judge 執行前做 SSH 可用性／金鑰一致性檢查，
  並回傳可區分的 `ssh_auth_failed`，而非籠統 `executor_error`。
- 注入失敗不應靜默視為建立成功；
  至少要讓班級機器狀態、資源狀態或 Teacher Judge 前置檢查看得到。
- 需要隔離回歸測試，模擬：
  `start=False` → 排程首次開機 → Teacher Judge 執行，
  不得直接操作真實 VM。
- 不重建現有機器，不主動輪替現有金鑰，
  不把私鑰寫入容器。
