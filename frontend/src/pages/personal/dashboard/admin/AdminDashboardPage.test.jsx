// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "../../../../queries/testing";
import { aiApiKeys } from "../../../../queries/aiApi";

const listAllRequests = vi.fn();
const listMining = vi.fn();
const listJobs = vi.fn();

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
vi.mock("../../../../services/jobs", () => ({ JobsService: { list: (...args) => listJobs(...args) } }));
vi.mock("../../../../services/miningIncidents", () => ({ MiningIncidentsService: { list: (...args) => listMining(...args) } }));
vi.mock("../../../../services/monitoring", () => ({ MonitoringService: { listAlerts: async () => [] } }));
vi.mock("../../../../services/specChangeRequests", () => ({ SpecChangeRequestsService: { listAll: async () => [] } }));
vi.mock("../../../../services/vmRequests", () => ({ VmRequestsService: { listAll: async () => [] } }));

const { default: AdminDashboardPage } = await import("./AdminDashboardPage");

let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.resetAllMocks();
  listJobs.mockResolvedValue([]);
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

async function renderPage(client) {
  await act(async () => root.render(withQueryClient(<MemoryRouter><AdminDashboardPage /></MemoryRouter>, client)));
  await flush();
}

function rowCount(titleKey) {
  const row = [...host.querySelectorAll("button")].find((button) => button.textContent.includes(titleKey));
  return row?.querySelector("b").textContent;
}

it("counts pending AI requests and open mining incidents from server-side status filters", async () => {
  /* 後端依狀態過濾後回總數，頁面只拿一筆；總數要用 count，不是 data 的長度 */
  listAllRequests.mockResolvedValue({ data: [{ id: "x", status: "pending" }], count: 7 });
  listMining.mockImplementation(async ({ status }) => (status === "detected" ? [{ id: 1 }, { id: 2 }] : [{ id: 3 }]));

  await renderPage();

  expect(listAllRequests).toHaveBeenCalledWith({ status: "pending", limit: 1 });
  expect(listMining).toHaveBeenCalledWith(expect.objectContaining({ status: "detected" }));
  expect(listMining).toHaveBeenCalledWith(expect.objectContaining({ status: "suspended" }));

  expect(rowCount("AdminDashboardPage.issueAiTitle")).toBe("7");
  expect(rowCount("AdminDashboardPage.issueMiningTitle")).toBe("3");
});

it("a failing source only blanks its own number and marks the page incomplete", async () => {
  listAllRequests.mockResolvedValue({ data: [], count: 4 });
  listMining.mockRejectedValue(new Error("503"));

  await renderPage();

  /* 其他來源照常顯示，不會整頁卡在「檢查中」或整頁失敗 */
  expect(host.textContent).not.toContain("AdminDashboardPage.checking");
  expect(rowCount("AdminDashboardPage.issueAiTitle")).toBe("4");
  expect(rowCount("AdminDashboardPage.issueMiningTitle")).toBeUndefined();
  expect(host.textContent).toContain("AdminDashboardPage.chipChecksUnavailable");
});

it("a failed background refresh keeps the previous count", async () => {
  listAllRequests
    .mockResolvedValueOnce({ data: [], count: 5 })
    .mockRejectedValueOnce(new Error("503"));
  listMining.mockResolvedValue([]);
  const client = createTestQueryClient();

  await renderPage(client);
  expect(rowCount("AdminDashboardPage.issueAiTitle")).toBe("5");

  await act(async () => { await client.refetchQueries({ queryKey: aiApiKeys.allRequests({ status: "pending", limit: 1 }) }); });
  await flush();

  expect(listAllRequests).toHaveBeenCalledTimes(2);
  expect(rowCount("AdminDashboardPage.issueAiTitle")).toBe("5");
  /* 背景重抓（含失敗）不回到「檢查中」 */
  expect(host.textContent).not.toContain("AdminDashboardPage.checking");
});
