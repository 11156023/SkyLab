// @vitest-environment happy-dom
/**
 * 回歸：停在「進階設定」分頁等延期審核，管理員核准後「審核中」提示消失、「申請延長」鈕重新亮起，
 * 到期日卻還是舊的。
 *
 * 改版前 LifecycleCard 的申請進度只在掛載時抓一次（loadRequests，LifecycleCard.jsx@ce7675b9^ 第 98–112 行），
 * 機器資料也是 AdvancedSettingsTab 進分頁時抓一次：兩份一起停在進頁當下（舊，但彼此一致：提示仍寫審核中）。
 * 改版後申請進度改用 useMySpecChangeRequestsQuery（每 30 秒輪詢），機器資料是
 * useResourceQuery(vmid, { refetchInterval: false })（這個分頁不輪詢）。核准後下一輪申請輪詢把提示拿掉，
 * 但沒有任何東西去重抓機器 → 到期日停在舊值、申請鈕又可按（ExtendModal 的最小日期也照舊值算），
 * 看起來像申請被吃掉；要切回視窗或換分頁才會更新。
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
const { createTestQueryClient, withQueryClient } = await import("../../../../../queries/testing");
const { specChangeRequestKeys } = await import("../../../../../queries/specChangeRequests");
const { formatDate } = await import("../lifecycleFormat");

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
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

const machine = (expiry) => ({
  vmid: 105, name: "lab-01", type: "lxc", status: "stopped", access_role: "owner", can_manage: true,
  expiry_date: expiry, public_urls: [],
});
const expiryRequest = (status) => ({
  id: "s1", vmid: 105, change_type: "expiry", status,
  current_expiry_date: "2026-11-01", requested_expiry_date: "2026-12-01",
});

it("refreshes the expiry date when the pending extension it was waiting on gets approved", async () => {
  resources.get.mockResolvedValue(machine("2026-11-01"));
  specs.listMy.mockResolvedValue({ data: [expiryRequest("pending")] });
  const client = createTestQueryClient();

  await act(async () => root.render(withQueryClient(<AdvancedSettingsTab vmid={105} backTo="/my-resources" />, client)));
  await flush();
  await flush();
  expect(host.textContent).toContain("LifecycleCard.pendingTitle");
  expect(host.textContent).toContain(formatDate("2026-11-01", "zh-TW"));

  /* 管理員核准：到期日延到 12/1 */
  resources.get.mockResolvedValue(machine("2026-12-01"));
  specs.listMy.mockResolvedValue({ data: [expiryRequest("approved")] });

  /* 「我的申請」的 30 秒輪詢 */
  await act(async () => { await client.refetchQueries({ queryKey: specChangeRequestKeys.mine }); });
  await flush();
  await flush();

  expect(host.textContent).not.toContain("LifecycleCard.pendingTitle");
  /* 提示拿掉時，到期日要是核准後的新值（改版前兩者一起停在進頁當下，不會出現「沒有審核中、日期卻沒變」） */
  expect(host.textContent).toContain(formatDate("2026-12-01", "zh-TW"));
});
