// Manual page: manual/domain.md
import { D, H, ago } from "../lib/mock.mjs";
import { escapeRegExp } from "../lib/run.mjs";
import { GATEWAY_PUBLIC_IP, ZONES, firewallMocks, studentTopology } from "../mocks/network.mjs";

const ACCOUNT = "7c41e2b9a05d48f3b6e1d2c3a4f50918";

function records(m, zoneId, published) {
  const zone = ZONES.find((z) => z.id === zoneId) ?? ZONES[0];
  const z = zone.name;
  const r = (id, type, name, content, extra = {}) => ({
    id, zone_id: zone.id, type, name, content, ttl: 1, proxied: false, proxiable: true, comment: null, priority: null, tags: [],
    created_on: ago(40 * D), modified_on: ago(10 * D), managed_by_system: false, managed_vmid: null, ...extra,
  });
  if (zone.name !== "ntubcloud.tw") {
    return [
      r("d1", "A", z, "203.145.220.17", { comment: m.L("Lab website", "實驗室網站") }),
      r("d2", "CNAME", `www.${z}`, z, { proxied: true }),
      r("d3", "TXT", z, "\"v=spf1 include:_spf.google.com ~all\"", { ttl: 3600, proxiable: false, proxied: null }),
    ];
  }
  const list = [
    r("a1", "A", z, GATEWAY_PUBLIC_IP, { comment: m.L("Gateway public IP", "Gateway 對外 IP") }),
    r("a2", "A", `portal.${z}`, GATEWAY_PUBLIC_IP, { managed_by_system: true, managed_vmid: 201, comment: "SkyLab reverse proxy vmid=201" }),
    r("a3", "A", `jupyter-alex.${z}`, GATEWAY_PUBLIC_IP, { managed_by_system: true, managed_vmid: 204, comment: "SkyLab reverse proxy vmid=204" }),
    r("a4", "A", `course.${z}`, GATEWAY_PUBLIC_IP, { managed_by_system: true, managed_vmid: 211, comment: "SkyLab reverse proxy vmid=211" }),
    r("a5", "CNAME", `docs.${z}`, "skylab-docs.pages.dev", { proxied: true, comment: m.L("Documentation site", "文件網站") }),
    r("a6", "CNAME", `www.${z}`, z, { proxied: true }),
    r("a7", "MX", z, "mx1.ntub.edu.tw", { ttl: 3600, priority: 10, proxiable: false, proxied: null }),
    r("a8", "TXT", `_dmarc.${z}`, "\"v=DMARC1; p=quarantine; rua=mailto:dmarc@ntub.edu.tw\"", { ttl: 3600, proxiable: false, proxied: null }),
  ];
  if (published) {
    list.splice(1, 0, r("a9", "A", published.domain, GATEWAY_PUBLIC_IP, { created_on: ago(0), modified_on: ago(0), managed_by_system: true, managed_vmid: published.vmid, comment: `SkyLab reverse proxy vmid=${published.vmid}` }));
  }
  return list;
}

function domainMocks(m, { connected = true } = {}) {
  const st = m.state;
  st.cf = connected
    ? { account_id: ACCOUNT, is_configured: true, has_api_token: true, has_default_dns_target: true, default_dns_target_type: "A", default_dns_target_value: GATEWAY_PUBLIC_IP, updated_at: ago(30 * D), last_verified_at: ago(2 * H) }
    : { account_id: null, is_configured: false, has_api_token: false, has_default_dns_target: false, default_dns_target_type: null, default_dns_target_value: null, updated_at: null, last_verified_at: null };
  const page = (items) => ({ items, page_info: { page: 1, per_page: 50, count: items.length, total_count: items.length, total_pages: 1 } });
  m.use([
    ["GET", /^\/api\/v1\/cloudflare\/config$/, () => st.cf],
    ["PUT", /^\/api\/v1\/cloudflare\/config$/, ({ body }) => {
      st.cf = { ...st.cf, ...body, api_token: undefined, is_configured: true, has_api_token: true, has_default_dns_target: Boolean(body.default_dns_target_value), updated_at: ago(0), last_verified_at: ago(0) };
      return st.cf;
    }],
    ["POST", /^\/api\/v1\/cloudflare\/config\/test$/, () => { st.cf = { ...st.cf, last_verified_at: ago(0) }; return { success: true, message: m.L("Connected successfully: the token is active", "連線成功：Token 有效"), token_status: "active" }; }],
    ["GET", /^\/api\/v1\/cloudflare\/zones$/, () => st.cf.is_configured
      ? page(ZONES.map((z) => ({ ...z, paused: false, type: "full", name_servers: ["ada.ns.cloudflare.com", "rick.ns.cloudflare.com"], created_on: ago(200 * D) })))
      : { __status: 400, __body: { detail: "Cloudflare is not configured" } }],
    ["GET", /^\/api\/v1\/cloudflare\/zones\/([^/]+)\/dns-records$/, ({ mt, query }) => {
      const q = (query.search ?? "").toLowerCase();
      return page(records(m, mt[1], st.published).filter((x) => !q || x.name.includes(q)));
    }],
    ["POST", /^\/api\/v1\/cloudflare\/zones\/([^/]+)\/dns-records$/, ({ body }) => ({ id: "new", ...body })],
    ["PATCH", /^\/api\/v1\/cloudflare\/zones\/([^/]+)\/dns-records\/([^/]+)$/, ({ body }) => ({ id: "x", ...body })],
    ["DELETE", /^\/api\/v1\/cloudflare\/zones\/([^/]+)\/dns-records\/([^/]+)$/, () => ({ message: "ok" })],
  ]);
}

