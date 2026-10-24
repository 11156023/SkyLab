// Manual page: manual/monitoring.md
import { D, H, MIN, NOW, ago } from "../lib/mock.mjs";
import { GB, main, stableTokens, toastOf, waitToast } from "../mocks/infra.mjs";

const unix = (ms) => (NOW - ms) / 1000;

const TASKS = [
  "process_due_request_starts", "process_due_request_stops", "process_expired_requests", "process_expired_unprovisioned_requests",
  "process_pending_deletions", "process_recurrence_windows", "process_scheduled_boot", "process_auto_stops",
  "process_quick_practice_lifecycle", "process_resource_alerts", "process_ttl_lifecycle", "process_idle_detection",
  "process_mining_detection", "process_snapshot_cleanup", "process_system_health_alerts",
];

function health(m) {
  return {
    status: "degraded",
    generated_at: new Date(NOW - 4000).toISOString(),
    heartbeat_source: "redis",
    components: [
      { name: "database", label: "Database", status: "ok", latency_ms: 3, detail: null },
      { name: "redis", label: "Redis", status: "ok", latency_ms: 1, detail: null },
      { name: "worker", label: "Background worker", status: "ok", latency_ms: null, detail: m.L("2 workers online", "2 個 worker 在線") },
      { name: "pve:1", label: m.L("Main Cluster", "主叢集"), status: "ok", latency_ms: 38, detail: m.L("3 nodes", "3 個節點") },
      { name: "pve:2", label: m.L("GPU Cluster", "GPU 叢集"), status: "ok", latency_ms: 44, detail: m.L("2 nodes", "2 個節點") },
      { name: "gateway", label: "Gateway 10.20.0.254", status: "ok", latency_ms: 12, detail: "nginx 1.26" },
      { name: "ai_gateway", label: "AI gateway (LiteLLM)", status: "ok", latency_ms: 21, detail: null },
      { name: "ai_model:qwen3-32b", label: "qwen3-32b", status: "ok", latency_ms: 180, detail: null },
      { name: "ai_model:gpt-oss-120b", label: "gpt-oss-120b", status: "ok", latency_ms: 240, detail: null },
    ],
    loops: [
      { loop: "scheduler", status: "ok", interval_seconds: 60, last_tick_at: unix(14000), leader_last_tick_at: unix(14000), leader_instance: "backend-1" },
      { loop: "web_push", status: "ok", interval_seconds: 30, last_tick_at: unix(9000), leader_last_tick_at: unix(9000), leader_instance: "backend-1" },
      { loop: "wireguard", status: "ok", interval_seconds: 60, last_tick_at: unix(41000), leader_last_tick_at: unix(41000), leader_instance: "backend-2" },
    ],
    tasks: TASKS.map((task, k) => {
      const failing = task === "process_snapshot_cleanup";
      return {
        loop: "scheduler", task, status: failing ? "failing" : "ok", interval_seconds: 60,
        last_run_at: unix((14 + k) * 1000), last_success_at: unix(failing ? 3 * H + 14000 : (14 + k) * 1000), last_failure_at: failing ? unix(14000) : null,
        last_duration_ms: failing ? 30012 : 40 + ((k * 137) % 900), consecutive_failures: failing ? 3 : 0, total_runs: 1440, total_failures: failing ? 3 : 0,
        last_error: failing ? m.L("Snapshot delete on pve-02 timed out after 30 s (VM 10412 is locked: backup)", "pve-02 刪除快照逾時 30 秒（VM 10412 被備份鎖定）") : null,
      };
    }),
  };
}

