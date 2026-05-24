#!/usr/bin/env bash
# =============================================================================
# SkyLab - Gateway VM 安裝腳本
# 支援系統：Debian 12 (Bookworm)
# 安裝服務：haproxy + Traefik + frps + frpc
# =============================================================================

set -euo pipefail

# ── 接受 SkyLab 公鑰參數 ────────────────────────────────────────────────
# 用法：bash install.sh "<ssh-ed25519 AAAA...>"
# 若提供公鑰，自動寫入 /root/.ssh/authorized_keys
skylab_PUBKEY="${1:-}"

# ── 版本設定（升級時只改這裡）────────────────────────────────────────────────
TRAEFIK_VERSION="3.3.4"
FRP_VERSION="0.62.0"
ARCH="amd64"

# ── 顏色輸出 ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
section() { echo -e "\n${GREEN}══════ $* ══════${NC}"; }

# ── Root 檢查 ─────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || error "請以 root 執行此腳本（sudo bash install.sh）"

# ── 系統更新 ──────────────────────────────────────────────────────────────────
section "系統更新"
apt-get update -qq
apt-get install -y -qq curl wget ca-certificates gnupg lsb-release

# =============================================================================
# 1. haproxy
# =============================================================================
section "安裝 haproxy"

apt-get install -y haproxy

# 初始設定
cat > /etc/haproxy/haproxy.cfg << 'HAPROXY_EOF'
global
    log /dev/log local0
    log /dev/log local1 notice
    maxconn 50000
    # Runtime API socket（SkyLab 用於動態管理）
    stats socket /run/haproxy/admin.sock mode 660 level admin expose-fd listeners
    stats timeout 30s
    user haproxy
    group haproxy
    daemon

defaults
    log     global
    mode    tcp
    option  tcplog
    option  dontlognull
    timeout connect 5s
    timeout client  1m
    timeout server  1m

# ──────────────────────────────────────────────────────────────────────────────
# 以下為 SkyLab 自動管理區域
# 請勿手動修改 BEGIN/END 之間的內容，由 SkyLab 透過 SSH 自動維護
# ──────────────────────────────────────────────────────────────────────────────
# BEGIN_skylab_MANAGED

# END_skylab_MANAGED
HAPROXY_EOF

systemctl enable haproxy
systemctl restart haproxy
info "haproxy 安裝完成"

# =============================================================================
# 2. Traefik
# =============================================================================
section "安裝 Traefik v${TRAEFIK_VERSION}"

TRAEFIK_URL="https://github.com/traefik/traefik/releases/download/v${TRAEFIK_VERSION}/traefik_v${TRAEFIK_VERSION}_linux_${ARCH}.tar.gz"
TMP_DIR=$(mktemp -d)
curl -sL "$TRAEFIK_URL" -o "$TMP_DIR/traefik.tar.gz"
tar xzf "$TMP_DIR/traefik.tar.gz" -C "$TMP_DIR" traefik
mv "$TMP_DIR/traefik" /usr/local/bin/traefik
chmod +x /usr/local/bin/traefik
rm -rf "$TMP_DIR"

# 設定目錄
mkdir -p /etc/traefik/dynamic /etc/traefik/env
touch /etc/traefik/acme.json
chmod 600 /etc/traefik/acme.json

cat > /etc/traefik/env/SkyLab.env << 'TRAEFIK_ENV_EOF'
# SkyLab 自動管理，供 Traefik dnsChallenge 使用
# 實際值會在 admin/domains 設定 Cloudflare Token 後由後端覆寫
CF_DNS_API_TOKEN=""
TRAEFIK_ENV_EOF
chmod 600 /etc/traefik/env/SkyLab.env

# 靜態設定
cat > /etc/traefik/traefik.yml << 'TRAEFIK_EOF'
# Traefik 靜態設定
# 修改此檔案後需重啟 traefik：systemctl restart traefik

entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https
  websecure:
    address: ":443"
  traefik:
    address: "127.0.0.1:8080"

api:
  dashboard: true
  insecure: true

providers:
  file:
    directory: /etc/traefik/dynamic
    watch: true     # 動態設定變更自動生效，無需重啟

certificatesResolvers:
  letsencrypt:
    acme:
      # 會在 admin/domains 完成設定後由 SkyLab 後端覆寫成正式值
      email: admin@example.com
      storage: /etc/traefik/acme.json
      dnsChallenge:
        provider: cloudflare
        resolvers:
          - "1.1.1.1:53"
          - "8.8.8.8:53"

log:
  level: INFO

accessLog: {}
TRAEFIK_EOF

# 初始 dynamic config（空）
cat > /etc/traefik/dynamic/SkyLab.yml << 'DYNAMIC_EOF'
# SkyLab 自動管理的反向代理設定
# 此檔案由 SkyLab 透過 SSH 自動維護，請勿手動修改
http:
  routers: {}
  services: {}
DYNAMIC_EOF

# Systemd service
cat > /etc/systemd/system/traefik.service << 'SYSTEMD_EOF'
[Unit]
Description=Traefik Reverse Proxy
Documentation=https://doc.traefik.io/traefik/
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
EnvironmentFile=-/etc/traefik/env/SkyLab.env
ExecStart=/usr/local/bin/traefik --configFile=/etc/traefik/traefik.yml
Restart=always
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
SYSTEMD_EOF

systemctl daemon-reload
systemctl enable traefik
systemctl start traefik
info "Traefik 安裝完成"

