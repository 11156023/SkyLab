# 登入帳號命名政策

SkyLab 寫進新 Linux 機器的登入帳號（cloud-init 的 `ciuser`）只依一份政策檔驗證：`backend/config/username_policy.yaml`。後端啟動時載入；申請表單經 `GET /api/v1/policies/username` 取得同一套規則，並用 `POST /api/v1/policies/username/check` 即時檢查，所以表單和伺服器不會不一致。檔案無效時後端直接拒絕啟動，而不是放寬規則。

Windows 範本不在範圍內：Proxmox 對 Windows 機器忽略 `ciuser`，帳號由範本內的 `cloudbase-init.conf` 固定。

## 規則

- 名稱必須符合樣式：小寫字母、數字、`_` 與 `-`，以字母開頭，1 到 32 個字元。
- 平台保留的名稱（`skylab`、`ansible`、`provision`）不可使用。
- 常見發行版系統帳號的名稱與前綴（`root`、`daemon`、`www-data`、`systemd-…` 等）不可使用，完整清單見下方。
- 雲端映像的預設帳號（`default_user_warn` 裡的 `ubuntu`、`debian`、`ec2-user` 等）可以使用，但會顯示警告。
- 清單載入時會去除重複。

違反規則的名稱在送出申請前就會被表單擋下，建機前後端會再驗一次；警告一律不擋。

## 政策檔

<<< ../../../backend/config/username_policy.yaml{yaml}

## 相關

- [申請機器](/zh-TW/manual/my-requests)：選擇登入帳號的地方
- [範本](/zh-TW/manual/templates)：沒有 cloud-init 的範本為什麼沿用自己的帳號
