// @vitest-environment happy-dom
/**
 * 回歸：某個待辦來源持續失敗時，管理首頁的待辦區每 30 秒閃回「檢查中」。
 *
 * 改版前 loadChecks(silent) 的背景刷新從不碰 loading（只有進頁第一次是 false→true），
 * 失敗的來源照樣計進 unavailable，待辦區一直顯示、頁首一直掛著「資料不完整」。
 * 改版後 loading＝「任一支查詢 isPending」。TanStack v5 在「沒有資料」時每次重抓都把 status 改回
 * pending（error 清空）：從沒成功過的來源（例如挖礦事件端點一直 503）每 30 秒輪詢、或切回視窗重抓時，
 * isPending 變 true → busy → 整個待辦區換成「檢查中」轉圈圈，「資料不完整」狀態籤也同時消失，
 * 直到那支請求再次失敗。
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "../../../../queries/testing";
import { miningIncidentKeys } from "../../../../queries/miningIncidents";

const listAllRequests = vi.fn();
const listMining = vi.fn();

const translation = {
  t: (key, vars) => (vars ? `${key}(${Object.values(vars).join(",")})` : key),
  i18n: { language: "zh-TW" },
};
vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => translation }));
vi.mock("../../../../components/AiPveChat/AiPveChat", () => ({ default: () => null }));
vi.mock("../../../../hooks/usePveOverview", () => ({
  default: () => ({ overview: { issues: [], data_status: "ok", nodes_total: 1, nodes_online: 1 }, loading: false, error: false }),
}));
vi.mock("../../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { full_name: "Admin" } }) }));
vi.mock("../../../../services/aiApi", () => ({ AiApiService: { listAllRequests: (...args) => listAllRequests(...args) } }));
vi.mock("../../../../services/batchProvision", () => ({ BatchProvisionService: { listPending: async () => [] } }));
vi.mock("../../../../services/jobs", () => ({ JobsService: { list: async () => [] } }));
vi.mock("../../../../services/miningIncidents", () => ({ MiningIncidentsService: { list: (...args) => listMining(...args) } }));
vi.mock("../../../../services/monitoring", () => ({ MonitoringService: { listAlerts: async () => [] } }));
vi.mock("../../../../services/specChangeRequests", () => ({ SpecChangeRequestsService: { listAll: async () => [] } }));
vi.mock("../../../../services/vmRequests", () => ({ VmRequestsService: { listAll: async () => [] } }));

const { default: AdminDashboardPage } = await import("./AdminDashboardPage");

let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.resetAllMocks();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

function rowCount(titleKey) {
  const row = [...host.querySelectorAll("button")].find((button) => button.textContent.includes(titleKey));
  return row?.querySelector("b").textContent;
}

it("keeps the to-do list (and the incomplete chip) while a never-loaded source is retried in the background", async () => {
  listAllRequests.mockResolvedValue({ data: [], count: 4 });
  /* 挖礦事件端點從頭到尾都失敗 */
  listMining.mockRejectedValue(new Error("503"));
  const client = createTestQueryClient();

  await act(async () => root.render(withQueryClient(<MemoryRouter><AdminDashboardPage /></MemoryRouter>, client)));
  await flush();
  expect(host.textContent).not.toContain("AdminDashboardPage.checking");
  expect(rowCount("AdminDashboardPage.issueAiTitle")).toBe("4");
  expect(host.textContent).toContain("AdminDashboardPage.chipChecksUnavailable");

  /* 30 秒輪詢（或切回視窗）重抓失敗的來源：這次請求還在路上 */
  listMining.mockImplementation(() => new Promise(() => {}));
  act(() => { client.refetchQueries({ queryKey: miningIncidentKeys.all }); });
  await flush();
  expect(listMining).toHaveBeenCalledTimes(4);

  /* 改版前：背景刷新不顯示載入中，待辦照常列著、頁首照樣標「資料不完整」 */
  expect(host.textContent).not.toContain("AdminDashboardPage.checking");
  expect(rowCount("AdminDashboardPage.issueAiTitle")).toBe("4");
  expect(host.textContent).toContain("AdminDashboardPage.chipChecksUnavailable");
});
