// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const courses = { listSchedule: vi.fn() };
const resources = { list: vi.fn(), get: vi.fn(), start: vi.fn(), shutdown: vi.fn() };
const quickPractice = { listTemplates: vi.fn() };

const translation = { t: (key) => key, i18n: { language: "zh-TW" } };
vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => translation }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() } }));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1" } }) }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));
vi.mock("../../../services/courses", () => ({ CoursesService: courses }));
vi.mock("../../../services/resources", () => ({ ResourcesService: resources }));
vi.mock("../../../services/quickPractice", () => ({ QuickPracticeService: quickPractice }));
vi.mock("../resources/TerminalDialog", () => ({ default: () => null }));
vi.mock("../resources/VncDialog", () => ({ default: () => null }));

const { default: StudentHomePage } = await import("./StudentHomePage");
const { sessionDayLabel } = await import("./CourseTicket");

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

  await act(async () => root.render(<MemoryRouter><StudentHomePage /></MemoryRouter>));

  expect(courses.listSchedule).toHaveBeenCalledTimes(1);
  expect(resources.list).toHaveBeenCalledTimes(1);
  expect(quickPractice.listTemplates).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("Linux 入門");
  expect(host.textContent).toContain("lab-web");
});

it("labels course sessions by the Taipei calendar day", () => {
  /* 台北 9/28 00:30 ＝ UTC 9/27 16:30：UTC 還是 27 號，台北已經是 28 號 */
  const now = Date.parse("2026-09-27T16:30:00Z");
  expect(sessionDayLabel("2026-09-28", now, "en")).toBe("Today");
  expect(sessionDayLabel("2026-09-29", now, "en")).toBe("Tomorrow");
  expect(sessionDayLabel("", now, "en")).toBe("");
});