function overview() {
  const n = (node, connection_name, cpu, maxcpu, memG, maxmemG, diskT, maxdiskT, vm_count, uptimeD) => ({
    node, status: "online", connection_name, cpu, maxcpu, mem: memG * GB, maxmem: maxmemG * GB, disk: diskT * 1024 * GB, maxdisk: maxdiskT * 1024 * GB, vm_count, uptime: uptimeD * 86400,
  });
  return (m) => ({
    collected_at: ago(20000), data_status: "fresh",
    nodes_online: 5, nodes_total: 5, vms_running: 96, vms_stopped: 31, lxc_running: 184, lxc_stopped: 22,
    cpu_used: 171.6, cpu_total: 448, mem_used: 1386 * GB, mem_total: 2560 * GB, disk_used: 19.4 * 1024 * GB, disk_total: 38.2 * 1024 * GB,
    nodes: [
      n("pve-01", m.L("Main Cluster", "主叢集"), 0.42, 64, 301, 512, 1.9, 3.5, 88, 41),
      n("pve-02", m.L("Main Cluster", "主叢集"), 0.63, 64, 431, 512, 2.6, 3.5, 102, 41),
      n("pve-03", m.L("Main Cluster", "主叢集"), 0.37, 64, 262, 512, 1.4, 3.5, 74, 12),
      n("gpu-01", m.L("GPU Cluster", "GPU 叢集"), 0.29, 128, 214, 512, 1.5, 3.5, 21, 27),
      n("gpu-02", m.L("GPU Cluster", "GPU 叢集"), 0.24, 128, 178, 512, 2.2, 3.5, 18, 27),
    ],
    top_cpu: [
      { vmid: 10388, name: "crypto-lab-test", node: "pve-02", type: "qemu", cpu: 0.968, mem: 3.8 * GB },
      { vmid: 10412, name: "llm-finetune-11156012", node: "gpu-01", type: "qemu", cpu: 0.912, mem: 58.2 * GB },
      { vmid: 20233, name: "web-11256002", node: "pve-01", type: "lxc", cpu: 0.61, mem: 1.2 * GB },
      { vmid: 20517, name: "hadoop-worker-3", node: "pve-03", type: "lxc", cpu: 0.55, mem: 6.1 * GB },
      { vmid: 10231, name: "ubuntu-desktop", node: "pve-02", type: "qemu", cpu: 0.47, mem: 5.4 * GB },
    ],
    top_mem: [
      { vmid: 10412, name: "llm-finetune-11156012", node: "gpu-01", type: "qemu", cpu: 0.912, mem: 58.2 * GB },
      { vmid: 10502, name: "llama-70b-eval", node: "gpu-02", type: "qemu", cpu: 0.33, mem: 46.7 * GB },
      { vmid: 10340, name: "db-cluster-01", node: "pve-03", type: "qemu", cpu: 0.21, mem: 15.5 * GB },
      { vmid: 20517, name: "hadoop-worker-3", node: "pve-03", type: "lxc", cpu: 0.55, mem: 6.1 * GB },
      { vmid: 10231, name: "ubuntu-desktop", node: "pve-02", type: "qemu", cpu: 0.47, mem: 5.4 * GB },
    ],
    issues: [],
  });
}

function alerts(m) {
  const a = (id, scope, target, metric, value, threshold, message, mins, extra = {}) => ({
    id, scope, target, metric, value, threshold, message, created_at: ago(mins * MIN), resolved_at: null, acknowledged_by: null, acknowledged_at: null, connection_id: null, connection_name: null, ...extra,
  });
  return [
    a("6b1f6a52-1e0a-4b8e-9f00-000000000001", "system", m.L("Scheduled task", "排程任務"), "cpu", 3, 3,
      m.L("process_snapshot_cleanup has failed 3 times in a row", "process_snapshot_cleanup 已連續失敗 3 次"), 12),
    a("6b1f6a52-1e0a-4b8e-9f00-000000000002", "node", "pve-02", "memory", 92.4, 90, "", 35, { connection_id: 1, connection_name: m.L("Main Cluster", "主叢集") }),
    a("6b1f6a52-1e0a-4b8e-9f00-000000000003", "vm", "10388 crypto-lab-test", "cpu", 96.8, 90, "", 5 * 60, { connection_id: 1, connection_name: m.L("Main Cluster", "主叢集") }),
    a("6b1f6a52-1e0a-4b8e-9f00-000000000004", "cluster", m.L("GPU Cluster", "GPU 叢集"), "disk", 85.2, 85, "", 3 * 60, { connection_id: 2, connection_name: m.L("GPU Cluster", "GPU 叢集"), acknowledged_at: ago(2 * H), acknowledged_by: "7d3c1a2e-0003-4a00-9000-000000000003" }),
  ];
}

