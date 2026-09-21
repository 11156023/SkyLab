#!/usr/bin/env bash
# =============================================================================
# SkyLab - Gateway VM 安裝腳本
# 支援系統：Debian 12 / 13
# 安裝服務：HAProxy + Traefik + WireGuard + nftables ACL / SNAT
# =============================================================================

set -euo pipefail
export LC_ALL=C

# ── 接受 SkyLab 公鑰參數 ────────────────────────────────────────────────
# 用法：bash install.sh "<ssh-ed25519 AAAA...>"
# 若提供公鑰，自動寫入 /root/.ssh/authorized_keys
skylab_PUBKEY="${1:-}"

# ── 版本設定（升級時只改這裡）────────────────────────────────────────────────
TRAEFIK_VERSION="3.3.4"
ARCH="amd64"

# ── WireGuard 設定（可用同名環境變數覆寫）──────────────────────────────────
WG_INTERFACE="${WG_INTERFACE:-wg0}"
WG_ADDRESS="${WG_ADDRESS:-10.250.0.1/16}"
WG_CLIENT_SUBNET="${WG_CLIENT_SUBNET:-10.250.0.0/16}"
WG_VM_SUBNET="${WG_VM_SUBNET:-10.10.0.0/16}"
WG_VM_INTERFACE="${WG_VM_INTERFACE:-eth1}"
WG_SNAT_ADDRESS="${WG_SNAT_ADDRESS:-10.10.0.2}"
WG_INGRESS_INTERFACE="${WG_INGRESS_INTERFACE:-eth0}"
WG_LISTEN_PORT="${WG_LISTEN_PORT:-51821}"
WG_ACL_TIMEOUT="${WG_ACL_TIMEOUT:-8h}"

WG_DIR="/etc/wireguard"
WG_CONFIG="${WG_DIR}/${WG_INTERFACE}.conf"
NFT_DIR="/etc/nftables.d"
NFT_CONFIG="${NFT_DIR}/campus-cloud-wg.nft"
FIREWALL_UNIT="/etc/systemd/system/campus-cloud-wg-firewall.service"
WG_OVERRIDE_DIR="/etc/systemd/system/wg-quick@${WG_INTERFACE}.service.d"
WG_OVERRIDE="${WG_OVERRIDE_DIR}/campus-cloud.conf"
BACKUP_ROOT="/root/campus-cloud-backups"
MANAGED_WG_MARKER="# Campus Cloud managed WireGuard interface"
HAPROXY_CONFIG_PREEXISTED=false
if [[ -s /etc/haproxy/haproxy.cfg ]]; then
    HAPROXY_CONFIG_PREEXISTED=true
fi

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
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq \
    curl wget ca-certificates gnupg lsb-release openssl tar iproute2

for command in ip ss systemctl tar; do
    command -v "$command" >/dev/null || error "缺少必要指令：${command}"
done

ip link show "$WG_VM_INTERFACE" >/dev/null 2>&1 \
    || error "找不到 VM 內網介面：${WG_VM_INTERFACE}"
ip link show "$WG_INGRESS_INTERFACE" >/dev/null 2>&1 \
    || error "找不到 WireGuard 對外介面：${WG_INGRESS_INTERFACE}"
ip -4 address show dev "$WG_VM_INTERFACE" | grep -Fq "${WG_SNAT_ADDRESS}/" \
    || error "${WG_VM_INTERFACE} 未設定 SNAT 位址 ${WG_SNAT_ADDRESS}"

if [[ -f "$WG_CONFIG" ]] && ! grep -Fq "$MANAGED_WG_MARKER" "$WG_CONFIG"; then
    error "拒絕覆寫非 SkyLab 管理的 WireGuard 設定：${WG_CONFIG}"
fi

if ss -H -lun "sport = :${WG_LISTEN_PORT}" | grep -q .; then
    current_port=""
    if command -v wg >/dev/null 2>&1; then
        current_port="$(wg show "$WG_INTERFACE" listen-port 2>/dev/null || true)"
    fi
    [[ "$current_port" == "$WG_LISTEN_PORT" ]] \
        || error "UDP ${WG_LISTEN_PORT} 已被其他服務使用"
