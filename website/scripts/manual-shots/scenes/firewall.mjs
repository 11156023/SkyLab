// Manual page: manual/firewall.md
import { GW, firewallMocks, studentTopology } from "../mocks/network.mjs";

// Teacher view: own machines plus two classes of student machines (14 machines: grouped by default).
function teacherTopology(m) {
  const sec = m.L("Network Security (Mon)", "網路安全實務（週一）");
  const cloud = m.L("Cloud Computing (Wed)", "雲端運算（週三）");
  const students = [
    m.L("Emily Chang", "張雅婷"), m.L("Kevin Chen", "陳冠宇"), m.L("Grace Lee", "李佳穎"),
    m.L("Brian Huang", "黃柏翰"), m.L("Iris Wu", "吳欣怡"), m.L("Jason Lin", "林育成"),
  ];
  const nodes = [GW];
  const vm = (vmid, name, ip, extra = {}) => nodes.push({
    vmid, name, node_type: "vm", vm_type: "lxc", status: "running", ip_address: ip, firewall_enabled: true,
    position_x: 100, position_y: 100, can_manage: true, owner_name: null, teaching_class_name: null,
    machine_kind: "personal", class_relation: null, ...extra,
  });
  vm(211, "course-site", "10.20.0.31");
  vm(212, "grading-db", "10.20.0.32");
  vm(213, "teacher-jupyter", "10.20.0.33", { vm_type: "qemu" });
  students.forEach((o, i) => vm(260 + i, `netsec-lab-${String(i + 1).padStart(2, "0")}`, `10.20.0.${60 + i}`,
    { owner_name: o, teaching_class_name: sec, machine_kind: "teaching_class", class_relation: "teacher" }));
  students.slice(0, 5).forEach((o, i) => vm(280 + i, `cloud-web-${String(i + 1).padStart(2, "0")}`, `10.20.0.${80 + i}`,
    { owner_name: o, teaching_class_name: cloud, machine_kind: "teaching_class", class_relation: "teacher" }));
  const e = (source_vmid, target_vmid, ports, extra = {}) => ({ source_vmid, target_vmid, ports, direction: "one_way", topology_managed: false, ...extra });
  const edges = [
    e(null, 211, [{ port: 443, protocol: "tcp", domain: "course.ntubcloud.tw", enable_https: true }]),
    e(211, 212, [{ port: 5432, protocol: "tcp" }]),
    ...[260, 261, 262, 263, 264, 265].map((v, i) => e(null, v, [{ port: 22, protocol: "tcp", external_port: 31001 + i }], { topology_managed: true })),
    ...[280, 281, 282, 283, 284].map((v) => e(v, 212, [{ port: 5432, protocol: "tcp" }])),
    ...nodes.filter((n) => n.vmid).map((n) => e(n.vmid, null, [])),
  ];
  return { nodes, edges };
}

const MAP = '[data-guide="firewall-map"]';
const node = (s, vmid) => s.page.locator(`.react-flow__node[data-id="${vmid}"]`);
const label = (s, text) => s.page.locator(".react-flow__edgelabel-renderer button", { hasText: text }).first();
const dlg = (s) => s.page.getByRole("dialog");
const card = (s, en, zh) => dlg(s).getByRole("button", { name: s.t(en, zh) }).first();
const mapCrop = (s) => ({ crop: s.page.locator(MAP), pad: 0 });

// At 1440 px the English toolbar is wider than the canvas leaves room for, so the search box sits on top of
// "Mini Map" / "Exposed only" (the canvas is 1018 px; the filter bar only moves under the toolbar at <= 1000 px).
// The English shots use a 1400 px window, where the page's own narrow layout applies.
const WIDTH = (s) => (s.lang === "en" ? 1400 : 1440);
const viewport = (s, height = 900) => s.page.setViewportSize({ width: WIDTH(s), height });
async function openMap(s, wait = 1500) {
  await viewport(s);
  await s.go("/firewall", { wait });
}

async function openDialog(s) {
  await openMap(s, 1200);
  await s.click(s.page.getByRole("button", { name: s.t("Add Connection", "新增連線") }), { wait: 700 });
}

