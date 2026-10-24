// Manual page: manual/gateway.md
import { D, H, MIN, ago, later } from "../lib/mock.mjs";
import { GATEWAY_PUBLIC_IP, SUBNET } from "../mocks/network.mjs";

const PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK7pV3f0Qm2xYd8sRz4LwHq9cNbT1eJ5uGv6FhXo2Ak3 skylab@platform";

const INSTALL_LOG_RUNNING = [
  "[2026-10-12 10:00:03] SkyLab gateway installer v2.4",
  "[2026-10-12 10:00:03] Detected Debian GNU/Linux 13 (trixie)",
  "[2026-10-12 10:00:04] Backing up existing configuration to /root/skylab-backups/20261012-100004",
  "[2026-10-12 10:00:05] apt-get update",
  "[2026-10-12 10:00:21] Installing nginx, nginx-extras, wireguard-tools, ufw",
  "[2026-10-12 10:01:02] Writing /etc/nginx/nginx.conf",
  "[2026-10-12 10:01:02] nginx -t: syntax is ok, test is successful",
  "[2026-10-12 10:01:03] Creating WireGuard interface wg-skylab (listen 51821/udp)",
  "[2026-10-12 10:01:04] Enabling IPv4 forwarding and SNAT via 10.20.0.2 on ens19",
  "[2026-10-12 10:01:05] Installing node_exporter and nginx-prometheus-exporter",
].join("\n");
const INSTALL_LOG_DONE = INSTALL_LOG_RUNNING + "\n" + [
  "[2026-10-12 10:01:38] UFW: allow 22/tcp (SSH first)",
  "[2026-10-12 10:01:38] UFW: allow 80,443/tcp, 51821/udp, 30000:39999/tcp+udp",
  "[2026-10-12 10:01:39] UFW enabled",
  "[2026-10-12 10:01:40] systemctl restart nginx wg-quick@wg-skylab",
  "[2026-10-12 10:01:42] Done. Exit code 0",
].join("\n");

function installStatus(state) {
  const installed = state === "succeeded";
  return {
    state, root_access: true, exit_code: installed ? 0 : null,
    started_at: state === "idle" ? null : ago(2 * MIN), finished_at: installed ? ago(0) : null,
    os_name: "Debian GNU/Linux 13 (trixie)",
    interfaces: [{ name: "ens18", addresses: [`${GATEWAY_PUBLIC_IP}/24`] }, { name: "ens19", addresses: [`${SUBNET.gateway_vm_ip}/24`] }],
    components: { nginx: installed, wireguard: installed, ufw: true },
    log: state === "idle" ? "" : state === "running" ? INSTALL_LOG_RUNNING : INSTALL_LOG_DONE,
    defaults: { ingress_interface: "ens18", vm_interface: "ens19", snat_address: SUBNET.gateway_vm_ip, listen_port: 51821, forward_port_start: 30000, forward_port_end: 39999, monitoring_allow_from: [SUBNET.platform_ip] },
    wireguard_interface: "wg-skylab", wireguard_client_subnet: "10.250.0.0/22", wireguard_vm_subnet: SUBNET.cidr,
  };
}

const NGINX_CONF = `user www-data;
worker_processes auto;
pid /run/nginx.pid;
error_log /var/log/nginx/error.log;
include /etc/nginx/modules-enabled/*.conf;

events {
    worker_connections 4096;
}

http {
    sendfile on;
    tcp_nopush on;
    types_hash_max_size 2048;
    server_tokens off;
    client_max_body_size 512m;

    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    access_log /var/log/nginx/access.log;
    gzip on;

    # SkyLab-generated domain rules (do not edit by hand)
    include /etc/nginx/skylab/http.conf;
}

stream {
    # SkyLab-generated port forwarding rules
    include /etc/nginx/skylab/stream.conf;
}
`;

const NGINX_STATUS = `● nginx.service - A high performance web server and a reverse proxy server
     Loaded: loaded (/usr/lib/systemd/system/nginx.service; enabled; preset: enabled)
     Active: active (running) since Mon 2026-10-12 09:32:15 CST; 27min ago
   Main PID: 1184 (nginx)
      Tasks: 5 (limit: 9434)
     Memory: 18.6M (peak: 21.2M)`;

