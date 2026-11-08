// @vitest-environment happy-dom
/**
 * 閒置偵測：剛開始閒置的第一個小時曾顯示「已閒置 0 小時」。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const { resources, specs, i18n } = vi.hoisted(() => ({
  i18n: { t: (key) => key, i18n: { language: "zh-TW" } },
  resources: { get: vi.fn() },
  specs: { listMy: vi.fn(), create: vi.fn(), cancel: vi.fn() },
}));

vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => i18n }));
vi.mock("../../../../../services/resources", () => ({ ResourcesService: resources }));
vi.mock("../../../../../services/specChangeRequests", () => ({ SpecChangeRequestsService: specs }));
vi.mock("../../../../../hooks/useToast", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock("../../../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => async () => false }));
/* 只看生命週期卡，其他卡片換成空殼 */
vi.mock("./FirewallCard", () => ({ default: () => null }));
vi.mock("./BootOptionsCard", () => ({ default: () => null }));
vi.mock("./CredentialsCard", () => ({ default: () => null }));
vi.mock("./SharingCard", () => ({ default: () => null }));

const { default: AdvancedSettingsTab } = await import("../AdvancedSettingsTab");
const { withQueryClient } = await import("../../../../../queries/testing");

let host, root;
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  specs.listMy.mockResolvedValue({ data: [] });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

const runningMachine = (idleMinutes) => ({
  vmid: 105, name: "lab-01", type: "lxc", status: "running", access_role: "owner", can_manage: true,
  expiry_date: null, public_urls: [],
  idle_since: new Date(Date.now() - idleMinutes * 60_000).toISOString(),
});

async function renderTab(machine) {
  resources.get.mockResolvedValue(machine);
  await act(async () => root.render(withQueryClient(<AdvancedSettingsTab vmid={105} backTo="/my-resources" />)));
  await flush();
  await flush();
}

it("a machine idle for less than an hour is not shown as idle for 0 hours", async () => {
  await renderTab(runningMachine(10));

  expect(host.textContent).toContain("LifecycleCard.idleUnderHour");
  expect(host.textContent).not.toContain("LifecycleCard.idleForHours");
});

it("a machine idle for a few hours still shows the hour count", async () => {
  await renderTab(runningMachine(150));

  expect(host.textContent).toContain("LifecycleCard.idleForHours");
  expect(host.textContent).not.toContain("LifecycleCard.idleUnderHour");
});