fi

# 修改任何 Gateway 設定前先建立可驗證備份。
section "備份現有 Gateway 設定"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="${BACKUP_ROOT}/gateway-${stamp}"
install -d -m 700 "$backup"
if command -v iptables-save >/dev/null 2>&1; then
    iptables-save >"${backup}/iptables-save.txt"
else
    printf '%s\n' "iptables-save unavailable" >"${backup}/iptables-save.txt"
fi
ip -details address show >"${backup}/ip-address.txt"
ip route show table all >"${backup}/ip-routes.txt"
if command -v ufw >/dev/null 2>&1; then
    ufw status numbered >"${backup}/ufw-status.txt"
else
    printf '%s\n' "ufw unavailable" >"${backup}/ufw-status.txt"
fi
tar_paths=()
for path in \
    etc/haproxy etc/traefik etc/wireguard etc/nftables.d etc/ufw \
    etc/systemd/network etc/systemd/system etc/sysctl.d; do
    [[ -e "/${path}" ]] && tar_paths+=("${path}")
done
[[ -f /etc/sysctl.conf ]] && tar_paths+=(etc/sysctl.conf)
if ((${#tar_paths[@]})); then
    tar -C / -czf "${backup}/gateway-config.tgz" "${tar_paths[@]}"
else
    tar -C / -czf "${backup}/gateway-config.tgz" --files-from /dev/null
fi
find "$backup" -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum >"${backup}/SHA256SUMS"
sha256sum -c "${backup}/SHA256SUMS" >/dev/null
info "備份完成：${backup}"

apt-get install -y -qq haproxy wireguard-tools nftables ufw

# Debian 的全域 nftables.service 可能載入含 `flush ruleset` 的規則；SkyLab
# 使用自己的獨立 unit，避免清除 UFW、NetBird 或其他既有服務的規則。
systemctl disable --now nftables.service >/dev/null 2>&1 || true

for command in wg nft ufw; do
    command -v "$command" >/dev/null || error "缺少必要指令：${command}"
done

# =============================================================================
# 1. haproxy
# =============================================================================
section "安裝 haproxy"

# 初次安裝才建立基礎設定；重跑時保留 SkyLab 已動態產生的規則。
if grep -Fq "# BEGIN_skylab_MANAGED" /etc/haproxy/haproxy.cfg 2>/dev/null; then
    info "保留現有 SkyLab HAProxy 設定"
elif [[ "$HAPROXY_CONFIG_PREEXISTED" == true ]]; then
    error "偵測到既有且非 SkyLab 管理的 HAProxy 設定，已停止避免覆寫"
else
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
fi

systemctl enable haproxy
systemctl restart haproxy
info "haproxy 安裝完成"

# =============================================================================
# 2. Traefik
# =============================================================================
section "安裝 Traefik v${TRAEFIK_VERSION}"

TRAEFIK_URL="https://github.com/traefik/traefik/releases/download/v${TRAEFIK_VERSION}/traefik_v${TRAEFIK_VERSION}_linux_${ARCH}.tar.gz"
TMP_DIR=$(mktemp -d)
curl -fsSL "$TRAEFIK_URL" -o "$TMP_DIR/traefik.tar.gz"
tar xzf "$TMP_DIR/traefik.tar.gz" -C "$TMP_DIR" traefik
mv "$TMP_DIR/traefik" /usr/local/bin/traefik
chmod +x /usr/local/bin/traefik
rm -rf "$TMP_DIR"

# 設定目錄
mkdir -p /etc/traefik/dynamic /etc/traefik/env
touch /etc/traefik/acme.json
chmod 600 /etc/traefik/acme.json

if [[ ! -f /etc/traefik/env/SkyLab.env ]]; then
cat > /etc/traefik/env/SkyLab.env << 'TRAEFIK_ENV_EOF'
# SkyLab 自動管理，供 Traefik dnsChallenge 使用
# 實際值會在 admin/domains 設定 Cloudflare Token 後由後端覆寫
CF_DNS_API_TOKEN=""
TRAEFIK_ENV_EOF
fi
chmod 600 /etc/traefik/env/SkyLab.env

# 靜態設定
if [[ ! -s /etc/traefik/traefik.yml ]]; then
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
else
    info "保留現有 Traefik 靜態設定"
fi

# 初始 dynamic config（空）
if [[ ! -s /etc/traefik/dynamic/SkyLab.yml ]]; then
cat > /etc/traefik/dynamic/SkyLab.yml << 'DYNAMIC_EOF'
# SkyLab 自動管理的反向代理設定
# 此檔案由 SkyLab 透過 SSH 自動維護，請勿手動修改
http:
  routers: {}
  services: {}
DYNAMIC_EOF
else
    info "保留現有 Traefik 動態設定"
fi

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
systemctl restart traefik
info "Traefik 安裝完成"

# =============================================================================
# 3. WireGuard + nftables ACL / SNAT
# =============================================================================
section "安裝 WireGuard 資料平面"

install -d -m 700 "$WG_DIR"
install -d -m 755 "$NFT_DIR" "$WG_OVERRIDE_DIR"

# 重跑安裝器時沿用原有伺服器私鑰，避免所有 Desktop peer 失效。
if [[ ! -s "${WG_DIR}/server_private.key" ]]; then
    umask 077
    wg genkey >"${WG_DIR}/server_private.key"
fi
wg pubkey <"${WG_DIR}/server_private.key" >"${WG_DIR}/server_public.key"
private_key="$(<"${WG_DIR}/server_private.key")"

umask 077
cat >"$WG_CONFIG" <<EOF
${MANAGED_WG_MARKER}
[Interface]
Address = ${WG_ADDRESS}
ListenPort = ${WG_LISTEN_PORT}
PrivateKey = ${private_key}
SaveConfig = false
EOF
chmod 600 "$WG_CONFIG" "${WG_DIR}/server_private.key" "${WG_DIR}/server_public.key"
unset private_key

cat >"$NFT_CONFIG" <<EOF
destroy table inet campus_cloud_wg

table inet campus_cloud_wg {
    set allowed_tcp {
        type ipv4_addr . ipv4_addr . inet_service
        flags timeout
        timeout ${WG_ACL_TIMEOUT}
        gc-interval 5m
        comment "Authorized WireGuard client, VM and TCP port tuples"
    }

    chain forward_guard {
        type filter hook forward priority -10; policy accept;
        iifname "${WG_INTERFACE}" ip saddr ${WG_CLIENT_SUBNET} ip daddr ${WG_VM_SUBNET} ip saddr . ip daddr . tcp dport @allowed_tcp counter accept
        iifname "${WG_INTERFACE}" counter drop
    }

    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        ip saddr ${WG_CLIENT_SUBNET} ip daddr ${WG_VM_SUBNET} oifname "${WG_VM_INTERFACE}" counter snat ip to ${WG_SNAT_ADDRESS}
    }
}
EOF
chmod 600 "$NFT_CONFIG"
nft --check --file "$NFT_CONFIG"

cat >"$FIREWALL_UNIT" <<EOF
[Unit]
Description=Campus Cloud WireGuard nftables policy
After=network-online.target ufw.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft --file ${NFT_CONFIG}
ExecReload=/usr/sbin/nft --file ${NFT_CONFIG}
ExecStop=-/usr/sbin/nft destroy table inet campus_cloud_wg

[Install]
WantedBy=multi-user.target
EOF

cat >"$WG_OVERRIDE" <<EOF
[Unit]
Requires=campus-cloud-wg-firewall.service
After=campus-cloud-wg-firewall.service
BindsTo=campus-cloud-wg-firewall.service
EOF

cat >/etc/sysctl.d/90-campus-cloud-wireguard.conf <<EOF
# Campus Cloud WireGuard gateway forwarding
net.ipv4.ip_forward = 1
EOF
sysctl -w net.ipv4.ip_forward=1 >/dev/null

# 既有 UFW 規則保持不動；全新主機才建立最小安全基線。
ufw_was_active=false
if ufw status | grep -Fq "Status: active"; then
    ufw_was_active=true
else
    warn "UFW 尚未啟用，將先允許 SSH、HTTP、HTTPS 與 WireGuard 再啟用"
    ufw default deny incoming
    ufw default allow outgoing
    ssh_ports="$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2}' | sort -u || true)"
    [[ -n "$ssh_ports" ]] || ssh_ports="22"
    while read -r ssh_port; do
        [[ -n "$ssh_port" ]] && ufw allow "${ssh_port}/tcp" comment "SSH"
    done <<<"$ssh_ports"
    ufw allow 80/tcp comment "HTTP"
    ufw allow 443/tcp comment "HTTPS"
fi

if ! ufw status | grep -Fq "${WG_LISTEN_PORT}/udp on ${WG_INGRESS_INTERFACE}"; then
    ufw allow in on "$WG_INGRESS_INTERFACE" to any port "$WG_LISTEN_PORT" \
        proto udp comment "Campus Cloud WireGuard"
fi
if ! ufw status | grep -Fq "Campus Cloud WireGuard routed traffic"; then
    ufw route allow in on "$WG_INTERFACE" out on "$WG_VM_INTERFACE" \
        from "$WG_CLIENT_SUBNET" to "$WG_VM_SUBNET" \
        comment "Campus Cloud WireGuard routed traffic after nft ACL"
fi
if [[ "$ufw_was_active" == false ]]; then
    ufw --force enable
fi

systemctl daemon-reload
systemd-analyze verify campus-cloud-wg-firewall.service "wg-quick@${WG_INTERFACE}.service"
systemctl enable --now campus-cloud-wg-firewall.service
systemctl enable --now "wg-quick@${WG_INTERFACE}.service"

systemctl is-active --quiet campus-cloud-wg-firewall.service
systemctl is-active --quiet "wg-quick@${WG_INTERFACE}.service"
systemctl is-active --quiet ssh
info "WireGuard 安裝完成（${WG_INTERFACE} / UDP ${WG_LISTEN_PORT}）"

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

cat <<SUMMARY_EOF

┌─────────────────────────────────────────────────────────────────┐
│              SkyLab Gateway VM 安裝完成                    │
├─────────────────────────────────────────────────────────────────┤
│  服務          狀態      設定檔                                  │
│  haproxy       ✅ 運行   /etc/haproxy/haproxy.cfg               │
│  traefik       ✅ 運行   /etc/traefik/traefik.yml               │
│  WireGuard     ✅ 運行   /etc/wireguard/${WG_INTERFACE}.conf                │
│  WG ACL/SNAT   ✅ 運行   /etc/nftables.d/campus-cloud-wg.nft   │
├─────────────────────────────────────────────────────────────────┤
│  後續步驟：                                                      │
│  1. 將 UDP ${WG_LISTEN_PORT} 轉送到此 Gateway 的 ${WG_INGRESS_INTERFACE}                      │
│  2. 在 Backend 設定 WIREGUARD_ENDPOINT_HOST                    │
│  3. 回到 SkyLab 管理介面填入此 VM 的 IP                         │
│  4. 點擊「測試連線」確認 SSH 連線正常                           │
├─────────────────────────────────────────────────────────────────┤
│  常用指令：                                                      │
│  systemctl status haproxy traefik wg-quick@${WG_INTERFACE}                  │
│  systemctl status campus-cloud-wg-firewall                       │
│  wg show ${WG_INTERFACE}                                                     │
└─────────────────────────────────────────────────────────────────┘

SUMMARY_EOF

echo "  備份：${backup}"
echo "  WireGuard：${WG_INTERFACE} (${WG_ADDRESS})"
echo "  監聽：${WG_INGRESS_INTERFACE}/udp/${WG_LISTEN_PORT}"
echo "  Public key：$(<"${WG_DIR}/server_public.key")"
echo ""