const NGINX_LOGS = [
  "Oct 12 09:32:15 skylab-gw systemd[1]: Starting nginx.service - A high performance web server and a reverse proxy server...",
  "Oct 12 09:32:15 skylab-gw systemd[1]: Started nginx.service - A high performance web server and a reverse proxy server.",
  "Oct 12 09:41:07 skylab-gw systemd[1]: Reloading nginx.service - A high performance web server and a reverse proxy server...",
  "Oct 12 09:41:07 skylab-gw nginx[2210]: 2026/10/12 09:41:07 [notice] 2210#2210: signal process started",
  "Oct 12 09:41:07 skylab-gw systemd[1]: Reloaded nginx.service - A high performance web server and a reverse proxy server.",
  "Oct 12 09:55:42 skylab-gw systemd[1]: Reloading nginx.service - A high performance web server and a reverse proxy server...",
  "Oct 12 09:55:42 skylab-gw nginx[2398]: 2026/10/12 09:55:42 [notice] 2398#2398: signal process started",
  "Oct 12 09:55:42 skylab-gw systemd[1]: Reloaded nginx.service - A high performance web server and a reverse proxy server.",
].join("\n");

const WG_STATUS = `● wg-quick@wg-skylab.service - WireGuard via wg-quick(8) for wg-skylab
     Loaded: loaded (/usr/lib/systemd/system/wg-quick@.service; enabled; preset: enabled)
     Active: active (exited) since Mon 2026-10-12 09:32:16 CST; 27min ago`;
const WG_LOGS = [
  "Oct 12 09:32:16 skylab-gw systemd[1]: Starting wg-quick@wg-skylab.service - WireGuard via wg-quick(8) for wg-skylab...",
  "Oct 12 09:32:16 skylab-gw wg-quick[1190]: [#] ip link add wg-skylab type wireguard",
  "Oct 12 09:32:16 skylab-gw wg-quick[1190]: [#] wg setconf wg-skylab /dev/fd/63",
  "Oct 12 09:32:16 skylab-gw wg-quick[1190]: [#] ip -4 address add 10.250.0.1/22 dev wg-skylab",
  "Oct 12 09:32:16 skylab-gw wg-quick[1190]: [#] ip link set mtu 1420 up dev wg-skylab",
  "Oct 12 09:32:16 skylab-gw systemd[1]: Finished wg-quick@wg-skylab.service - WireGuard via wg-quick(8) for wg-skylab.",
].join("\n");

