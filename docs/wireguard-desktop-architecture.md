# Desktop WireGuard 架構與部署

SkyLab Connect 使用 WireGuard 建立桌面端到 Gateway VM 的加密 L3 網路。桌面端取得授權後，直接連到 VM 的實際位址與服務埠。

## 連線流程

1. Desktop Client 在本機產生 X25519 金鑰；私鑰只以 Electron `safeStorage` 加密保存。
2. Client 以登入 token 呼叫 `POST /api/v1/desktop-client/wireguard/connect`，只送出裝置 ID 與公鑰。
3. Backend 從既有 `resources/my` 授權邏輯取得使用者目前可控制、正在執行的 VM。
4. Backend 透過 Gateway 既有的 SSH 管理通道加入 WireGuard peer，並加入限時 nftables ACL。
5. Client 安裝短效 WireGuard tunnel，路由只包含 VM 子網，SSH/RDP 直接連線到 `VM_IP:22` 或 `VM_IP:3389`。
6. 使用者中斷或登出時，Client 移除本機 tunnel，Backend 同步移除 peer 與 ACL。

ACL tuple 為 `client_tunnel_ip . vm_ip . tcp_port`。LXC 僅開 SSH；QEMU VM 開 SSH 與 RDP。ACL 預設八小時到期，Gateway 即使無法收到中斷請求也會自動停止放行資料流。

## Gateway VM

目前配置使用：

- WireGuard interface：`wg0` / `10.250.0.1/16`
- UDP listen port：`51821`（`51820` 保留給 NetBird）
- VM network：`10.10.0.0/16`，由 `eth1` 送出
- SNAT address：`10.10.0.2`
- nftables table：`inet campus_cloud_wg`
- systemd units：`wg-quick@wg0`、`campus-cloud-wg-firewall.service`

在新 Gateway 上先確認介面、位址與 UDP port 沒有衝突，再以 root 執行：

```bash
sudo ./gateway/install.sh
```

這是 Gateway 的唯一安裝入口，會安裝 nginx（Port 轉發與網域反向代理）、certbot、WireGuard、nftables ACL 與 SNAT。Installer 會先將網路、防火牆及服務設定備份到 `/root/campus-cloud-backups/`，不會移除既有 NetBird，也不會清空整份 UFW ruleset。若 `/etc/wireguard/wg0.conf` 不是 Campus Cloud 管理的檔案，Installer 會拒絕覆寫。

Gateway 上游防火牆或 NAT 還必須將對外的 UDP `51821` 轉送到 Gateway。若 Client 與 Gateway 位於同一個可路由網路，可直接使用 Gateway 的內部位址。

## Backend 設定

```dotenv
WIREGUARD_ENDPOINT_HOST=192.168.100.143
WIREGUARD_ENDPOINT_PORT=51821
WIREGUARD_INTERFACE=wg0
WIREGUARD_CLIENT_SUBNET=10.250.0.0/16
WIREGUARD_VM_SUBNET=10.10.0.0/16
WIREGUARD_KEEPALIVE_SECONDS=25
WIREGUARD_SESSION_TTL_SECONDS=28800
```

正式環境的 `WIREGUARD_ENDPOINT_HOST` 應填 Client 可以到達的 DNS 名稱或公網 IP，而不是管理用 SSH 位址。部署 Backend 前必須先套用 Alembic migration，建立 `wireguard_peers` table。

## Desktop Client

目前 Windows 版本使用官方 WireGuard for Windows 的 tunnel service。正式 Setup EXE 內含經 SHA-256 與 Authenticode 驗證的官方 MSI，安裝 SkyLab Connect 時會一併安裝；portable EXE 若偵測到系統尚未安裝 WireGuard，會在第一次連線時要求 UAC 並安裝同一份 MSI。因此學生不需要事先另外下載 WireGuard。

WireGuard 是共用的系統網路元件，移除 SkyLab Connect 時不會連帶移除 WireGuard，以免中斷其他應用程式的 tunnel。第三方授權聲明會一起放在 App resources 的 `wireguard/THIRD_PARTY_NOTICES.txt`。

Client 不會把私鑰傳給 Backend，產生 tunnel 設定後也會立即刪除暫存明文設定檔。

## 驗證

Gateway 健康檢查：

```bash
systemctl is-active wg-quick@wg0 campus-cloud-wg-firewall.service
wg show wg0
nft list set inet campus_cloud_wg allowed_tcp
```

連線後應看到一個 peer，以及只屬於該使用者 VM 的限時 ACL。中斷後 peer 與對應 ACL 應立即消失。從 Client 測試時，應直接連 VM IP。
