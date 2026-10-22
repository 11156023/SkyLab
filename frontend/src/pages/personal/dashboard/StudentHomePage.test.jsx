// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const courses = { listSchedule: vi.fn() };
const resources = { list: vi.fn(), get: vi.fn(), start: vi.fn(), shutdown: vi.fn() };
const quickPractice = { listTemplates: vi.fn(), listMySessions: vi.fn() };
const confirmMock = vi.fn();

const translation = { t: (key) => key, i18n: { language: "zh-TW" } };
vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => translation }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() } }));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1" } }) }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => confirmMock }));
vi.mock("../../../services/courses", () => ({ CoursesService: courses }));
vi.mock("../../../services/resources", () => ({ ResourcesService: resources }));
vi.mock("../../../services/quickPractice", () => ({ QuickPracticeService: quickPractice }));
vi.mock("../resources/TerminalDialog", () => ({ default: () => null }));
vi.mock("../resources/VncDialog", () => ({ default: () => null }));

const { default: StudentHomePage } = await import("./StudentHomePage");
const { sessionDayLabel } = await import("./CourseTicket");
const { createTestQueryClient, withQueryClient } = await import("../../../queries/testing");
const { resourceKeys } = await import("../../../queries/resources");

/* TanStack Query 以 macrotask 批次通知，等一個 timer tick 再看畫面 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  vi.clearAllMocks();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it("renders the home overview from the schedule, resources and quick templates only", async () => {
  courses.listSchedule.mockResolvedValue([
    { id: "p1", title: "Linux 入門", state: "now", start_at: "2026-09-27T01:00:00Z", end_at: "2026-09-27T03:00:00Z" },
  ]);
  resources.list.mockResolvedValue([{ vmid: 101, name: "lab-web", type: "lxc", status: "running" }]);
  quickPractice.listTemplates.mockResolvedValue([]);
  quickPractice.listMySessions.mockResolvedValue([]);

  await act(async () => root.render(withQueryClient(<MemoryRouter><StudentHomePage /></MemoryRouter>)));
  await flush();

  expect(courses.listSchedule).toHaveBeenCalledTimes(1);
  expect(resources.list).toHaveBeenCalledTimes(1);
  expect(quickPractice.listTemplates).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("Linux 入門");
  expect(host.textContent).toContain("lab-web");
});

it("marks the shut-down machine as stopped in the shared resources cache", async () => {
  courses.listSchedule.mockResolvedValue([]);
  resources.list.mockResolvedValue([
    { vmid: 101, name: "lab-web", type: "lxc", status: "running" },
    { vmid: 102, name: "lab-db", type: "lxc", status: "running" },
  ]);
  resources.shutdown.mockResolvedValue({});
  quickPractice.listTemplates.mockResolvedValue([]);
  quickPractice.listMySessions.mockResolvedValue([]);
  confirmMock.mockResolvedValue(true);
  const client = createTestQueryClient();

  await act(async () => root.render(withQueryClient(<MemoryRouter><StudentHomePage /></MemoryRouter>, client)));
  await flush();

  const shutdown = host.querySelector('button[aria-label="HomeOverview.shutdownAria"]');
  expect(shutdown).toBeTruthy();
  await act(async () => shutdown.click());
  await flush();

  expect(resources.shutdown).toHaveBeenCalledWith(101);
  /* 只改那一台，不整頁重抓 */
  expect(resources.list).toHaveBeenCalledTimes(1);
  expect(client.getQueryData(resourceKeys.mine).resources.map((item) => item.status)).toEqual(["stopped", "running"]);
});

it("keeps the page usable when only the schedule fails", async () => {
  courses.listSchedule.mockRejectedValue(new Error("500"));
  resources.list.mockResolvedValue([{ vmid: 101, name: "lab-web", type: "lxc", status: "running" }]);
  quickPractice.listTemplates.mockResolvedValue([]);
  quickPractice.listMySessions.mockResolvedValue([]);

  await act(async () => root.render(withQueryClient(<MemoryRouter><StudentHomePage /></MemoryRouter>)));
  await flush();

  expect(host.textContent).not.toContain("StudentHomePage.loadingLabel");
  expect(host.textContent).toContain("lab-web");
});

it("keeps the failed-machines panel up during a background retry instead of flashing back to loading", async () => {
  /* 首載失敗後背景輪詢重抓：TanStack v5 沒有資料時重抓會把 status 改回 pending、error 清空，
     失敗畫面不能每輪都閃回整頁載入中（見 useQueryView） */
  courses.listSchedule.mockResolvedValue([]);
  resources.list.mockRejectedValueOnce(new Error("500"));
  quickPractice.listTemplates.mockResolvedValue([]);
  quickPractice.listMySessions.mockResolvedValue([]);
  const client = createTestQueryClient();

  await act(async () => root.render(withQueryClient(<MemoryRouter><StudentHomePage /></MemoryRouter>, client)));
  await flush();
  expect(host.textContent).toContain("HomeOverview.resourcesFailed");

  let reject;
  resources.list.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
  act(() => { client.refetchQueries({ queryKey: resourceKeys.mine }); });
  await flush();
  /* 重抓還在途中：失敗畫面照樣在，沒有閃成載入中 */
  expect(resources.list).toHaveBeenCalledTimes(2);
  expect(host.textContent).not.toContain("StudentHomePage.loadingLabel");
  expect(host.textContent).toContain("HomeOverview.resourcesFailed");

  await act(async () => reject(new Error("still down")));
  await flush();
  expect(host.textContent).toContain("HomeOverview.resourcesFailed");
});

it("labels course sessions by the Taipei calendar day", () => {
  /* 台北 9/28 00:30 ＝ UTC 9/27 16:30：UTC 還是 27 號，台北已經是 28 號 */
  const now = Date.parse("2026-09-27T16:30:00Z");
  expect(sessionDayLabel("2026-09-28", now, "en")).toBe("Today");
  expect(sessionDayLabel("2026-09-29", now, "en")).toBe("Tomorrow");
  expect(sessionDayLabel("", now, "en")).toBe("");
});