function gatewayMocks(m) {
  const st = m.state;
  st.install = st.install ?? "idle";
  st.cert = st.cert ?? { ssl_certificate_path: "", ssl_certificate_key_path: "" };
  st.platform = st.platform ?? { enabled: false, domain: "", upstream_host: "", upstream_port: 8082, enable_https: true, dns_proxied: false };
  const certPublic = () => ({ ...st.cert, configured: Boolean(st.cert.ssl_certificate_path), gateway_ready: true });
  const platformPublic = () => ({ ...st.platform, updated_at: ago(D), gateway_ready: true, certificate_configured: true, gateway_host: SUBNET.gateway_vm_ip, dns_managed: st.platform.domain.endsWith("ntubcloud.tw") });
  m.use([
    ["GET", /^\/api\/v1\/gateway\/config$/, () => ({ host: SUBNET.gateway_vm_ip, ssh_port: 22, ssh_user: "skylab", public_key: PUBKEY, is_configured: true })],
    ["PUT", /^\/api\/v1\/gateway\/config$/, ({ body }) => ({ ...body, public_key: PUBKEY, is_configured: true })],
    ["POST", /^\/api\/v1\/gateway\/test-connection$/, () => ({ success: true, message: m.L("SSH connection to 10.20.0.2 succeeded (Debian GNU/Linux 13)", "已成功以 SSH 連線到 10.20.0.2（Debian GNU/Linux 13）") })],
    ["POST", /^\/api\/v1\/gateway\/reset-host-key$/, () => ({ message: m.L("Host key cleared", "已清除 Host Key") })],
    ["GET", /^\/api\/v1\/gateway\/install$/, () => installStatus(st.install)],
    ["POST", /^\/api\/v1\/gateway\/install$/, () => { st.install = "running"; return installStatus("running"); }],
    ["POST", /^\/api\/v1\/reverse-proxy\/rules\/sync$/, () => ({ message: m.L("Synced 4 domain rules and 6 port forwards to the Gateway", "已同步 4 條網域規則與 6 條 Port 轉發到 Gateway") })],
    ["GET", /^\/api\/v1\/gateway\/certificate$/, () => certPublic()],
    ["PUT", /^\/api\/v1\/gateway\/certificate$/, ({ body }) => { st.cert = { ...body }; return certPublic(); }],
    ["GET", /^\/api\/v1\/gateway\/certificate\/status$/, () => st.cert.ssl_certificate_path
      ? { configured: true, cert_readable: true, key_readable: true, cert_valid: true, key_valid: true, key_matches: true, expires_at: later(78 * D),
          dns_names: ["ntubcloud.tw", "*.ntubcloud.tw"], covered_domains: ["portal.ntubcloud.tw", "jupyter-alex.ntubcloud.tw", "course.ntubcloud.tw"], uncovered_domains: [], checked_at: ago(0) }
      : { configured: false, dns_names: [], covered_domains: [], uncovered_domains: [], checked_at: ago(0) }],
    ["GET", /^\/api\/v1\/gateway\/platform-entry$/, () => platformPublic()],
    ["PUT", /^\/api\/v1\/gateway\/platform-entry$/, ({ body }) => { st.platform = { ...body }; return platformPublic(); }],
    ["POST", /^\/api\/v1\/gateway\/platform-entry\/test-upstream$/, ({ body }) => ({ reachable: true, detail: m.L(`The Gateway reached http://${body.upstream_host}:${body.upstream_port} (HTTP 200, 12 ms)`, `Gateway 連得到 http://${body.upstream_host}:${body.upstream_port}（HTTP 200，12 ms）`) })],
    ["GET", /^\/api\/v1\/gateway\/platform-entry\/status$/, () => st.platform.enabled
      ? { applied: true, applied_domain: st.platform.domain, applied_upstream: `${st.platform.upstream_host}:${st.platform.upstream_port}`, applied_https: true,
          certificate: "/etc/ssl/skylab/fullchain.pem", certificate_ready: true, certificate_expires_at: later(78 * D), certificate_matches_domain: true,
          upstream_reachable: true, upstream_detail: "HTTP 200", observed_client_ip: "140.131.78.45", observed_scheme: "https",
          dns_record: `A ${GATEWAY_PUBLIC_IP}${st.platform.dns_proxied ? " (proxied)" : ""}`, dns_record_ok: true, dns_detail: null, checked_at: ago(0) }
      : { applied: true, checked_at: ago(0) }],
    ["GET", /^\/api\/v1\/gateway\/services\/nginx\/status$/, () => ({ service: "nginx", active: true, status_text: NGINX_STATUS })],
    ["GET", /^\/api\/v1\/gateway\/services\/nginx\/config$/, () => ({ service: "nginx", content: NGINX_CONF })],
    ["PUT", /^\/api\/v1\/gateway\/services\/nginx\/config$/, () => ({ message: "ok" })],
    ["GET", /^\/api\/v1\/gateway\/services\/nginx\/logs$/, () => ({ __text: NGINX_LOGS })],
    ["GET", /^\/api\/v1\/gateway\/services\/wireguard\/status$/, () => ({ service: "wireguard", active: true, status_text: WG_STATUS })],
    ["GET", /^\/api\/v1\/gateway\/services\/wireguard\/logs$/, () => ({ __text: WG_LOGS })],
    ["POST", /^\/api\/v1\/gateway\/services\/(\w+)\/(\w+)$/, ({ mt }) => ({ service: mt[1], action: mt[2], success: true, output: "" })],
    ["GET", /^\/api\/v1\/gateway\/wireguard\/overview$/, () => ({
      mode: "wireguard", interface: "wg-skylab", systemd_unit: "wg-quick@wg-skylab.service", endpoint: `${GATEWAY_PUBLIC_IP}:51821`,
      client_subnet: "10.250.0.0/22", vm_subnet: SUBNET.cidr, session_ttl_seconds: 12 * 3600, reconcile_enabled: true,
      authorized_sessions: 23, expired_sessions: 2, live_peers: 23, recent_handshakes: 17,
      transfer_rx_bytes: 3.4 * 1024 ** 3, transfer_tx_bytes: 18.7 * 1024 ** 3, listen_port: 51821, inspection_available: true, inspected_at: ago(0),
    })],
  ]);
}