const dlg = (s) => s.page.getByRole("dialog");
const btn = (s, en, zh, exact = false) => s.page.getByRole("button", { name: s.t(en, zh), exact });
// Matched from the start of the label: a plain hasText is a case-insensitive substring, so "Name" would
// hit the Type field, whose select has a CNAME option.
const field = (s, en, zh) => dlg(s).locator("label", { hasText: new RegExp(`^${escapeRegExp(s.t(en, zh))}`) }).first();
const row = (s, name) => s.page.locator('[class*="_row_"]', { has: s.page.locator('[class*="_rowName_"]', { hasText: new RegExp(`^${escapeRegExp(name)}$`) }) }).first();
const waitToast = async (s) => {
  const t = s.page.locator("[data-sonner-toast]").first();
  await t.waitFor({ timeout: 15000 });
  await s.page.waitForTimeout(500);
  return t;
};

export default {
  page: "domain",
  role: "admin",
  mocks(m) { domainMocks(m); },
  sections: [
    // s01 Connect Cloudflare
    {
      mocks(m) { domainMocks(m, { connected: false }); },
      run: async (s) => {
        await s.go("/domain", { wait: 800 });
        await s.shot(1, s.page.getByText(s.t("Cloudflare not connected yet", "尚未連線 Cloudflare")), { min: [720, 300] });
        const open = btn(s, "Connection Settings", "連線設定");
        await s.shot(2, open, { min: [720, 300] });
        await s.click(open, { wait: 600 });
        await s.fill(dlg(s).locator("input").first(), ACCOUNT);
        await s.fill(dlg(s).locator('input[type="password"]').first(), "Yq3mR8vZ0bK2pW7sT5nH1cX9dL4fG6jA");
        await dlg(s).locator("select").selectOption("A");
        await s.fill(dlg(s).locator("input").last(), GATEWAY_PUBLIC_IP);
        await s.shot(3, [field(s, "Account ID", "Account ID"), field(s, "API Token", "API Token")]);
        await s.shot(4, [field(s, "Default DNS Target Type", "預設 DNS Target 類型"), field(s, "Default DNS Target Value", "預設 DNS Target 值")]);
        await s.shot(5, dlg(s).getByRole("button", { name: s.t("Save", "儲存"), exact: true }));
        await s.click(dlg(s).getByRole("button", { name: s.t("Save", "儲存"), exact: true }), { wait: 1200 });
        await s.page.waitForFunction(() => !document.querySelector("[data-sonner-toast]"), null, { timeout: 12000 }).catch(() => {});
        // The centred toast would cover the long English page title: drop it into the gap under the header.
        await s.page.addStyleTag({ content: "[data-sonner-toaster]{top:78px !important}" });
        await btn(s, "Test Connection", "測試連線").click();
        const t = await waitToast(s);
        await s.shot(6, [btn(s, "Test Connection", "測試連線"), t, s.page.locator('[data-guide="domain-status"]')], { crop: { x: 340, y: 0, width: 1100, height: 480 } });
      },
    },
    // s02 Browse the DNS records of a zone
    async (s) => {
      await s.go("/domain", { wait: 1000 });
      const search = s.page.getByPlaceholder(s.t("Search record name, press Enter to search", "搜尋紀錄名稱，Enter 查詢"));
      await s.fill(search, "jupyter");
      await s.shot(1, [s.page.locator('[data-guide="domain-zones"] h2'), s.page.locator('[class*="_zoneList_"]')], { crop: "viewport" });
      await s.shot(2, s.page.locator('[class*="_zoneItemActive_"]'), { crop: "viewport" });
      const portal = row(s, "portal.ntubcloud.tw");
      await s.shot(3, [portal.locator('[class*="_rowMain_"]')], { crop: "viewport" });
      await s.shot(4, [portal.locator('[class*="_badge_"]').first(), row(s, "ntubcloud.tw").locator('[class*="_badge_"]').first(), row(s, "docs.ntubcloud.tw").locator('[class*="_badge_"]').nth(1), portal.locator('[class*="_badge_"]').nth(1)], { crop: "viewport" });
      await s.shot(5, search, { crop: "viewport" });
    },
    // s03 Add a DNS record
    async (s) => {
      await s.go("/domain", { wait: 1000 });
      const add = btn(s, "Add Record", "新增紀錄");
      await s.shot(1, [s.page.locator('[class*="_zoneItemActive_"]'), add]);
      await s.click(add, { wait: 600 });
      await dlg(s).locator("select").selectOption("MX");
      await s.fill(field(s, "TTL", "TTL").locator("input"), "3600");
      await s.fill(field(s, "Name", "名稱").locator("input"), "ntubcloud.tw");
      await s.fill(field(s, "Content", "內容").locator("input"), "mx2.ntub.edu.tw");
      await s.fill(field(s, "Priority", "優先順序").locator("input"), "20");
      await s.fill(field(s, "Comment", "備註").locator("input"), s.t("Backup mail exchanger", "備援郵件伺服器"));
      await s.shot(2, [field(s, "Type", "類型"), field(s, "TTL", "TTL")]);
      await s.shot(3, [field(s, "Name", "名稱"), field(s, "Content", "內容")]);
      await s.shot(4, field(s, "Priority", "優先順序"));
      await s.shot(5, [field(s, "Comment", "備註"), dlg(s).locator('[class*="_checkRow_"]')]);
      await s.shot(6, dlg(s).getByRole("button", { name: s.t("Save", "儲存"), exact: true }));
    },
    // s04 Edit or delete a DNS record
    async (s) => {
      await s.go("/domain", { wait: 1000 });
      const docs = row(s, "docs.ntubcloud.tw");
      await s.shot(1, docs.getByTitle(s.t("Edit", "編輯")), { min: [1000, 420] });
      await s.shot(2, docs.getByTitle(s.t("Delete", "刪除")), { min: [1000, 420] });
      await s.click(docs.getByTitle(s.t("Edit", "編輯")), { wait: 600 });
      await s.fill(field(s, "Content", "內容").locator("input"), "skylab-docs-v2.pages.dev");
      await s.fill(field(s, "Comment", "備註").locator("input"), "");
      await s.shot(1, [field(s, "Content", "內容"), field(s, "Comment", "備註"), dlg(s).getByRole("button", { name: s.t("Save", "儲存"), exact: true })]);
      await s.click(dlg(s).getByRole("button", { name: s.t("Cancel", "取消"), exact: true }), { wait: 600 });
      await s.click(docs.getByTitle(s.t("Delete", "刪除")), { wait: 500 });
      const confirm = s.page.getByRole("alertdialog").or(s.page.getByRole("dialog")).last();
      await s.shot(2, confirm.getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
    },
    // s05 Give a machine its own URL (published from the Firewall page, then seen here)
    {
      mocks(m) { firewallMocks(m, studentTopology(m)); },
      run: async (s) => {
        await s.go("/firewall", { wait: 1500 });
        await s.click(btn(s, "Add Connection", "新增連線"), { wait: 700 });
        await s.click(dlg(s).getByRole("button", { name: s.t("Expose a service", "開放服務給外部") }).first(), { wait: 600 });
        await dlg(s).locator("#cd-vm").selectOption("203");
        await s.fill(dlg(s).locator("#cd-domain-port"), "5000");
        await s.fill(dlg(s).locator("#cd-prefix"), "flask-demo", { wait: 1200 });
        await s.shot(1, [dlg(s).locator('[data-guide="connection-dialog-endpoints"]'), dlg(s).locator('[class*="_dirToggle_"]').first()]);
        await s.shot(2, [dlg(s).locator("#cd-domain-port"), dlg(s).locator("#cd-prefix"), dlg(s).locator("#cd-zone"), dlg(s).locator('input[type="checkbox"]').first()]);
        await s.click(dlg(s).getByRole("button", { name: s.t("Publish", "發布") }), { wait: 1000 });
        await s.go("/domain", { wait: 1200 });
        const rec = row(s, "flask-demo.ntubcloud.tw");
        await s.shot(3, [rec.locator('[class*="_rowMain_"]'), rec.locator('[class*="_badge_"]').first()], { min: [1100, 520] });
      },
    },
  ],
};
