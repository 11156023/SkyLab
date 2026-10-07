# 安全政策

> [English](./SECURITY.md) | **繁體中文**

SkyLab 管理的是 hypervisor、學生機器、閘道防火牆與 AI API 憑證，我們會嚴肅看待每一份漏洞回報。

## 支援版本

只支援 `main` 分支。沒有另外維護的 release 線；請從 `main` 部署並保持更新。

## 回報漏洞

請**不要**為安全問題開公開的 issue。

請改用 GitHub 的私密漏洞回報：到本 repo 的 **Security** 分頁，選 **Report a vulnerability**，這會建立一份只有維護者看得到的私密 advisory。

請盡量提供下列資訊：

- 受影響的元件（後端路由、前端頁面、閘道腳本、桌面端、vLLM / LiteLLM stack）
- 重現步驟或概念驗證
- 你認為的影響範圍（例如角色之間的權限提升、存取他人的 VM、憑證外洩）
- 你測試時所用的 commit 或版本

我們會盡量在一週內回覆收到回報，並在修復過程中與你保持聯繫。請在公開揭露前給我們合理的修復時間。

## 範圍說明

- 機密絕不可提交到 repo。`SECRET_KEY` 既簽發 token，也衍生加密資料庫內憑證的金鑰；更換時要用 `backend/scripts/rotate_secret_key.py`，不可只改 `.env`。
- Proxmox、LDAP 與閘道憑證加密存於資料庫，由介面設定，不經 `.env`。
- 使用者自訂的機器登入密碼（申請表單、範本克隆、重設密碼）只以 SHA-512 crypt 雜湊保存，並直接把雜湊寫進機器；API 不會回傳，忘記密碼只能重設。唯一例外是 Windows：cloudbase-init 只收明文，所以那組密碼會加密暫存到機器建好為止，之後清除。平台代發的密碼（班級機器、快速練習）加密存放，機器擁有者可以查看。
- 給維運者的強化建議（信任代理、rootless Docker 的來源 IP、憑證處理）見 [`docs/deployment.zh-TW.md`](docs/deployment.zh-TW.md)。

感謝你協助保護 SkyLab 與它的使用者。