const tab = (s, key) => s.page.locator(`[data-guide-tab="gateway-${key}"]`);
const btn = (s, en, zh, exact = false) => s.page.getByRole("button", { name: s.t(en, zh), exact });
const card = (s, en, zh) => s.page.locator('[class*="_card_"]', { has: s.page.locator("h2", { hasText: s.t(en, zh) }) }).first();
const field = (s, en, zh) => s.page.locator("label", { hasText: s.t(en, zh) }).first();
const toastOf = async (s) => {
  const t = s.page.locator("[data-sonner-toast]").first();
  await t.waitFor({ timeout: 15000 });
  await s.page.waitForTimeout(500);
  return t;
};
// Crop: the page content right of the sidebar, full window height.
const C = (s) => ({ crop: { x: 340, y: 0, width: 1100, height: s.page.viewportSize().height } });
const tall = (s, height) => s.page.setViewportSize({ width: 1440, height });
// Waits until earlier toasts are gone (removing sonner's nodes by hand crashes React).
const removeToasts = (s) => s.page.waitForFunction(() => !document.querySelector("[data-sonner-toast]"), null, { timeout: 12000 }).catch(() => {});
// Crop: the page content from the top of the window down to the bottom of `loc`.
const topTo = async (s, loc, pad = 20) => {
  const b = await loc.boundingBox();
  return { crop: { x: 340, y: 0, width: 1100, height: Math.ceil(b.y + b.height + pad) } };
};
// Crop: the page content around all of `locs`.
const around = async (s, locs, pad = 16) => {
  const bs = await Promise.all(locs.map((l) => l.boundingBox()));
  const y = Math.min(...bs.map((b) => b.y)) - pad;
  const bottom = Math.max(...bs.map((b) => b.y + b.height)) + pad;
  return { crop: { x: 340, y: Math.floor(y), width: 1100, height: Math.ceil(bottom - y) } };
};
// The centred toast covers the page title and tabs; show the next one just under `loc` instead
// (optionally wider, so a long English message stays on one line, and centred further right).
const toastBelow = async (s, loc, { width, left } = {}) => {
  const b = await loc.boundingBox();
  const css = [`top:${Math.round(b.y + b.height + 12)}px !important`];
  if (width) css.push(`--width:${width}px !important`);
  if (left) css.push(`left:${left}px !important`);
  await s.page.addStyleTag({ content: `[data-sonner-toaster]{${css.join(";")}}` });
};

