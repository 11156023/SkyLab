// Manual page: manual/gpu-management.md
import { endFrame, newFrame, stableTokens } from "../mocks/infra.mjs";

// n consecutive PCI functions starting at bus:dev.fn (function is 3 bits).
function vfs(node, bus, startOrd, n) {
  return Array.from({ length: n }, (_, i) => {
    const ord = startOrd + i;
    const dev = Math.floor(ord / 8).toString(16).padStart(2, "0");
    return { id: "10de:2335", node, path: `0000:${bus}:${dev}.${ord % 8}`, iommugroup: 140 + i };
  });
}

function mappings(m) {
  const vm = (vmid, vm_name, status = "running", node = "gpu-01") => ({ vmid, vm_name, node, status, mdev_type: "", allocated_vram_mb: 0 });
  return [
    {
      id: "h200-gpu01", description: "NVIDIA H200 141GB",
      maps: vfs("gpu-01", "17", 4, 32), physical_gpu_count: 1, device_count: 32, capacity_count: 17, used_count: 6, available_count: 11,
      is_sriov: true, has_mdev: true, total_vram_mb: 17 * 8192, used_vram_mb: 6 * 8192, used_vram_known: true, per_instance_vram_mb: 8192, mdev_profile: "NVIDIA H200-8C",
      used_by: [vm(10412, "llm-finetune-11156012"), vm(10415, "rag-lab-11256003"), vm(10418, "yolo-train-11156034"), vm(10421, "whisper-asr-lab", "stopped"), vm(10433, "sd-webui-11256045"), vm(10440, "nlp-thesis-lin")],
    },
    {
      id: "h200-gpu02", description: m.L("NVIDIA H200 141GB (research)", "NVIDIA H200 141GB（研究用）"),
      maps: vfs("gpu-02", "17", 4, 32), physical_gpu_count: 1, device_count: 32, capacity_count: 32, used_count: 3, available_count: 21,
      is_sriov: true, has_mdev: true, total_vram_mb: 35 * 4096, used_vram_mb: 36864, used_vram_known: false, per_instance_vram_mb: 4096, mdev_profile: "NVIDIA H200-4C",
      used_by: [vm(10501, "protein-fold-chen", "running", "gpu-02"), vm(10502, "llama-70b-eval", "running", "gpu-02"), vm(10507, "vision-lab-wu", "stopped", "gpu-02")],
    },
    {
      id: "rtx4090", description: "NVIDIA GeForce RTX 4090 24GB",
      maps: [{ id: "10de:2684", node: "gpu-01", path: "0000:41:00.0" }, { id: "10de:2684", node: "gpu-01", path: "0000:c1:00.0" }],
      physical_gpu_count: 2, device_count: 2, capacity_count: 2, used_count: 2, available_count: 0, is_sriov: false, has_mdev: false,
      total_vram_mb: 2 * 24576, used_vram_mb: 2 * 24576, used_vram_known: true, per_instance_vram_mb: 0, mdev_profile: "",
      used_by: [vm(10377, "unity-render-farm"), vm(10380, "cuda-course-demo")],
    },
    {
      id: "tesla-m60", description: "NVIDIA Tesla M60 8G",
      maps: [{ id: "10de:13f2", node: "pve-03", path: "0000:84:00.0" }, { id: "10de:13f2", node: "pve-03", path: "0000:85:00.0" }],
      physical_gpu_count: 2, device_count: 2, capacity_count: 2, used_count: 0, available_count: 2, is_sriov: false, has_mdev: false,
      total_vram_mb: 2 * 8192, used_vram_mb: 0, used_vram_known: true, per_instance_vram_mb: 0, mdev_profile: "", used_by: [],
    },
    {
      id: "a100-oldlab", description: m.L("NVIDIA A100 40GB (old lab)", "NVIDIA A100 40GB（舊機房）"),
      maps: [{ id: "10de:20f1", node: "lab-pve", path: "0000:3b:00.0" }],
      physical_gpu_count: 1, device_count: 0, capacity_count: 0, used_count: 0, available_count: 0, is_sriov: false, has_mdev: false,
      total_vram_mb: 0, used_vram_mb: 0, used_vram_known: true, per_instance_vram_mb: 0, mdev_profile: "", used_by: [],
    },
  ];
}

