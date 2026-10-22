// Manual page: manual/pve-connections.md
import { ago } from "../lib/mock.mjs";
import { connections, dropToasts, main, nodes, stableTokens, toastOf, waitToast } from "../mocks/infra.mjs";

const CA_PEM = `-----BEGIN CERTIFICATE-----
MIIFvTCCA6WgAwIBAgIUQ8b3N0dGVzdC1za3lsYWItcHZlLWNhMA0GCSqGSIb3DQEB
CwUAMG4xCzAJBgNVBAYTAlRXMQ8wDQYDVQQIDAZUYWlwZWkxDTALBgNVBAoMBE5U
VUIxFjAUBgNVBAsMDVNreUxhYiBQVkUgQ0ExJzAlBgNVBAMMHlByb3htb3ggVmly
dHVhbCBFbnZpcm9ubWVudCBDQTAeFw0yNjAzMDEwMDAwMDBaFw0zNjAyMjgwMDAw
-----END CERTIFICATE-----`;

function routes(m, opts = {}) {
  const st = m.state;
  st.conns = connections(m);
  st.nodes = nodes();
  stableTokens(m);
  const conn = (id) => st.conns.find((c) => c.id === Number(id));
  m.use([
    ["GET", /^\/api\/v1\/proxmox-config\/connections$/, () => st.conns],
    ["POST", /^\/api\/v1\/proxmox-config\/connections$/, ({ body }) => {
      const c = { ...body, id: 4, has_ca_cert: Boolean(body.ca_cert), node_count: 0, updated_at: ago(0) };
      delete c.password;
      delete c.ca_cert;
      st.conns.push(c);
      return c;
    }],
    ["PUT", /^\/api\/v1\/proxmox-config\/connections\/(\d+)$/, ({ mt, body }) => {
      const c = conn(mt[1]);
      if (body.is_default) st.conns.forEach((x) => { x.is_default = false; });
      Object.assign(c, body);
      return c;
    }],
    ["DELETE", /^\/api\/v1\/proxmox-config\/connections\/(\d+)$/, ({ mt, query }) => {
      if (opts.unreachable && query.force !== "true") {
        return { __status: 503, __body: { detail: m.L(
          "This Proxmox connection is currently unreachable, so whether machines still run on it cannot be verified. Deletion is blocked for now; please try again later.",
          "目前連不上這個 PVE，無法確認底下是否還有機器，暫時不允許刪除，請稍後再試") } };
      }
      st.conns = st.conns.filter((c) => c.id !== Number(mt[1]));
      return { success: true };
    }],
    ["POST", /^\/api\/v1\/proxmox-config\/connections\/(\d+)\/test$/, ({ mt }) => {
      const names = st.nodes.filter((n) => (Number(mt[1]) === 1 ? n.id <= 3 : Number(mt[1]) === 2 ? [4, 5].includes(n.id) : n.id === 6)).map((n) => n.name);
      return { __delay: 300, __body: { success: true, message: m.L(`Connection succeeded. Detected nodes: ${names.join(", ")}`, `連線成功，偵測到節點：${names.join(", ")}`) } };
    }],
    ["POST", /^\/api\/v1\/proxmox-config\/connections\/(\d+)\/sync$/, ({ mt }) => {
      const id = Number(mt[1]);
      if (id === 4) {
        st.nodes.push({ id: 7, name: "pve-04", host: "172.20.203.11", port: 8006, is_primary: true, is_online: true, last_checked: ago(0), priority: 5, enabled: true },
          { id: 8, name: "pve-05", host: "172.20.203.12", port: 8006, is_primary: false, is_online: true, last_checked: ago(0), priority: 5, enabled: true });
        conn(4).node_count = 2;
        return { __delay: 400, __body: { success: true, connection_id: 4, nodes: st.nodes.slice(-2), storage_count: 3 } };
      }
      st.nodes.forEach((n) => { n.last_checked = ago(0); });
      const list = st.nodes.filter((n) => (id === 1 ? n.id <= 3 : [4, 5].includes(n.id)));
      return { __delay: 400, __body: { success: true, connection_id: id, nodes: list, storage_count: id === 1 ? 7 : 3 } };
    }],
    ["GET", /^\/api\/v1\/proxmox-config\/nodes$/, () => st.nodes],
    ["PUT", /^\/api\/v1\/proxmox-config\/nodes\/(\d+)$/, ({ mt, body }) => {
      const n = st.nodes.find((x) => x.id === Number(mt[1]));
      Object.assign(n, body, { priority: Number(body.priority), port: Number(body.port) });
      return n;
    }],
  ]);
}

