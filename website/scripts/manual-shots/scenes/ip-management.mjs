// Manual page: manual/ip-management.md
import { ago, D } from "../lib/mock.mjs";
import { SUBNET, allocations } from "../mocks/network.mjs";

const ipNum = (ip) => ip.split(".").reduce((a, b) => a * 256 + Number(b), 0);

function subnetConfig(m, rows) {
  const used = rows.length;
  return {
    ...SUBNET,
    extra_blocked_subnets: ["192.168.100.0/24", "140.131.0.0/16"],
    forward_port_start: 30000,
    forward_port_end: 39999,
    forward_public_host: "gw.ntubcloud.tw",
    updated_at: ago(12 * D),
    total_ips: 254,
    used_ips: used,
    available_ips: 254 - used,
  };
}

// Configured subnet with `extraVms` extra machines in the table.
function configured(m, { extraVms = 8, onlyGateways = false } = {}) {
  let rows = allocations(m, { extraVms }).sort((a, b) => ipNum(a.ip_address) - ipNum(b.ip_address));
  if (onlyGateways) rows = rows.filter((r) => ["subnet_gateway", "gateway_vm", "platform_host"].includes(r.purpose));
  m.state.rows = rows;
  m.state.subnet = subnetConfig(m, rows);
  m.use([
    ["GET", /^\/api\/v1\/ip-management\/subnet$/, ({ state }) => state.subnet ?? null],
    ["GET", /^\/api\/v1\/ip-management\/allocations$/, ({ state }) => ({ allocations: state.rows, total: state.rows.length })],
    ["GET", /^\/api\/v1\/ip-management\/status$/, ({ state }) => state.subnet
      ? { configured: true, cidr: state.subnet.cidr, bridge_name: state.subnet.bridge_name, vlan_tag: state.subnet.vlan_tag, total_ips: 254, used_ips: state.rows.length, available_ips: 254 - state.rows.length }
      : { configured: false, total_ips: 0, used_ips: 0, available_ips: 0 }],
    ["PUT", /^\/api\/v1\/ip-management\/subnet$/, ({ body, state }) => {
      state.subnet = { ...state.subnet, ...body, block_sync: { targets: body.extra_blocked_subnets ?? [], created: 0, updated: 0, skipped: 0, deleted: 0, errors: state.syncErrors ?? [] } };
      return state.subnet;
    }],
    ["DELETE", /^\/api\/v1\/ip-management\/subnet$/, ({ state }) => { state.subnet = null; state.rows = []; return { message: "ok" }; }],
  ]);
}

function unconfigured(m) {
  m.state.rows = [];
  m.state.subnet = null;
  m.use([
    ["GET", /^\/api\/v1\/ip-management\/subnet$/, ({ state }) => state.subnet ?? null],
    ["GET", /^\/api\/v1\/ip-management\/allocations$/, ({ state }) => ({ allocations: state.rows, total: state.rows.length })],
    ["GET", /^\/api\/v1\/ip-management\/status$/, ({ state }) => state.subnet
      ? { configured: true, cidr: state.subnet.cidr, bridge_name: state.subnet.bridge_name, vlan_tag: state.subnet.vlan_tag, total_ips: 254, used_ips: state.rows.length, available_ips: 254 - state.rows.length }
      : { configured: false, total_ips: 0, used_ips: 0, available_ips: 0 }],
    ["PUT", /^\/api\/v1\/ip-management\/subnet$/, ({ body, state }) => {
      state.rows = [
        { ip_address: body.gateway, purpose: "subnet_gateway", vmid: null, description: null, allocated_at: ago(0) },
        { ip_address: body.gateway_vm_ip, purpose: "gateway_vm", vmid: null, description: null, allocated_at: ago(0) },
        ...(body.platform_ip ? [{ ip_address: body.platform_ip, purpose: "platform_host", vmid: null, description: null, allocated_at: ago(0) }] : []),
      ];
      state.subnet = { ...body, updated_at: ago(0), total_ips: 254, used_ips: state.rows.length, available_ips: 254 - state.rows.length, block_sync: { targets: [], created: 0, updated: 0, skipped: 0, deleted: 0, errors: [] } };
      return state.subnet;
    }],
  ]);
}

// Clicks and waits for the toast; shoot right away (it disappears after 4 s).
async function clickToast(s, btn) {
  await btn.click();
  const toast = s.page.locator("[data-sonner-toast]").first();
  await toast.waitFor({ timeout: 15000 });
  await s.page.waitForTimeout(600);
  return toast;
}
const TOP = { x: 340, y: 0, width: 1100, height: 520 };
// The centred toast covers the page title (and, once the subnet is gone, the "not configured" banner):
// move the next toast elsewhere. left is the toast's centre in px.
const toastAt = (s, { top, left, width } = {}) => {
  const css = [];
  if (top != null) css.push(`top:${top}px !important`);
  if (left != null) css.push(`left:${left}px !important`);
  if (width != null) css.push(`--width:${width}px !important`);
  return s.page.addStyleTag({ content: `[data-sonner-toaster]{${css.join(";")}}` });
};

const dialog = (s) => s.page.getByRole("dialog");
const field = (s, en, zh) => s.page.getByRole("dialog").locator("label", { hasText: s.t(en, zh) }).first();

