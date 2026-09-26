#!/usr/bin/env bash
# =============================================================================
# SkyLab - Gateway 主機安裝腳本
# 支援系統：Debian 12 / 13
# 安裝服務：nginx（Port 轉發 stream + 網域反向代理 http）+ certbot（Let's Encrypt，
#           Cloudflare DNS-01）+ WireGuard + nftables ACL / SNAT
# =============================================================================

set -euo pipefail
export LC_ALL=C

# ── 接受 SkyLab 公鑰參數 ────────────────────────────────────────────────
# 用法：bash install.sh "<ssh-ed25519 AAAA...>"
# 若提供公鑰，自動寫入 /root/.ssh/authorized_keys
skylab_PUBKEY="${1:-}"

# ── Port 轉發設定（可用同名環境變數覆寫）──────────────────────────────────
# 對外 port 自動配號池（與 SkyLab「IP 管理」子網設定的 forward_port_start/end 一致），
# UFW 會放行這段 TCP/UDP；手動填其他 port 的轉發要自己再 ufw allow
FORWARD_PORT_RANGE="${FORWARD_PORT_RANGE:-30000:39999}"

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
NGINX_DIR="/etc/nginx"
NGINX_CONF="${NGINX_DIR}/nginx.conf"
NGINX_MANAGED_DIR="${NGINX_DIR}/skylab"
NGINX_MARKER="# SkyLab managed nginx.conf"
NGINX_CONFIG_PREEXISTED=false
if [[ -s "$NGINX_CONF" ]]; then
    NGINX_CONFIG_PREEXISTED=true
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
    etc/nginx etc/letsencrypt etc/haproxy etc/traefik \
    etc/wireguard etc/nftables.d etc/ufw \
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

apt-get install -y -qq \
    nginx libnginx-mod-stream certbot python3-certbot-dns-cloudflare \
    wireguard-tools nftables ufw

# Debian 的全域 nftables.service 可能載入含 `flush ruleset` 的規則；SkyLab
# 使用自己的獨立 unit，避免清除 UFW、NetBird 或其他既有服務的規則。
systemctl disable --now nftables.service >/dev/null 2>&1 || true

for command in wg nft ufw nginx certbot openssl; do
    command -v "$command" >/dev/null || error "缺少必要指令：${command}"
done

# =============================================================================
# 1. nginx（Port 轉發 + 網域反向代理）+ certbot
# =============================================================================
section "安裝 nginx"

# 舊版 Gateway 裝過 haproxy / traefik：它們佔著 80/443 與轉發 port，nginx 起不來，
# 先停掉並取消開機啟動（設定檔留在原位、已列入本次備份）。
for legacy in haproxy traefik; do
    if systemctl list-unit-files "${legacy}.service" 2>/dev/null | grep -q "^${legacy}.service"; then
        warn "停用舊的 ${legacy} 服務（已被 nginx 取代）"
        systemctl disable --now "${legacy}.service" >/dev/null 2>&1 || true
    fi
done

nginx -V 2>&1 | grep -q -- "--with-stream" \
    || error "這個 nginx 沒有 stream 模組，無法做 Port 轉發（需要 libnginx-mod-stream 或 nginx-full）"

install -d -m 755 "$NGINX_MANAGED_DIR"

# nginx.conf 由 SkyLab 持有：初次安裝寫入；重跑時保留；若是別人改過的設定則停下來。
# Debian 剛裝好、沒動過的預設 nginx.conf 可以直接覆寫（dpkg -V 查不到修改紀錄）。
if grep -Fq "$NGINX_MARKER" "$NGINX_CONF" 2>/dev/null; then
    info "保留現有 SkyLab nginx.conf"
elif [[ "$NGINX_CONFIG_PREEXISTED" == true ]] \
    && dpkg -V nginx-common 2>/dev/null | grep -q "${NGINX_CONF}$"; then
    error "偵測到既有且非 SkyLab 管理的 nginx.conf，已停止避免覆寫"
else
    cat > "$NGINX_CONF" << 'NGINX_EOF'
# SkyLab managed nginx.conf
# 此檔案由 SkyLab Gateway 安裝腳本產生。可以在 SkyLab「閘道」頁面編輯，
# 但 /etc/nginx/skylab/http.conf 與 stream.conf 由 SkyLab 後端自動重建，請勿手動修改。

