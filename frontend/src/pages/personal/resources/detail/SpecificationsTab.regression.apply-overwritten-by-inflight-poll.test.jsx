// @vitest-environment happy-dom
/**
 * 回歸：按「套用新規格」後，在途的背景重抓把「套用中」蓋回「可套用」，套用鈕又出現。
 *
 * 改版前 SpecificationsTab 的申請進度只在進頁時抓一次、套用中才輪詢（SpecificationsTab.jsx@ce7675b9^ 第 258–286 行），
 * 按套用時沒有任何背景請求在路上，setOpenRequest(res.request) 之後畫面就停在「套用中」。
 * 改版後「我的規格調整申請」走 useMySpecChangeRequestsQuery：平常每 30 秒輪詢、切回視窗也重抓
 * （清單端點要問 PVE 拿機器名稱，一次常要好幾秒）。handleApply 用 useSpecChangeRequestCacheUpdater().update()
 * 直接 setQueryData，前面沒有 `await cancelQueries`——按套用之前就送出的那次重抓（例如收到核准通知、
 * 切回視窗馬上按套用）晚一步回來，會把剛寫進去的 apply_status:"applying" 蓋回 "ready"：
 * 通知列退回「已核准，可套用」、套用鈕重新出現；而且不是套用中，輪詢也退回 30 秒一次，
 * 這段期間使用者再按一次只會拿到錯誤。LifecycleCard 撤回延期申請（requestCache.update）是同一個寫法。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const { resources, specRequests, quotas, toastApi, auth, i18n } = vi.hoisted(() => ({
  i18n: { t: (key, opts) => (opts?.value != null ? `${key}:${opts.value}` : key), i18n: { language: "zh-TW" } },
  resources: { getSpecs: vi.fn(), get: vi.fn(), updateSpecDirect: vi.fn() },
  specRequests: { listMy: vi.fn(), create: vi.fn(), apply: vi.fn(), cancel: vi.fn() },
  quotas: { getMyUsage: vi.fn() },
  toastApi: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  auth: { user: { id: "u1", role: "student" } },
}));

vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => i18n }));
vi.mock("../../../../services/resources", () => ({ ResourcesService: resources }));
vi.mock("../../../../services/quotas", () => ({ QuotasService: quotas }));
vi.mock("../../../../services/specChangeRequests", async (original) => ({
  ...await original(),
  SpecChangeRequestsService: specRequests,
}));
vi.mock("../../../../hooks/useToast", () => ({ useToast: () => toastApi }));
vi.mock("../../../../contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("../../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => async () => true }));

import SpecificationsTab from "./SpecificationsTab";
import { createTestQueryClient, withQueryClient } from "../../../../queries/testing";
import { specChangeRequestKeys } from "../../../../queries/specChangeRequests";

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
  resources.get.mockResolvedValue({ vmid: 300, can_request_spec_change: true });
  resources.getSpecs.mockResolvedValue({ vmid: 300, resource_type: "lxc", cpu_cores: 2, memory_mb: 1024, disk_gb: 8 });
  quotas.getMyUsage.mockResolvedValue(null);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

const ready = {
  id: "r3", vmid: 300, status: "approved", apply_status: "ready",
  change_type: "combined", requested_memory: 2048, created_at: "2026-10-20T00:00:00Z",
};

it("keeps showing 'applying' when a background refresh started before the click lands afterwards", async () => {
  specRequests.listMy.mockResolvedValue({ data: [ready] });
  const client = createTestQueryClient();
  await act(async () => root.render(withQueryClient(<SpecificationsTab vmid={300} />, client)));
  await flush();
  await flush();
  expect(host.textContent).toContain("SpecificationsTab.noticeReady");

  /* 30 秒輪詢／切回視窗的重抓已經送出，伺服器讀到的還是按套用之前的狀態 */
  let finishStaleRefresh;
  specRequests.listMy.mockReturnValueOnce(new Promise((resolve) => { finishStaleRefresh = resolve; }));
  act(() => { client.refetchQueries({ queryKey: specChangeRequestKeys.mine }); });
  await flush();

  /* 使用者按「套用新規格」：後端回「套用中」 */
  specRequests.apply.mockResolvedValue({ request: { ...ready, apply_status: "applying" } });
  const applyButton = [...host.querySelectorAll("button")].find((b) => b.textContent.includes("SpecificationsTab.applyNewSpec"));
  await act(async () => applyButton.click());
  await flush();
  expect(specRequests.apply).toHaveBeenCalledWith("r3");
  expect(host.textContent).toContain("SpecificationsTab.noticeApplying");

  /* 按之前送出的那次重抓這時才回來（內容是舊的「可套用」） */
  await act(async () => finishStaleRefresh({ data: [ready] }));
  await flush();

  /* 改版前：沒有在途的背景請求，畫面停在「套用中」，套用鈕不會再出現 */
  expect(host.textContent).toContain("SpecificationsTab.noticeApplying");
  expect([...host.querySelectorAll("button")].some((b) => b.textContent.includes("SpecificationsTab.applyNewSpec"))).toBe(false);
});