export default {
  page: "ip-management",
  role: "admin",
  mocks(m) { configured(m); },
  sections: [
    // s01 Create the subnet configuration
    {
      mocks(m) { unconfigured(m); },
      run: async (s) => {
        await s.go("/ip-management");
        const empty = s.page.getByText(s.t("Subnet not configured yet", "尚未設定子網"));
        await s.shot(1, empty, { min: [720, 300] });
        const create = s.page.getByRole("button", { name: s.t("Create Subnet Config", "建立子網設定") });
        await s.shot(2, create, { min: [720, 300] });
        await s.click(create, { wait: 600 });
        const d = dialog(s);
        const inputs = d.locator("input");
        await s.fill(inputs.nth(0), "10.20.0.0/24");
        await s.fill(inputs.nth(1), "10.20.0.1");
        await s.fill(inputs.nth(2), "vmbr1");
        await s.fill(inputs.nth(4), "10.20.0.2");
        await s.shot(3, [field(s, "Subnet CIDR", "子網 CIDR"), field(s, "Gateway IP", "閘道 IP"), field(s, "Bridge Name", "Bridge 名稱"), field(s, "Gateway VM IP", "Gateway VM IP")]);
        await s.fill(inputs.nth(3), "120");
        await s.fill(inputs.nth(5), "10.20.0.3");
        await s.fill(inputs.nth(6), "140.131.1.1,8.8.8.8");
        await s.fill(inputs.nth(9), "gw.ntubcloud.tw");
        await s.fill(d.locator("textarea"), "192.168.100.0/24\n140.131.0.0/16");
        await s.shot(4, [
          field(s, "VLAN ID", "VLAN ID"), field(s, "Main system IP", "主系統 IP"), field(s, "DNS Servers", "DNS Servers"),
          field(s, "External port pool: start", "對外 port 配號池：起始"), field(s, "External port pool: end", "對外 port 配號池：結束"),
          field(s, "Public entry host", "對外入口主機"), field(s, "Default Blocked Subnets", "預設封鎖網段"),
        ]);
        const save = d.getByRole("button", { name: s.t("Create Config", "建立設定") });
        await s.shot(5, save);
        // Between the title and the Edit button.
        await toastAt(s, { left: 960 });
        const toast = await clickToast(s, save);
        await s.shot(5, toast, { crop: TOP });
      },
    },
    // s02 Change the subnet configuration
    {
      mocks(m) {
        configured(m);
        m.state.syncErrors = [{ vmid: 203, error: "PVE node pve-02 unreachable" }, { vmid: 241, error: "PVE node pve-02 unreachable" }];
      },
      run: async (s) => {
        await s.go("/ip-management");
        const edit = s.page.getByRole("button", { name: s.t("Edit Subnet Config", "編輯子網設定") });
        await s.shot(1, edit, { crop: TOP });
        await s.click(edit, { wait: 600 });
        const d = dialog(s);
        await s.fill(d.locator("input").nth(6), "140.131.1.1,1.1.1.1");
        await s.shot(2, [field(s, "DNS Servers", "DNS Servers"), d.getByRole("button", { name: s.t("Update Config", "更新設定") })]);
        await s.shot(3, field(s, "Subnet CIDR", "子網 CIDR"));
        await s.fill(d.locator("textarea"), "192.168.100.0/24\n140.131.0.0/16\n10.99.0.0/16");
        // The long error toast, wider and right of the title, over the stat cards.
        await toastAt(s, { left: 935, width: 470 });
        const toast = await clickToast(s, d.getByRole("button", { name: s.t("Update Config", "更新設定") }));
        await s.shot(4, toast, { crop: TOP });
      },
    },
    // s03 Delete the subnet configuration
    {
      mocks(m) { configured(m, { extraVms: 0, onlyGateways: true }); },
      run: async (s) => {
        await s.go("/ip-management");
        await s.click(s.page.getByRole("button", { name: s.t("Edit Subnet Config", "編輯子網設定") }), { wait: 600 });
        const del = dialog(s).getByRole("button", { name: s.t("Delete Subnet Config", "刪除子網設定") });
        await s.shot(1, del);
        await s.click(del, { wait: 500 });
        const confirm = s.page.getByRole("alertdialog").or(s.page.getByRole("dialog").filter({ hasText: s.t("Are you sure", "確定刪除") })).last();
        await s.shot(2, confirm.getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
        // Right of the search box, so the "subnet not configured" banner at the top stays readable.
        await toastAt(s, { top: 262, left: 1000 });
        const toast = await clickToast(s, confirm.getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
        await s.shot(3, [toast, s.page.getByText(s.t("Subnet not configured yet", "尚未設定子網"))], { crop: { x: 340, y: 0, width: 1100, height: 700 } });
      },
    },
    // s04 Look up who uses an address
    {
      mocks(m) { configured(m, { extraVms: 50 }); },
      run: async (s) => {
        await s.go("/ip-management");
        const search = s.page.getByPlaceholder(s.t("Search by IP, VMID, or description", "搜尋 IP、VMID 或備註"));
        await s.fill(search, "kali", { wait: 400 });
        await s.shot(1, search, { crop: { x: 340, y: 0, width: 1100, height: 620 } });
        // Taller window: page 2 of the table and the pager fit on one screen.
        await s.page.setViewportSize({ width: 1440, height: 1400 });
        await s.fill(search, "", { wait: 400 });
        await s.click(s.page.getByRole("button", { name: s.t("Next", "下一頁") }), { wait: 500 });
        await s.page.evaluate(() => window.scrollTo(0, 0));
        await s.shot(2, s.page.getByText(s.t("Subnet:", "子網:")), { crop: { x: 340, y: 0, width: 1100, height: 1400 }, scroll: false });
        await s.shot(3, [s.page.locator("thead tr"), s.page.getByRole("button", { name: s.t("Previous", "上一頁") }), s.page.getByRole("button", { name: s.t("Next", "下一頁") })], { crop: { x: 340, y: 0, width: 1100, height: 1400 }, scroll: false });
      },
    },
  ],
};