function incidents(m) {
  const base = { user_id: "7d3c1a2e-0101-4a00-9000-0000000000aa", resource_type: "qemu", window_hours: 6, suspended_at: null, reviewed_by: null, reviewed_at: null, review_note: null };
  return [
    { ...base, id: "a9c2e7d4-0000-4c1e-8000-000000000003", vmid: 10388, node: "pve-02", avg_cpu: 96.8, snapshot_name: "mining-evidence-20261012-0412", status: "suspended", detected_at: ago(5 * H + 48 * MIN), suspended_at: ago(5 * H + 47 * MIN), owner_email: "11156012@ntub.edu.tw", owner_full_name: m.L("Kevin Chen", "陳冠宇") },
    { ...base, id: "a9c2e7d4-0000-4c1e-8000-000000000004", vmid: 10418, node: "gpu-01", avg_cpu: 98.9, snapshot_name: "mining-evidence-20261012-0817", status: "detected", detected_at: ago(1 * H + 43 * MIN), owner_email: "11156034@ntub.edu.tw", owner_full_name: m.L("Brian Huang", "黃柏翰") },
    { ...base, id: "a9c2e7d4-0000-4c1e-8000-000000000002", vmid: 20260, node: "pve-01", resource_type: "lxc", avg_cpu: 91.2, snapshot_name: null, status: "dismissed", detected_at: ago(7 * D), reviewed_at: ago(7 * D - 3 * H), review_note: m.L("Image-recognition coursework training a model; added to exemptions", "影像辨識作業訓練模型，已加入豁免"), owner_email: "11256021@ntub.edu.tw", owner_full_name: m.L("Grace Lee", "李佳穎") },
    { ...base, id: "a9c2e7d4-0000-4c1e-8000-000000000001", vmid: 10277, node: "pve-03", avg_cpu: 99.1, snapshot_name: "mining-evidence-20260924-0130", status: "banned", detected_at: ago(18 * D), reviewed_at: ago(18 * D - 9 * H), review_note: m.L("xmrig found in /tmp; account disabled", "/tmp 中發現 xmrig，帳號已停權"), owner_email: "11256045@ntub.edu.tw", owner_full_name: m.L("Iris Wu", "吳欣怡") },
  ];
}

function rrd() {
  return Array.from({ length: 72 }, (_, i) => ({
    time: Math.floor((NOW - (72 - i) * 20 * MIN) / 1000),
    cpu: 0.38 + 0.18 * Math.sin(i / 7) + 0.06 * Math.sin(i * 1.7),
    memused: (410 + 18 * Math.cos(i / 9)) * GB, memtotal: 512 * GB,
  }));
}

function routes(m) {
  stableTokens(m);
  const st = m.state;
  st.alerts = alerts(m);
  st.incidents = incidents(m);
  const ov = overview();
  m.use([
    ["GET", /^\/api\/v1\/monitoring\/system-health$/, () => health(m)],
    ["GET", /^\/api\/v1\/monitoring\/overview$/, () => (st.pveDown ? { __status: 502, __body: { detail: "Proxmox API unreachable" } } : ov(m))],
    ["GET", /^\/api\/v1\/monitoring\/alerts$/, () => st.alerts],
    ["POST", /^\/api\/v1\/monitoring\/alerts\/([^/]+)\/ack$/, ({ mt }) => {
      const a = st.alerts.find((x) => x.id === mt[1]);
      a.acknowledged_at = new Date(NOW).toISOString();
      a.acknowledged_by = m.users.admin.id;
      return a;
    }],
    ["POST", /^\/api\/v1\/monitoring\/grafana\/session$/, () => ({ enabled: true, url: "https://skylab.ntub.edu.tw/grafana/" })],
    ["GET", /^\/api\/v1\/monitoring\/nodes\/[^/]+\/rrd$/, () => rrd()],
    ["GET", /^\/api\/v1\/mining-incidents$/, () => st.incidents],
    ["POST", /^\/api\/v1\/mining-incidents\/([^/]+)\/dismiss$/, ({ mt, body }) => {
      const x = st.incidents.find((i) => i.id === mt[1]);
      const warning = m.L("Resuming VM 10388 failed: VM is locked (snapshot-delete); start it manually", "恢復 VM 10388 失敗：VM 被鎖定（snapshot-delete），請手動開機");
      Object.assign(x, { status: "dismissed", reviewed_at: new Date(NOW).toISOString(), review_note: [body?.note, warning].filter(Boolean).join("；") });
      return { __delay: 300, __body: { ...x, warnings: [warning] } };
    }],
    ["POST", /^\/api\/v1\/mining-incidents\/([^/]+)\/ban$/, ({ mt }) => {
      const x = st.incidents.find((i) => i.id === mt[1]);
      x.status = "banned";
      return x;
    }],
  ]);
}