# =============================================================================
# 3. frp（frps + frpc）
# =============================================================================
section "安裝 frp v${FRP_VERSION}"

FRP_URL="https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_${ARCH}.tar.gz"
FRP_TMP=$(mktemp -d)
curl -sL "$FRP_URL" -o "$FRP_TMP/frp.tar.gz"
tar xzf "$FRP_TMP/frp.tar.gz" -C "$FRP_TMP" --strip-components=1
mv "$FRP_TMP/frps" /usr/local/bin/frps
mv "$FRP_TMP/frpc" /usr/local/bin/frpc
chmod +x /usr/local/bin/frps /usr/local/bin/frpc
rm -rf "$FRP_TMP"

mkdir -p /etc/frp

# frps 設定（服務端，讓學生用戶端連入）
cat > /etc/frp/frps.toml << 'FRPS_EOF'
# frp Server 設定
# 學生用戶端（exe）連線到此伺服器，建立 tunnel 存取其 VM

bindAddr = "0.0.0.0"
bindPort = 7000

# 認證（請修改為強密碼）
auth.method = "token"
auth.token = "CHANGE_THIS_TOKEN"

# Web Dashboard（可選）
webServer.addr = "127.0.0.1"
webServer.port = 7500
webServer.user = "admin"
webServer.password = "CHANGE_THIS_PASSWORD"

# 允許用戶端使用的 port 範圍（1-65535 全開）
allowPorts = [
  { start = 1, end = 65535 }
]

log.to = "/var/log/frps.log"
log.level = "info"
log.maxDays = 7
FRPS_EOF

# frpc 設定（客戶端，如需連到外部 frps 時使用）
cat > /etc/frp/frpc.toml << 'FRPC_EOF'
# frp Client 設定（按需啟用）
# 若此 Gateway VM 本身需要透過外部 frps 做穿透，請設定此檔

serverAddr = "your-frps-server.example.com"
serverPort = 7000

auth.method = "token"
auth.token = "CHANGE_THIS_TOKEN"

log.to = "/var/log/frpc.log"
log.level = "info"

# 範例：將 Gateway VM 的 SSH 暴露到外部 frps
# [[proxies]]
# name = "gateway-ssh"
# type = "tcp"
# localIP = "127.0.0.1"
# localPort = 22
# remotePort = 12022
FRPC_EOF

# frps systemd service
cat > /etc/systemd/system/frps.service << 'SYSTEMD_EOF'
[Unit]
Description=frp Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/frps -c /etc/frp/frps.toml
Restart=always
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
SYSTEMD_EOF

# frpc systemd service（預設不自啟，需要時手動啟用）
cat > /etc/systemd/system/frpc.service << 'SYSTEMD_EOF'
[Unit]
Description=frp Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/frpc -c /etc/frp/frpc.toml
Restart=always
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
SYSTEMD_EOF

systemctl daemon-reload
systemctl enable frps
systemctl start frps
# frpc 預設不啟動，需要時手動：systemctl enable frpc && systemctl start frpc
info "frp 安裝完成（frps 已啟動，frpc 待設定後手動啟用）"

# =============================================================================
# 4. SkyLab SSH 公鑰（若有提供則自動寫入）
# =============================================================================
if [[ -n "$skylab_PUBKEY" ]]; then
    section "設定 SkyLab SSH 公鑰"
    mkdir -p /root/.ssh
    chmod 700 /root/.ssh
    # 避免重複寫入同一把公鑰
    if ! grep -qF "$skylab_PUBKEY" /root/.ssh/authorized_keys 2>/dev/null; then
        echo "$skylab_PUBKEY" >> /root/.ssh/authorized_keys
    fi
    chmod 600 /root/.ssh/authorized_keys
    info "SkyLab 公鑰已加入 /root/.ssh/authorized_keys"
else
    warn "未提供公鑰，請手動將 SkyLab 公鑰加入 /root/.ssh/authorized_keys"
fi

# =============================================================================
# 完成
# =============================================================================
section "安裝完成"

cat << 'SUMMARY_EOF'

┌─────────────────────────────────────────────────────────────────┐
│              SkyLab Gateway VM 安裝完成                    │
├─────────────────────────────────────────────────────────────────┤
│  服務          狀態      設定檔                                  │
│  haproxy       ✅ 運行   /etc/haproxy/haproxy.cfg               │
│  traefik       ✅ 運行   /etc/traefik/traefik.yml               │
│  frps          ✅ 運行   /etc/frp/frps.toml                     │
│  frpc          ⏸ 停止   /etc/frp/frpc.toml（按需啟用）         │
├─────────────────────────────────────────────────────────────────┤
│  後續步驟：                                                      │
│  1. 修改 /etc/frp/frps.toml 中的 auth.token（必要）            │
│  2. 修改 /etc/traefik/traefik.yml 中的 email（HTTPS 憑證）     │
│  3. 回到 SkyLab 管理介面填入此 VM 的 IP                   │
│  4. 點擊「測試連線」確認 SSH 連線正常                           │
├─────────────────────────────────────────────────────────────────┤
│  常用指令：                                                      │
│  systemctl status haproxy|traefik|frps|frpc                     │
│  systemctl restart haproxy                                       │
│  journalctl -u traefik -f                                        │
└─────────────────────────────────────────────────────────────────┘

SUMMARY_EOF

echo "  frps Token（請立即修改）："
grep "auth.token" /etc/frp/frps.toml | head -1
echo ""