user www-data;
worker_processes auto;
pid /run/nginx.pid;
error_log /var/log/nginx/error.log;
include /etc/nginx/modules-enabled/*.conf;

events {
    worker_connections 4096;
}

# ── 網域反向代理（domain → VM）─────────────────────────────────────────────
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    server_tokens off;
    access_log /var/log/nginx/access.log;
    # 上傳大小交給後面的 VM 服務自己限制
    client_max_body_size 0;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    # 沒對到任何已發布網域的請求一律關閉連線（443 用安裝時產生的自簽憑證完成握手）
    server {
        listen 80 default_server;
        server_name _;
        return 444;
    }
    server {
        listen 443 ssl default_server;
        server_name _;
        ssl_certificate /etc/nginx/skylab/fallback.crt;
        ssl_certificate_key /etc/nginx/skylab/fallback.key;
        return 444;
    }

    # SkyLab 自動管理：每個對外網址一個 server 區塊
    include /etc/nginx/skylab/http.conf;
}

# ── Port 轉發（對外 port → VM:port，TCP/UDP）──────────────────────────────
stream {
    log_format skylab_stream '$remote_addr [$time_local] $protocol $status '
                             '$bytes_sent $bytes_received $session_time "$upstream_addr"';
    access_log /var/log/nginx/stream.log skylab_stream;

    # SkyLab 自動管理：每條轉發規則一個 server 區塊
    include /etc/nginx/skylab/stream.conf;
}
NGINX_EOF
fi

# Debian 預設站台也監聽 80，會搶走 default_server；SkyLab 不用它
rm -f "${NGINX_DIR}/sites-enabled/default"

# SkyLab 自動管理的兩份設定：初次安裝建空檔（nginx.conf 有 include，缺檔會起不來），
# 重跑時保留後端已同步的內容
for managed in http.conf stream.conf; do
    if [[ ! -f "${NGINX_MANAGED_DIR}/${managed}" ]]; then
        printf '# SkyLab 自動管理的設定，請勿手動修改\n' > "${NGINX_MANAGED_DIR}/${managed}"
    fi
done

# 自簽備援憑證：給 443 的 default_server，以及 Let's Encrypt 還沒簽下來的網域先頂著用
if [[ ! -s "${NGINX_MANAGED_DIR}/fallback.crt" || ! -s "${NGINX_MANAGED_DIR}/fallback.key" ]]; then
    openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -subj "/CN=skylab-gateway" \
        -keyout "${NGINX_MANAGED_DIR}/fallback.key" \
        -out "${NGINX_MANAGED_DIR}/fallback.crt" >/dev/null 2>&1
    chmod 600 "${NGINX_MANAGED_DIR}/fallback.key"
fi

# certbot：Cloudflare token 由 SkyLab 後端在第一次同步 HTTPS 網域時寫入
# /etc/letsencrypt/skylab-cloudflare.ini；Debian 的 certbot.timer 會自動續期，
# 續期後由 deploy hook reload nginx
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/skylab-nginx-reload << 'HOOK_EOF'
#!/bin/sh
# SkyLab：Let's Encrypt 憑證續期後重新載入 nginx
systemctl reload nginx
HOOK_EOF
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/skylab-nginx-reload
systemctl enable certbot.timer >/dev/null 2>&1 || true

nginx -t
systemctl enable nginx
systemctl restart nginx
info "nginx 安裝完成（stream + http，certbot 續期 hook 已就緒）"

# =============================================================================
# 2. WireGuard + nftables ACL / SNAT
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
# Port 轉發配號池：nginx stream 在這段 port 監聽，外面連不進來轉發就沒用
for proto in tcp udp; do
    if ! ufw status | grep -Fq "${FORWARD_PORT_RANGE}/${proto}"; then
        ufw allow "${FORWARD_PORT_RANGE}/${proto}" comment "SkyLab port forwarding"
    fi
done
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
# 3. SkyLab SSH 公鑰（若有提供則自動寫入）
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
│              SkyLab Gateway 安裝完成                       │
├─────────────────────────────────────────────────────────────────┤
│  服務          狀態      設定檔                                  │
│  nginx         ✅ 運行   /etc/nginx/nginx.conf                  │
│    Port 轉發             /etc/nginx/skylab/stream.conf（自動）   │
│    反向代理              /etc/nginx/skylab/http.conf（自動）     │
│  certbot       ⏱ timer   /etc/letsencrypt（Cloudflare DNS-01）  │
│  WireGuard     ✅ 運行   /etc/wireguard/${WG_INTERFACE}.conf                │
│  WG ACL/SNAT   ✅ 運行   /etc/nftables.d/campus-cloud-wg.nft   │
├─────────────────────────────────────────────────────────────────┤
│  後續步驟：                                                      │
│  1. 將 TCP 80/443、TCP+UDP ${FORWARD_PORT_RANGE} 與 UDP ${WG_LISTEN_PORT}      │
│     轉送到此 Gateway 的 ${WG_INGRESS_INTERFACE}                                  │
│  2. 在 Backend 設定 WIREGUARD_ENDPOINT_HOST                    │
│  3. 回到 SkyLab 管理介面填入此主機的 IP                         │
│  4. 點擊「測試連線」確認 SSH 連線正常                           │
├─────────────────────────────────────────────────────────────────┤
│  常用指令：                                                      │
│  systemctl status nginx wg-quick@${WG_INTERFACE}                            │
│  nginx -t && systemctl reload nginx                              │
│  certbot certificates                                            │
│  systemctl status campus-cloud-wg-firewall                       │
│  wg show ${WG_INTERFACE}                                                     │
└─────────────────────────────────────────────────────────────────┘

SUMMARY_EOF

echo "  備份：${backup}"
echo "  WireGuard：${WG_INTERFACE} (${WG_ADDRESS})"
echo "  監聽：${WG_INGRESS_INTERFACE}/udp/${WG_LISTEN_PORT}"
echo "  Public key：$(<"${WG_DIR}/server_public.key")"
echo ""
