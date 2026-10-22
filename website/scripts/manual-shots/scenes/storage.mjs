// Manual page: manual/storage.md
import { endFrame, main, newFrame, stableTokens, storages, toastOf, waitToast } from "../mocks/infra.mjs";

const H = 1300; // every row fits without scrolling
const area = (top = 0, height = 1220) => main(top, height);

const row = (s, name, node) =>
  s.page.locator('[data-guide="storage-row"]').filter({ has: s.page.locator("[class*=rowName]", { hasText: new RegExp("^" + name + "(?![a-z0-9-])") }) }).filter({ hasText: node }).first();
const tier = (r) => r.locator('[data-guide="storage-speed-tier"] select');
const prio = (r) => r.locator('[data-guide="storage-priority"] input');
const enabled = (r) => r.locator('[data-guide="storage-enabled"]');
const meta = (r) => r.locator("[class*=rowMeta]");
const search = (s) => s.page.locator('[data-guide="storage-filters"] input');

async function open(s) {
  await s.page.setViewportSize({ width: 1440, height: H });
  await s.go("/storage");
}

export default {
  page: "storage",
  role: "admin",
  mocks(m) {
    stableTokens(m);
    m.state.list = storages(m);
    m.use([
      ["GET", /^\/api\/v1\/proxmox-config\/storages$/, () => m.state.list],
      ["PUT", /^\/api\/v1\/proxmox-config\/storages\/(\d+)$/, ({ mt, body }) => {
        const st = m.state.list.find((x) => x.id === Number(mt[1]));
        Object.assign(st, body);
        return { __delay: 250, __body: st };
      }],
    ]);
  },
  sections: [
    // s01 Find a storage
    async (s) => {
      await s.go("/storage"); // normal height: the empty result is centred in the window
      await s.fill(search(s), "pve-02", { wait: 300 });
      await s.shot(1, s.page.locator('[data-guide="storage-filters"] [role="radiogroup"], [data-guide="storage-filters"] > div').first(), { crop: area(0, 520), scroll: false });
      await s.shot(2, search(s), { crop: area(0, 520), scroll: false });
      await s.fill(search(s), "zfs", { wait: 300 });
      const clear = s.page.getByRole("button", { name: s.t("Clear filters", "清除篩選") });
      const b = await clear.boundingBox();
      const bottom = Math.round(b.y + b.height + 60);
      await newFrame(s, bottom + 20); // the emptied list alone is too small a change for a new frame
      await s.shot(3, clear, { crop: area(0, bottom), scroll: false });
      await endFrame(s);
    },
    // s02 Read a storage row
    async (s) => {
      await open(s);
      const lvm = row(s, "local-lvm", "pve-01");
      const ceph = row(s, "ceph-vm", "rbd");
      await s.shot(1, [
        lvm.locator("[class*=rowName] span").last(),
        ceph.locator("[class*=rowName] span").last(),
        ceph.locator("[class*=rowName] span").first(),
      ], { crop: area(0, 900), scroll: false });
      await s.shot(2, [meta(lvm), meta(row(s, "old-nfs", "nfs"))], { crop: area(0, 900), scroll: false });
      await s.shot(3, [meta(row(s, "local", "dir")), meta(row(s, "pbs-backup", "pbs"))], { crop: area(0, 900), scroll: false });
    },
    // s03 Set the speed tier
    async (s) => {
      await open(s);
      const r = row(s, "local-lvm", "pve-02");
      await tier(r).selectOption("nvme");
      await waitToast(s);
      await s.shot(1, tier(r), { crop: area(), scroll: false });
      await s.shot(2, toastOf(s), { crop: area(), scroll: false });
      await s.shot(3, tier(row(s, "infortrend-lvm", "lvm")), { crop: area(), scroll: false });
    },
    // s04 Set the user priority
    async (s) => {
      await open(s);
      const r3 = row(s, "local-lvm", "pve-03");
      await prio(r3).fill("3");
      await s.shot(1, prio(r3), { crop: area(0, 800), scroll: false });
      await prio(r3).press("Enter");
      await waitToast(s);
      await s.shot(2, [prio(r3), toastOf(s)], { crop: area(0, 800), scroll: false });
      const r1 = row(s, "local-lvm", "pve-01");
      await prio(r1).fill("15");
      await s.shot(3, prio(r1), { crop: area(0, 800), scroll: false });
      await s.shot(4, [prio(row(s, "local-lvm", "pve-02")), prio(row(s, "local", "dir"))], { crop: area(0, 800), scroll: false });
    },
    // s05 Enable or disable a storage
    async (s) => {
      await open(s);
      const r = row(s, "local-lvm", "pve-03");
      await enabled(r).locator("input").click();
      await waitToast(s);
      await s.shot(1, [enabled(r), toastOf(s)], { crop: area(), scroll: false });
      await s.shot(2, [enabled(row(s, "archive-hdd", "lvm")), enabled(row(s, "old-nfs", "nfs"))], { crop: area(), scroll: false });
    },
    // s06 Change shared storage
    async (s) => {
      await open(s);
      const r = row(s, "infortrend-lvm", "lvm");
      await tier(r).selectOption("hdd");
      await waitToast(s);
      await s.shot(1, [tier(r), r.locator("[class*=badge_info]")], { crop: area(), scroll: false });
      await s.shot(2, toastOf(s), { crop: area(), scroll: false });
    },
  ],
};