export default {
  page: "firewall",
  role: "student",
  mocks(m) { firewallMocks(m, studentTopology(m)); },
  sections: [
    // s01 Read the map
    async (s) => {
      await openMap(s);
      await s.shot(1, [node(s, "gateway"), node(s, 201)], mapCrop(s));
      await s.shot(2, [s.page.locator('[class*="_legend_"]').first(), label(s, "portal.ntubcloud.tw")], mapCrop(s));
      await s.shot(3, s.page.getByRole("button", { name: s.t("Internet Lines", "上網線") }), mapCrop(s));
      await s.click(label(s, "portal.ntubcloud.tw"), { wait: 700 });
      await s.shot(4, [label(s, "portal.ntubcloud.tw"), s.page.locator('[class*="_panel_"]').filter({ hasText: s.t("Connection details", "連線內容") }).first()], mapCrop(s));
      await s.click(node(s, 203), { wait: 900 });
      await s.shot(5, [node(s, 203), s.page.getByRole("button", { name: s.t("Deselect", "取消選取") })], mapCrop(s));
    },
    // s02 Create a connection by dragging
    async (s) => {
      await openMap(s);
      const src = node(s, 204);
      await src.hover();
      await s.settle(400);
      await s.shot(1, src, mapCrop(s));
      const from = await src.locator('[data-handleid="s-left"]').boundingBox();
      const to = await node(s, 202).locator('[data-handleid="t-right"]').boundingBox();
      const fx = from.x + from.width / 2, fy = from.y + from.height / 2;
      const tx = to.x + to.width / 2, ty = to.y + to.height / 2;
      await s.page.mouse.move(fx, fy);
      await s.page.mouse.down();
      for (let i = 1; i <= 12; i++) await s.page.mouse.move(fx + (tx - fx) * i * 0.075, fy + (ty - fy) * i * 0.075);
      await s.page.waitForTimeout(300);
      await s.shot(2, [src.locator('[data-handleid="s-left"]'), node(s, 202)], mapCrop(s));
      await s.page.mouse.move(tx, ty, { steps: 4 });
      await s.page.mouse.up();
      await s.settle(800);
      await s.shot(3, dlg(s).locator('[data-guide="connection-dialog-endpoints"]'));
      await s.fill(dlg(s).locator("[data-port-input]").first(), "3306");
      await s.shot(4, [dlg(s).locator("[data-port-input]").first(), dlg(s).getByRole("button", { name: s.t("Create connection", "建立連線") })]);
    },
    // s03 Create a connection with the button
    async (s) => {
      await openMap(s, 1200);
      const add = s.page.getByRole("button", { name: s.t("Add Connection", "新增連線") });
      // Header (title and button) plus the top of the map, so the button is seen in context.
      await s.shot(1, add, { crop: { x: 340, y: 0, width: WIDTH(s) - 340, height: 520 } });
      await s.click(add, { wait: 700 });
      await s.shot(2, dlg(s).locator('[data-guide="connection-dialog-endpoints"]'));
    },
    // s04 Let a machine reach the internet
    async (s) => {
      await openDialog(s);
      const c = card(s, "Let a machine reach the internet", "讓機器能上網");
      await s.shot(1, c);
      await s.click(c, { wait: 500 });
      await dlg(s).locator("#cd-vm").selectOption("202");
      await s.settle(200);
      await s.shot(2, dlg(s).locator("#cd-vm"));
      await s.shot(3, dlg(s).getByRole("button", { name: s.t("Create connection", "建立連線") }));
    },
    // s05 Connect two machines
    async (s) => {
      await openDialog(s);
      const c = card(s, "Connect two machines", "讓兩台機器互通");
      await s.shot(1, c);
      await s.click(c, { wait: 500 });
      await dlg(s).locator("#cd-peer-source").selectOption("204");
      await dlg(s).locator("#cd-peer-target").selectOption("202");
      await s.fill(dlg(s).locator("[data-port-input]").first(), "3306");
      await s.click(dlg(s).getByRole("button", { name: s.t("Add port", "新增 port") }), { wait: 300 });
      await dlg(s).locator("select").last().selectOption("icmp");
      await s.settle(200);
      await s.shot(2, [dlg(s).locator("#cd-peer-source"), dlg(s).locator("#cd-peer-target")]);
      await s.shot(3, dlg(s).getByRole("radiogroup").or(dlg(s).locator('[class*="_dirToggle_"]')).first());
      await s.shot(4, [dlg(s).locator('[class*="_portSection_"]').first()]);
      await s.shot(5, dlg(s).getByRole("button", { name: s.t("Create connection", "建立連線") }));
    },
    // s06 Expose a service to the outside
    async (s) => {
      await openDialog(s);
      await s.click(card(s, "Expose a service", "開放服務給外部"), { wait: 600 });
      await dlg(s).locator("#cd-vm").selectOption("203");
      await s.settle(200);
      await s.shot(1, [dlg(s).locator('[data-guide="connection-dialog-endpoints"]'), dlg(s).locator("#cd-vm")]);
      await s.fill(dlg(s).locator("#cd-domain-port"), "5000");
      await s.fill(dlg(s).locator("#cd-prefix"), "flask-demo", { wait: 1200 });
      await s.shot(2, [
        dlg(s).locator('[class*="_dirToggle_"]').first(),
        dlg(s).locator("#cd-domain-port"), dlg(s).locator("#cd-prefix"), dlg(s).locator("#cd-zone"),
        dlg(s).getByText(s.t("flask-demo.ntubcloud.tw is available", "flask-demo.ntubcloud.tw 可以使用")),
      ]);
      await s.shot(3, dlg(s).getByRole("button", { name: s.t("Publish", "發布") }));
    },
    // s07 Write a raw firewall rule
    async (s) => {
      await openDialog(s);
      const c = card(s, "Write a rule myself", "自己寫規則");
      await s.shot(1, c);
      await s.click(c, { wait: 500 });
      await dlg(s).locator("#cd-vm").selectOption("201");
      await dlg(s).locator("#cd-rule-type").selectOption("in");
      await dlg(s).locator("#cd-rule-action").selectOption("ACCEPT");
      await dlg(s).locator("#cd-rule-proto").selectOption("tcp");
      await s.fill(dlg(s).locator("#cd-rule-dport"), "8000:8010");
      await s.fill(dlg(s).locator("#cd-rule-addr"), "10.20.0.0/24");
      await s.fill(dlg(s).locator("#cd-rule-comment"), s.t("Course demo web servers", "課堂示範用網站"));
      await s.shot(2, ["#cd-rule-type", "#cd-rule-action", "#cd-rule-proto", "#cd-rule-dport"].map((id) => dlg(s).locator(id)));
      await s.shot(3, [dlg(s).locator("#cd-rule-addr"), dlg(s).locator("#cd-rule-comment")]);
      await s.shot(4, dlg(s).getByRole("button", { name: s.t("Add rule", "新增規則") }));
    },
    // s08 Manage a machine's rules (taller window so the whole rules panel fits without scrolling)
    async (s) => {
      await viewport(s, 1240);
      await s.go("/firewall", { wait: 1500 });
      await s.click(node(s, 201), { wait: 1200 });
      const sectionOf = (en, zh) => s.page.locator("div", { has: s.page.locator("h3", { hasText: s.t(en, zh) }) }).last();
      const rows = s.page.locator('[class*="_ruleRow_"]');
      const o = { crop: { x: 340, y: 80, width: WIDTH(s) - 340, height: 1160 }, scroll: false };
      await s.shot(1, node(s, 201), o);
      await s.shot(2, sectionOf("Firewall Settings", "防火牆設定"), o);
      await s.shot(3, s.page.locator('[class*="_ruleList_"]'), o);
      await s.shot(4, [rows.nth(0).locator('[class*="_lockedTag_"]'), rows.nth(3).locator('[class*="_lockedTag_"]')], o);
      await s.shot(5, [rows.nth(7).locator('[class*="_ruleActions_"]'), rows.nth(8).locator('[class*="_ruleActions_"]')], o);
      await s.shot(6, s.page.getByRole("link", { name: s.t("Open resource detail", "前往資源詳情") }), o);
    },
    // s09 Delete a connection
    async (s) => {
      await openMap(s);
      await s.click(label(s, "Redis (6379)"), { wait: 700 });
      await s.shot(1, [label(s, "Redis (6379)"), s.page.getByRole("button", { name: s.t("Delete connection", "刪除連線") })], mapCrop(s));
      await s.click(s.page.getByRole("button", { name: s.t("Delete connection", "刪除連線") }), { wait: 500 });
      const confirm = s.page.getByRole("alertdialog").or(s.page.getByRole("dialog")).last();
      await s.shot(2, confirm.getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
    },
    // s10 Arrange and filter the map (teacher with two classes: grouped by default)
    {
      role: "teacher",
      mocks(m) {
        const full = teacherTopology(m);
        // First only the teacher's own machines and three student machines (below the grouping threshold).
        const keep = new Set([null, 211, 212, 213, 260, 261, 262]);
        firewallMocks(m, { nodes: full.nodes.filter((n) => keep.has(n.vmid)), edges: full.edges.filter((e) => keep.has(e.source_vmid) && keep.has(e.target_vmid)) });
        m.state.full = full;
      },
      run: async (s) => {
        await openMap(s);
        const groupBtn = s.page.getByRole("button", { name: s.t("Group", "分組"), exact: true });
        const arrange = s.page.getByRole("button", { name: s.t("Auto Arrange", "自動排列") });
        await s.click(arrange, { wait: 1500 });
        await s.shot(1, arrange, mapCrop(s));
        s.m.state.topo = s.m.state.full;
        await s.go("/firewall", { wait: 1800 });
        await s.shot(2, [groupBtn, s.page.getByRole("button", { name: s.t("Reset layout", "重設位置") }), s.page.locator('.react-flow__node button[aria-expanded]').first()], mapCrop(s));
        await s.shot(3, ["Connection Labels|連線標籤", "Mini Map|地圖", "Exposed only|只看對外開放"].map((p) => {
          const [en, zh] = p.split("|");
          return s.page.getByRole("button", { name: s.t(en, zh), exact: true });
        }), mapCrop(s));
        const search = s.page.getByRole("searchbox", { name: s.t("Search name or IP", "搜尋機器名稱或 IP") });
        await s.fill(search, "netsec", { wait: 1200 });
        await s.shot(4, [search, s.page.getByRole("combobox", { name: s.t("Filter by group", "依群組篩選") }), s.page.getByRole("button", { name: s.t("Clear filters", "清除篩選") })], mapCrop(s));
      },
    },
  ],
};