export default {
  page: "gateway",
  role: "admin",
  mocks(m) { gatewayMocks(m); },
  sections: [
    // s01 Connect to the Gateway over SSH
    async (s) => {
      await s.go("/gateway", { wait: 800 });
      await s.shot(1, tab(s, "connection"), { crop: { x: 340, y: 0, width: 1100, height: 490 } });
      await s.shot(2, [field(s, "Host / IP", "Host / IP"), field(s, "SSH Port", "SSH Port"), field(s, "SSH User", "SSH 使用者"), btn(s, "Save Settings", "儲存設定")], { crop: { x: 340, y: 0, width: 1100, height: 490 } });
      await s.shot(3, [btn(s, "Regenerate Keypair", "重新產生 Keypair"), s.page.locator("pre").first()], { crop: { x: 340, y: 0, width: 1100, height: 490 } });
      await s.shot(4, btn(s, "Copy", "複製", true), { crop: { x: 340, y: 0, width: 1100, height: 490 } });
      await toastBelow(s, card(s, "SSH Connection Settings", "SSH 連線設定"));
      await btn(s, "Test Connection", "測試連線").click();
      const t = await toastOf(s);
      await s.shot(5, [btn(s, "Test Connection", "測試連線"), t], { ...(await topTo(s, card(s, "SSH Public Key", "SSH 公鑰"))), newFrame: true });
    },
    // s02 Reset the host key after reinstalling the Gateway
    async (s) => {
      await s.go("/gateway", { wait: 800 });
      const reset = btn(s, "Reset Host Key", "重設 Host Key");
      await s.shot(1, reset, { crop: { x: 340, y: 0, width: 1100, height: 490 } });
      await s.click(reset, { wait: 500 });
      const confirm = s.page.getByRole("alertdialog").or(s.page.getByRole("dialog")).last();
      await s.shot(2, confirm.getByRole("button", { name: s.t("Reset", "重設"), exact: true }));
    },
    // s03 Install the Gateway services
    async (s) => {
      await tall(s, 1250);
      await s.go("/gateway", { wait: 800 });
      const t = tab(s, "install");
      await s.click(t, { wait: 900 });
      const status = card(s, "Install services", "安裝服務");
      const opts = card(s, "Install options", "安裝參數");
      const top = await topTo(s, opts);
      await s.shot(1, t, top);
      await s.shot(2, [status.locator('[class*="_statusRow_"]'), status.locator('[class*="_componentRow_"]'), status.locator("dl")], top);
      await s.shot(3, [opts.locator('[class*="_installGrid_"]'), btn(s, "Use detected values", "套用偵測值")], top);
      await s.click(btn(s, "One-click install", "一鍵安裝"), { wait: 500 });
      const confirm = s.page.getByRole("alertdialog").or(s.page.getByRole("dialog")).last();
      await s.shot(4, confirm.getByRole("button", { name: s.t("Start install", "開始安裝") }));
      await s.click(confirm.getByRole("button", { name: s.t("Start install", "開始安裝") }), { wait: 300 });
      await removeToasts(s);
      const log = card(s, "Install log", "安裝日誌");
      await log.scrollIntoViewIfNeeded();
      await s.shot(5, [status.locator('[class*="_badge_"]').first(), log.locator("pre")], { ...(await topTo(s, log)), scroll: false });
      s.m.state.install = "succeeded";
      await s.page.evaluate(() => window.scrollTo(0, 0));
      await s.page.locator("main, body").first().evaluate(() => document.querySelectorAll("*").forEach((e) => { if (e.scrollTop) e.scrollTop = 0; }));
      // The sync toast lands over the empty middle of the "Install options" heading, under the status card.
      await toastBelow(s, status, { width: 440, left: 800 });
      await btn(s, "Refresh", "重新整理").first().click();
      const done = s.page.locator("[data-sonner-toast]").first();
      await done.waitFor({ timeout: 15000 });
      await s.page.waitForTimeout(800);
      await s.shot(6, [btn(s, "Sync rules to Gateway", "同步規則到 Gateway"), status.locator('[class*="_badge_"]').first()], { ...(await topTo(s, done)), scroll: false, newFrame: true });
    },
    // s04 Set up the HTTPS certificate
    async (s) => {
      await s.go("/gateway", { wait: 800 });
      const t = tab(s, "certificate");
      await s.click(t, { wait: 900 });
      const form = card(s, "HTTPS certificate", "HTTPS 憑證");
      await s.shot(1, form.locator('[class*="_securityNote_"]'), { crop: { x: 340, y: 0, width: 1100, height: 580 } });
      await s.shot(2, t, { crop: { x: 340, y: 0, width: 1100, height: 580 } });
      await s.fill(field(s, "Certificate path", "憑證路徑").locator("input"), "/etc/ssl/skylab/fullchain.pem");
      await s.fill(field(s, "Private key path", "私鑰路徑").locator("input"), "/etc/ssl/skylab/privkey.pem");
      await s.shot(3, [field(s, "Certificate path", "憑證路徑"), field(s, "Private key path", "私鑰路徑"), btn(s, "Save and apply", "儲存並套用")], { crop: { x: 340, y: 0, width: 1100, height: 580 } });
      await btn(s, "Save and apply", "儲存並套用").click();
      await s.page.locator("[data-sonner-toast]").first().waitFor({ timeout: 15000 });
      await s.settle(600);
      await removeToasts(s);
      const st = card(s, "Certificate on the Gateway", "Gateway 上的憑證");
      await s.shot(4, [st.locator("dl"), st.locator('[class*="_successNote_"], [class*="_warningNote_"]').first()], await topTo(s, st));
    },
    // s05 Serve the platform through the Gateway (platform entry)
    async (s) => {
      await tall(s, 1650);
      await s.go("/gateway", { wait: 800 });
      const t = tab(s, "platform");
      await s.click(t, { wait: 900 });
      const chk = (en, zh) => s.page.locator("label", { hasText: s.t(en, zh) }).first();
      await chk("Serve the SkyLab platform", "讓 SkyLab 主系統").locator("input").check();
      await s.fill(field(s, "Platform domain", "主系統網域").locator("input"), "skylab.ntubcloud.tw");
      await s.fill(field(s, "Platform address", "主系統位址").locator("input"), SUBNET.platform_ip);
      // The form card only; the status cards below get their own frame for step 8.
      const form = card(s, "Platform entry", "平台入口");
      const top1 = await topTo(s, form);
      await s.shot(1, t, top1);
      await s.shot(2, chk("Serve the SkyLab platform", "讓 SkyLab 主系統"), top1);
      await s.shot(3, [field(s, "Platform domain", "主系統網域"), field(s, "Platform address", "主系統位址"), field(s, "Platform port", "主系統 Port")], top1);
      await s.shot(4, chk("Enable HTTPS", "啟用 HTTPS"), top1);
      await chk("Proxy through Cloudflare", "經由 Cloudflare Proxy").locator("input").check();
      await s.click(btn(s, "Test upstream", "測試上游"), { wait: 600 });
      const top2 = await topTo(s, form);
      await s.shot(5, chk("Proxy through Cloudflare", "經由 Cloudflare Proxy"), top2);
      await s.shot(6, [btn(s, "Test upstream", "測試上游"), s.page.locator('[class*="_successNote_"]').first()], top2);
      await s.shot(7, btn(s, "Save and apply", "儲存並套用"), top2);
      await btn(s, "Save and apply", "儲存並套用").click();
      await s.page.locator("[data-sonner-toast]").first().waitFor({ timeout: 15000 });
      await s.settle(800);
      await removeToasts(s);
      const st = card(s, "Applied state on the Gateway", "Gateway 套用狀態");
      const todo = card(s, "Still to do by hand", "啟用後還要手動完成");
      await todo.scrollIntoViewIfNeeded();
      await s.shot(8, [st.locator("dl"), todo.locator("ul")], { ...(await around(s, [st, todo])), scroll: false });
    },
    // s06 Manage nginx and edit its main configuration
    async (s) => {
      await s.go("/gateway", { wait: 800 });
      const t = tab(s, "nginx");
      await s.click(t, { wait: 2500 });
      await s.page.locator(".monaco-editor").first().waitFor({ timeout: 20000 });
      await s.settle(800);
      const head = s.page.locator('[class*="_areaStatus_"]').first();
      await s.shot(1, t, C(s));
      await s.shot(2, [head.locator('[class*="_badge_"]').first(), head.locator('[class*="_cardHeadActions_"]')], C(s));
      // Type a change into the editor: raise worker_connections.
      await s.page.locator(".monaco-editor .view-lines").first().click({ position: { x: 300, y: 8 } });
      await s.page.keyboard.press("Control+End");
      await s.page.keyboard.type("\n# Allow larger uploads for the course file share\n");
      await s.settle(500);
      const editor = s.page.locator('[class*="_areaEditor_"]').first();
      await s.shot(3, [btn(s, "Write Config", "寫入設定檔"), editor.getByText(s.t("● Unsaved", "● 未寫入"))], C(s));
      await s.shot(4, head.getByRole("button", { name: "Reload" }), C(s));
      const logs = card(s, "Service Logs", "服務日誌");
      await logs.scrollIntoViewIfNeeded();
      await s.shot(5, logs.locator("pre"), { ...C(s), scroll: false });
    },
    // s07 Check the WireGuard VPN
    async (s) => {
      await tall(s, 1400);
      await s.go("/gateway", { wait: 800 });
      const t = tab(s, "wireguard");
      await s.click(t, { wait: 1200 });
      const head = card(s, "WireGuard VPN", "WireGuard VPN");
      const logs = card(s, "Service Logs", "服務日誌");
      // Down to the logs card: the window is tall enough that nothing scrolls.
      const top = await topTo(s, logs);
      await s.shot(1, t, top);
      await s.shot(2, [head.locator('[class*="_badge_"]').first(), head.locator('[class*="_cardHeadActions_"]')], top);
      await s.shot(3, [s.page.locator('[class*="_wireguardMetrics_"]'), s.page.locator('[class*="_warningNote_"]').first()], top);
      const sum = card(s, "VPN configuration summary", "VPN 設定摘要");
      await s.shot(4, sum.locator("dl"), { ...top, scroll: false });
      await s.shot(5, logs.locator("pre"), { ...top, scroll: false });
    },
  ],
};
