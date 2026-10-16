// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  t: (key, params) => (params?.vmid ? `${key}:${params.vmid}` : key),
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock("../../../services/miningIncidents", () => ({
  MiningIncidentsService: { list: mocks.list, ban: vi.fn(), dismiss: vi.fn() },
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

const { default: MiningIncidentsPanel } = await import("./MiningIncidentsPanel");

const BASE = {
  avg_cpu: 96.8,
  window_hours: 6,
  snapshot_name: "mining-202610160412",
  status: "suspended",
  review_note: null,
  detected_at: "2026-10-15T20:12:00Z",
};

let host;
let root;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function flush() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  });
}

test("顯示擁有者並把 VMID 連到資源詳情", async () => {
  mocks.list.mockResolvedValue([
    { ...BASE, id: "i1", vmid: 10388, owner_email: "11156077@ntub.edu.tw", owner_full_name: "王小明" },
    { ...BASE, id: "i2", vmid: 30215, owner_email: "11256003@ntub.edu.tw", owner_full_name: null },
    { ...BASE, id: "i3", vmid: 20419, owner_email: null, owner_full_name: null },
  ]);
  await act(async () => root.render(
    <MemoryRouter>
      <MiningIncidentsPanel />
    </MemoryRouter>,
  ));
  await flush();

  const rows = [...host.querySelectorAll("tbody tr")];
  const link = rows[0].querySelector("a");
  expect(link.getAttribute("href")).toBe("/resource-mgmt/10388");
  expect(link.getAttribute("title")).toBe("MiningIncidentsPanel.openResourceDetail:10388");

  // 有姓名：姓名＋email；只有 email：只顯示 email 一次；查不到：未知使用者
  const ownerCell = (row) => row.querySelectorAll("td")[1].textContent;
  expect(ownerCell(rows[0])).toBe("王小明11156077@ntub.edu.tw");
  expect(ownerCell(rows[1])).toBe("11256003@ntub.edu.tw");
  expect(ownerCell(rows[2])).toBe("MiningIncidentsPanel.ownerUnknown");
});