const dialog = (s) => s.page.locator('[role="dialog"]').last();
const confirmDlg = (s) => s.page.locator('[role="alertdialog"]').last();
const field = (scope, label) => scope.locator("label", { hasText: label }).locator("input, textarea").first();
const row = (s, text) => s.page.locator('[data-guide="pve-connection-row"]', { hasText: text }).first();
const rowBtn = (s, text, name) => row(s, text).getByRole("button", { name, exact: true });
const editBtn = (s, name) => s.page.getByRole("button", { name: `${s.t("Edit", "編輯")} ${name}`, exact: true });
const nodeRow = (s, name) => s.page.locator('[data-guide="pve-nodes"] > div > div', { hasText: name }).first();
// The crop stays the top 900 px (main()); a taller window keeps the floating AI mascot, pinned to the
// window's bottom-right corner, off the last node row.
const tallWindow = (s) => s.page.setViewportSize({ width: 1440, height: 1200 });

export default {
  page: "pve-connections",
  role: "admin",
  mocks(m) {
    routes(m);
  },
  sections: [
    // s01 Add a connection
    async (s) => {
      const L = s.m.L;
      // Tall enough for the whole connection form to fit in one picture.
      await s.page.setViewportSize({ width: 1440, height: 1480 });
      await s.go("/pve-connections");
      const add = s.page.locator('[data-guide="pve-add-connection"]').first();
      await s.shot(1, add, { crop: main(0, 420) });
      await s.click(add, { wait: 600 });
      const d = dialog(s);
      await s.fill(field(d, s.t("Name *", "名稱 *")), L("Research Cluster", "研究叢集"));
      await s.fill(field(d, "Host *"), "172.20.203.11");
      await s.fill(field(d, s.t("API User", "API 使用者")), "skylab@pve");
      await s.fill(field(d, s.t("Password", "密碼")), "Sk7!pve-api-2026");
      const verify = d.locator("label", { hasText: s.t("Verify SSL certificate", "驗證 SSL 憑證") });
      await s.click(verify.locator("input"));
      const ca = field(d, s.t("CA Certificate PEM", "CA 憑證 PEM"));
      await s.fill(ca, CA_PEM);
      await s.fill(field(d, "Data Storage"), "ceph-vm");
      await s.fill(field(d, "Backup Storage"), "pbs-backup");
      await s.fill(field(d, "Gateway IP"), "10.30.0.1");
      await s.fill(field(d, s.t("Local Subnet", "內網網段")), "10.30.0.0/16");
      await s.fill(field(d, s.t("Default Node", "預設節點")), "pve-04");
      const enable = d.locator("label", { hasText: s.t("Enable this connection", "啟用此連線") });
      const def = d.locator("label", { hasText: s.t("Set as default connection", "設為預設連線") });
      const save = d.getByRole("button", { name: s.t("Save Connection", "儲存連線") });
      const o = { scroll: false };
      await s.shot(2, [field(d, s.t("Name *", "名稱 *")), field(d, "Host *"), field(d, "Port"), field(d, s.t("API User", "API 使用者")), field(d, s.t("Password", "密碼")), field(d, "API Timeout")], o);
      await s.shot(3, [verify, ca], o);
      await s.shot(4, [field(d, s.t("Pool Name", "Pool 名稱")), field(d, "ISO Storage"), field(d, "Data Storage"), field(d, "Backup Storage")], o);
      await s.shot(5, [enable, def], o);
      await s.shot(6, save, o);
      await s.click(save, { wait: 900 });
      await dropToasts(s);
      await s.shot(7, rowBtn(s, L("Research Cluster", "研究叢集"), s.t("Sync", "同步")), { crop: main(0, 560), scroll: false });
    },
    // s02 Test a connection
    async (s) => {
      await s.go("/pve-connections");
      const btn = rowBtn(s, s.m.L("Main Cluster", "主叢集"), s.t("Test", "測試"));
      await s.click(btn, { wait: 300 });
      await waitToast(s);
      await s.shot(1, btn, { crop: main(0, 440), scroll: false });
      await s.shot(2, toastOf(s), { crop: main(0, 440), scroll: false });
    },
    // s03 Sync nodes and storages
    async (s) => {
      await tallWindow(s);
      await s.go("/pve-connections");
      const btn = rowBtn(s, s.m.L("Main Cluster", "主叢集"), s.t("Sync", "同步"));
      await s.click(btn, { wait: 300 });
      await waitToast(s);
      await s.shot(1, btn, { crop: main(), scroll: false });
      await s.shot(2, [toastOf(s), s.page.locator('[data-guide="pve-nodes"] > div').first()], { crop: main(), scroll: false });
    },
    // s04 Edit a connection
    async (s) => {
      await s.go("/pve-connections");
      const name = s.m.L("Main Cluster", "主叢集");
      await s.shot(1, editBtn(s, name), { crop: main(0, 440) });
      await s.click(editBtn(s, name), { wait: 600 });
      const d = dialog(s);
      await s.fill(field(d, "API Timeout"), "60");
      await s.shot(2, [field(d, s.t("Password", "密碼")), field(d, s.t("CA Certificate PEM", "CA 憑證 PEM"))]);
      await s.shot(3, d.getByRole("button", { name: s.t("Save Connection", "儲存連線") }));
    },
    // s05 Make a connection the default
    async (s) => {
      await s.page.setViewportSize({ width: 1440, height: 1300 });
      await s.go("/pve-connections");
      const name = s.m.L("GPU Cluster", "GPU 叢集");
      await s.shot(1, editBtn(s, name), { crop: main(0, 440) });
      await s.click(editBtn(s, name), { wait: 600 });
      const d = dialog(s);
      const def = d.locator("label", { hasText: s.t("Set as default connection", "設為預設連線") });
      await s.click(def.locator("input"));
      await s.shot(2, [def, d.getByRole("button", { name: s.t("Save Connection", "儲存連線") })], { scroll: false });
    },
    // s06 Delete a connection
    async (s) => {
      await s.go("/pve-connections");
      const name = s.m.L("GPU Cluster", "GPU 叢集");
      const del = s.page.getByRole("button", { name: `${s.t("Delete", "刪除")} ${name}` });
      await s.shot(1, del, { crop: main(0, 440) });
      await s.click(del, { wait: 500 });
      await s.shot(2, confirmDlg(s).getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
    },
    // s07 Force-delete a connection whose PVE is gone
    {
      mocks(m) { routes(m, { unreachable: true }); },
      run: async (s) => {
        await s.go("/pve-connections");
        const name = s.m.L("Old Lab PVE", "舊機房 PVE");
        await s.click(s.page.getByRole("button", { name: `${s.t("Delete", "刪除")} ${name}` }), { wait: 500 });
        const first = confirmDlg(s).getByRole("button", { name: s.t("Delete", "刪除"), exact: true });
        await s.shot(1, first);
        await s.click(first, { wait: 900 });
        const force = confirmDlg(s);
        await force.getByText(s.t("Force Delete PVE Connection", "強制刪除 PVE 連線")).waitFor();
        await s.shot(2, force.getByText(s.t("currently unreachable", "目前連不上")));
        await s.shot(3, force.getByRole("button", { name: s.t("Force Delete", "強制刪除"), exact: true }));
      },
    },
    // s08 Enable or disable a node
    async (s) => {
      await tallWindow(s);
      await s.go("/pve-connections");
      const r = nodeRow(s, "pve-03");
      await r.scrollIntoViewIfNeeded();
      const sw = r.getByRole("switch");
      await s.click(sw, { wait: 300 });
      await waitToast(s);
      await s.shot(1, sw, { crop: main(), scroll: false });
      await s.shot(2, [toastOf(s), r.getByText(s.t("Disabled", "停用"), { exact: true })], { crop: main(), scroll: false });
    },
    // s09 Edit a node's address and priority
    async (s) => {
      await tallWindow(s);
      await s.go("/pve-connections");
      const edit = editBtn(s, "gpu-02");
      await s.shot(1, edit, { crop: main() });
      await s.click(edit, { wait: 600 });
      const d = dialog(s);
      await s.fill(field(d, s.t("Priority", "優先度")), "3");
      await s.shot(2, [field(d, "Host"), field(d, "Port"), field(d, s.t("Priority", "優先度"))]);
      await s.shot(3, d.getByRole("button", { name: s.t("Save", "儲存"), exact: true }));
    },
  ],
};
