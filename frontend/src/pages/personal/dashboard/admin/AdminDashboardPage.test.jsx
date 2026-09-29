// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const listAllRequests = vi.fn();
const listMining = vi.fn();

const translation = {
  t: (key, vars) => (vars ? `${key}(${Object.values(vars).join(",")})` : key),
  i18n: { language: "zh-TW" },
};
vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => translation }));
vi.mock("../../../../components/AiPveChat/AiPveChat", () => ({ default: () => null }));
vi.mock("../../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
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
  vi.clearAllMocks();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it("counts pending AI requests and open mining incidents from server-side status filters", async () => {
  /* 後端依狀態過濾後回總數，頁面只拿一筆；總數要用 count，不是 data 的長度 */
  listAllRequests.mockResolvedValue({ data: [{ id: "x", status: "pending" }], count: 7 });
  listMining.mockImplementation(async ({ status }) => (status === "detected" ? [{ id: 1 }, { id: 2 }] : [{ id: 3 }]));

  await act(async () => root.render(<MemoryRouter><AdminDashboardPage /></MemoryRouter>));

  expect(listAllRequests).toHaveBeenCalledWith({ status: "pending", limit: 1 });
  expect(listMining).toHaveBeenCalledWith(expect.objectContaining({ status: "detected" }));
  expect(listMining).toHaveBeenCalledWith(expect.objectContaining({ status: "suspended" }));

  const aiRow = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("AdminDashboardPage.issueAiTitle"));
  expect(aiRow.querySelector("b").textContent).toBe("7");
  const miningRow = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("AdminDashboardPage.issueMiningTitle"));
  expect(miningRow.querySelector("b").textContent).toBe("3");
});