const tab = (s, en, zh) => s.page.getByRole("tab", { name: new RegExp(s.t(en, zh)) });
const healthCard = (s) => s.page.locator('[data-guide="monitoring-health"]');

export default {
  page: "monitoring",
  role: "admin",
  mocks(m) { routes(m); },
  sections: [
    // s01 Check the health of the platform
    async (s) => {
      await s.page.setViewportSize({ width: 1440, height: 1500 });
      await s.go("/monitoring");
      const card = healthCard(s);
      const header = card.locator("button[aria-expanded]");
      const crop = { crop: card, scroll: false, pad: 16 };
      await s.shot(1, header.locator("h2 span").last(), crop);
      await s.shot(2, card.locator("[class*=healthComponents]"), crop);
      await s.shot(3, header.locator("[class*=cardHeaderMeta]"), crop);
      await s.click(header, { wait: 500 });
      const b = await card.boundingBox();
      const top = { crop: { x: b.x - 16, y: b.y - 16, width: b.width + 32, height: 760 }, scroll: false };
      await s.shot(4, card.locator("[class*=healthLoops]"), top);
      await s.shot(5, card.locator("tbody tr").first(), top);
      await s.shot(6, header.locator("[class*=cardHeaderMeta]"), top);
    },
    // s02 Read the cluster overview
    async (s) => {
      await s.go("/monitoring");
      const cards = s.page.locator('[data-guide="monitoring-overview"] > div');
      await s.shot(1, [cards.nth(0), cards.nth(1), cards.nth(2), cards.nth(3)], { crop: main(0, 600), scroll: false });
      s.m.state.pveDown = true;
      await s.go("/monitoring");
      // The overview cards are a small share of the screen, so the error state can fall under the same-screen threshold.
      await s.shot(2, s.page.getByText(s.t("Unable to fetch monitoring data", "無法取得監控資料")), { crop: main(0, 600), scroll: false, newFrame: true });
    },
    // s03 Review and acknowledge active alerts
    async (s) => {
      // Tall window: the panels sit mid-screen, away from the floating AI mascot in the bottom-right corner.
      await s.page.setViewportSize({ width: 1440, height: 1500 });
      await s.go("/monitoring");
      const panels = s.page.locator('[data-guide="monitoring-panels"]');
      await panels.scrollIntoViewIfNeeded();
      const rows = panels.locator("[class*=alertRow]");
      const vmRow = rows.filter({ hasText: "10388" });
      await s.click(vmRow.getByRole("button", { name: s.t("Acknowledge", "確認") }), { wait: 500 });
      const crop = { crop: panels, scroll: false, pad: 16 };
      await s.shot(1, tab(s, "Active Alerts", "活動警告"), crop);
      await s.shot(2, [rows.nth(0).locator("[class*=alertHead]"), rows.nth(2).locator("[class*=alertHead]")], crop);
      await s.shot(3, [rows.nth(1).getByRole("button"), vmRow.locator("[class*=alertTime]")], crop);
    },
    // s04 Handle a mining incident
    async (s) => {
      // Wider window: at 1440 px the incident table scrolls sideways. 100 px taller than the
      // 1000 px crop of the last frame, so the floating AI mascot stays out of it.
      await s.page.setViewportSize({ width: 1640, height: 1100 });
      await s.go("/monitoring");
      const panels = s.page.locator('[data-guide="monitoring-panels"]');
      await s.click(tab(s, "Mining Incidents", "挖礦事件"), { wait: 500 });
      await panels.scrollIntoViewIfNeeded();
      const crop = { crop: panels, scroll: false, pad: 16 };
      const row = (vmid) => panels.locator("tbody tr").filter({ hasText: String(vmid) }).first();
      const r = row(10388);
      await s.shot(1, tab(s, "Mining Incidents", "挖礦事件"), crop);
      await s.shot(2, [r.locator("td").nth(0).locator("a"), r.locator("td").nth(4), r.locator("td").nth(5).locator("span").first()], crop);
      // Ban: the confirmation
      await s.click(r.getByRole("button", { name: s.t("Ban", "停權"), exact: true }), { wait: 500 });
      const confirmDlg = s.page.locator('[role="alertdialog"]');
      await s.shot(3, confirmDlg.getByRole("button", { name: s.t("Confirm Ban", "確認停權") }));
      await s.click(confirmDlg.getByRole("button", { name: s.t("Cancel", "取消") }), { wait: 500 });
      // Dismiss as false positive: the dialog
      await s.click(r.getByRole("button", { name: s.t("Dismiss as False Positive", "誤判解除") }), { wait: 500 });
      const dlg = s.page.locator('[role="dialog"]').last();
      await dlg.locator("input[type=checkbox]").check();
      await dlg.locator("textarea").fill(s.m.L("Final project: protein-folding simulation for Prof. Chen's lab", "期末專題：陳老師實驗室的蛋白質摺疊模擬"));
      await s.settle(200);
      await s.shot(3, [dlg.locator("label", { has: s.page.locator("input[type=checkbox]") }), dlg.locator("textarea"), dlg.getByRole("button", { name: s.t("Confirm Dismiss", "確認解除") })]);
      await s.click(dlg.getByRole("button", { name: s.t("Confirm Dismiss", "確認解除") }), { wait: 300 });
      await waitToast(s);
      await s.page.locator('[role="dialog"]').waitFor({ state: "detached" }).catch(() => {});
      await s.settle(300);
      await panels.scrollIntoViewIfNeeded();
      await panels.evaluate((el) => el.querySelectorAll("*").forEach((n) => { if (n.scrollLeft) n.scrollLeft = 0; }));
      await s.shot(4, [toastOf(s), row(10388).locator("td").nth(5), row(10277).locator("td").nth(5).locator("span").first()], { crop: { x: 336, y: 0, width: 1304, height: 1000 }, scroll: false });
    },
    // s05 Look at usage per node
    async (s) => {
      // Wider window: at 1440 px the node names wrap onto two lines. Tall enough that the card is not
      // at the bottom edge, where the floating AI mascot sits.
      await s.page.setViewportSize({ width: 1640, height: 1500 });
      await s.go("/monitoring");
      const card = s.page.locator('[data-guide="monitoring-nodes"]');
      const header = card.locator("button[aria-expanded]");
      await s.click(header, { wait: 400 });
      const r = card.locator("tbody tr").filter({ hasText: "pve-02" }).first();
      await r.click();
      await s.settle(800);
      await card.evaluate((el) => el.scrollIntoView({ block: "center" }));
      await s.page.mouse.move(5, 5);
      await s.settle(200);
      const crop = { crop: card, scroll: false, pad: 16 };
      await s.shot(1, header.locator("[class*=cardHeaderMeta]"), crop);
      await s.shot(2, card.locator("tbody tr").filter({ hasText: "pve-01" }).first(), crop);
      await s.shot(3, [r.locator("td").first(), card.locator("[class*=trExpand]")], crop);
    },
    // s06 Find the heaviest machines
    async (s) => {
      // Tall window: the whole page fits, so the cards are not at the bottom edge where the
      // floating AI mascot would peek into the crop.
      await s.page.setViewportSize({ width: 1440, height: 1500 });
      await s.go("/monitoring");
      const top = s.page.locator('[data-guide="monitoring-top"]');
      await s.shot(1, [top.locator("> div").nth(0), top.locator("> div").nth(1)], { crop: top, scroll: false, pad: 16 });
    },
    // s07 Open Grafana (the link is only photographed, never followed)
    async (s) => {
      await s.go("/monitoring");
      const link = s.page.locator('[data-guide="monitoring-grafana"]');
      await s.shot(1, link, { crop: main(0, 360), scroll: false });
      await s.shot(2, link, { crop: main(0, 360), scroll: false });
    },
  ],
};

void D;