const row = (s, id) => s.page.locator("tbody tr", { has: s.page.getByText(id, { exact: true }) }).first();
// The table needs 1080 px; at 1440 px it scrolls sideways, so widen the window.
const WIDE = { width: 1520, height: 900 };
const area = (top = 0, height = 900) => ({ x: 336, y: top, width: 1184, height });
async function open(s) {
  await s.page.setViewportSize(WIDE);
  await s.go("/gpu-mgmt");
}
const search = (s) => s.page.locator('[data-guide="gpu-mgmt-search"] input');

export default {
  page: "gpu-management",
  role: "admin",
  mocks(m) {
    stableTokens(m);
    m.state.maps = mappings(m);
    m.use([
      ["GET", /^\/api\/v1\/gpu\/mappings$/, () => ({ data: m.state.maps, count: m.state.maps.length })],
      ["DELETE", /^\/api\/v1\/gpu\/mappings\/([^/]+)$/, ({ mt }) => {
        m.state.maps = m.state.maps.filter((x) => x.id !== decodeURIComponent(mt[1]));
        return { __delay: 300, __body: { message: "ok" } };
      }],
    ]);
  },
  sections: [
    // s01 Find a mapping
    async (s) => {
      await open(s);
      await s.fill(search(s), "gpu-02", { wait: 300 });
      await s.shot(1, search(s), { crop: area(0, 300), scroll: false });
      await s.shot(2, row(s, "h200-gpu02").locator("td").nth(2), { crop: area(0, 300), scroll: false });
      await s.fill(search(s), "a6000", { wait: 300 });
      // The empty table changes too few pixels to start a new frame on its own.
      await newFrame(s, 640);
      await s.shot(3, s.page.getByRole("button", { name: s.t("Clear search", "清除搜尋") }), { crop: area(0, 620), scroll: false });
      await endFrame(s);
    },
    // s02 Read a mapping row
    async (s) => {
      await open(s);
      const r = row(s, "h200-gpu01");
      const td = (i) => r.locator("td").nth(i);
      await s.shot(1, td(0), { crop: area(), scroll: false });
      await s.shot(2, td(1), { crop: area(), scroll: false });
      await s.shot(3, td(2), { crop: area(), scroll: false });
      await s.shot(4, td(3), { crop: area(), scroll: false });
      await s.shot(5, r.locator("td").nth(4).locator("span").first(), { crop: area(), scroll: false });
      await s.shot(6, [td(5).locator("span").first(), row(s, "rtx4090").locator("td").nth(5).locator("span").first(), row(s, "tesla-m60").locator("td").nth(5).locator("span").first(), row(s, "a100-oldlab").locator("td").nth(5).locator("span").first()], { crop: area(), scroll: false });
    },
    // s03 Expand a long PCI list
    async (s) => {
      await open(s);
      const r = row(s, "h200-gpu01");
      const chip = r.locator("button[aria-expanded]");
      await s.shot(1, r.locator("td").nth(2).locator("code").first(), { crop: area(), scroll: false });
      await s.click(chip, { wait: 400 });
      const detail = r.locator("xpath=following-sibling::tr[1]");
      await s.shot(2, [chip, detail], { crop: area(), scroll: false });
      await s.shot(3, chip, { crop: area(), scroll: false });
    },
    // s04 Read capacity and VRAM
    async (s) => {
      await open(s);
      const cap = (id) => row(s, id).locator("td").nth(3);
      await s.shot(1, cap("h200-gpu01").locator("div").nth(1), { crop: area(), scroll: false });
      await s.shot(2, [cap("rtx4090").locator("div").last(), cap("h200-gpu02").locator("div").last()], { crop: area(), scroll: false });
    },
    // s05 Remove a mapping
    async (s) => {
      await open(s);
      const del = s.page.getByRole("button", { name: `${s.t("Remove Mapping", "移除映射")} a100-oldlab` });
      // Down to the last row: its delete button is the target.
      await s.shot(1, del, { crop: area(0, 820) });
      await s.click(del, { wait: 500 });
      await s.shot(2, s.page.locator('[role="alertdialog"]').getByRole("button", { name: s.t("Delete", "刪除"), exact: true }));
      await s.click(s.page.locator('[role="alertdialog"]').getByRole("button", { name: s.t("Delete", "刪除"), exact: true }), { wait: 900 });
      await s.shot(3, [s.page.locator("[data-sonner-toast]").first(), s.page.locator("tbody")], { crop: area(0, 720), scroll: false });
    },
  ],
};
