// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  ban: vi.fn(),
  confirm: vi.fn(),
  t: (key, params) => (params?.vmid ? `${key}:${params.vmid}` : key),
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock("../../../services/miningIncidents", () => ({
  MiningIncidentsService: { list: mocks.list, ban: mocks.ban, dismiss: vi.fn() },
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
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
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

async function renderPanel(props = {}) {
  await act(async () => root.render(withQueryClient(
    <MemoryRouter>
      <MiningIncidentsPanel {...props} />
    </MemoryRouter>,
  )));
  await flush();
}

test("顯示擁有者並把 VMID 連到資源詳情", async () => {
  mocks.list.mockResolvedValue([
    { ...BASE, id: "i1", vmid: 10388, owner_email: "s1110001@example.edu", owner_full_name: "王小明" },
    { ...BASE, id: "i2", vmid: 30215, owner_email: "s1120001@example.edu", owner_full_name: null },
    { ...BASE, id: "i3", vmid: 20419, owner_email: null, owner_full_name: null },
  ]);
  await renderPanel();

  const rows = [...host.querySelectorAll("tbody tr")];
  const link = rows[0].querySelector("a");
  expect(link.getAttribute("href")).toBe("/resource-mgmt/10388");
  expect(link.getAttribute("title")).toBe("MiningIncidentsPanel.openResourceDetail:10388");

  // 有姓名：姓名＋email；只有 email：只顯示 email 一次；查不到：未知使用者
  const ownerCell = (row) => row.querySelectorAll("td")[1].textContent;
  expect(ownerCell(rows[0])).toBe("王小明s1110001@example.edu");
  expect(ownerCell(rows[1])).toBe("s1120001@example.edu");
  expect(ownerCell(rows[2])).toBe("MiningIncidentsPanel.ownerUnknown");
});

test("首次就抓不到事件時顯示空狀態並回報 0 筆待處理", async () => {
  mocks.list.mockRejectedValue(new Error("503"));
  const onCountChange = vi.fn();
  await renderPanel({ onCountChange });

  expect(host.textContent).toContain("MiningIncidentsPanel.emptyNone");
  expect(onCountChange).toHaveBeenLastCalledWith(0);
});

test("停權後重抓清單，待處理數跟著更新", async () => {
  mocks.list
    .mockResolvedValueOnce([{ ...BASE, id: "i1", vmid: 10388, owner_email: null, owner_full_name: null }])
    .mockResolvedValueOnce([{ ...BASE, id: "i1", vmid: 10388, status: "banned", owner_email: null, owner_full_name: null }]);
  mocks.confirm.mockResolvedValue(true);
  mocks.ban.mockResolvedValue({});
  const onCountChange = vi.fn();
  await renderPanel({ onCountChange });
  expect(onCountChange).toHaveBeenLastCalledWith(1);

  const banButton = [...host.querySelectorAll("button")].find((b) => b.textContent.includes("MiningIncidentsPanel.ban"));
  await act(async () => banButton.click());
  await flush();

  expect(mocks.ban).toHaveBeenCalledWith("i1");
  expect(mocks.list).toHaveBeenCalledTimes(2);
  expect(onCountChange).toHaveBeenLastCalledWith(0);
  expect(host.textContent).toContain("MiningIncidentsPanel.statusBanned");
});
