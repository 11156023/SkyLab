// @vitest-environment happy-dom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import TeacherDashboardPage, {
  buildTeacherDashboardDemo,
  nextClassSession,
  teacherDisplayName,
} from "./TeacherDashboardPage";
import { createTestQueryClient, withQueryClient } from "../../../../queries/testing";

const mocks = vi.hoisted(() => ({ list: vi.fn(), toastError: vi.fn() }));
vi.mock("react-i18next", async (original) => ({ ...await original(),
  useTranslation: () => ({ t: (key) => key, i18n: { language: "zh-TW" } }),
}));
vi.mock("../../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { full_name: "Amy Chen" } }) }));
vi.mock("../../../../hooks/useToast", () => {
  const api = { success: vi.fn(), error: (message) => mocks.toastError(message) };
  return { useToast: () => api };
});
vi.mock("../../../../services/teachingClasses", () => ({ TeachingClassesService: { list: mocks.list } }));

/* TanStack Query 以 macrotask 批次通知，等一個 timer tick 再看畫面 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

async function renderPage(client) {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(withQueryClient(createElement(MemoryRouter, null, createElement(TeacherDashboardPage)), client)));
  await flush();
  return { host, unmount: async () => { await act(async () => root.unmount()); host.remove(); } };
}

describe("TeacherDashboardPage", () => {
  it("counts active classes from the shared class list", async () => {
    mocks.list.mockReset().mockResolvedValue({ data: [
      { id: 1, name: "Linux A", status: "active" },
      { id: 2, name: "Docker", status: "planning" },
    ] });
    const page = await renderPage(createTestQueryClient());
    const metrics = page.host.querySelector('[data-guide="teacher-home-metrics"]');
    expect(metrics.querySelector("strong").textContent).toBe("1");
    await page.unmount();
  });

  it("shows dashes and toasts once when the first load fails, even if a retry fails the same way", async () => {
    mocks.toastError.mockReset();
    mocks.list.mockReset().mockRejectedValue(new Error("班級載入失敗"));
    const client = createTestQueryClient();
    const page = await renderPage(client);
    const metrics = page.host.querySelector('[data-guide="teacher-home-metrics"]');
    expect(metrics.querySelector("strong").textContent).toBe("—");
    expect(mocks.toastError).toHaveBeenCalledExactlyOnceWith("班級載入失敗");

    await act(async () => { await client.refetchQueries({ queryKey: ["teachingClasses", "list"] }); });
    await flush();
    expect(mocks.list).toHaveBeenCalledTimes(2);
    expect(mocks.toastError).toHaveBeenCalledTimes(1);
    await page.unmount();
  });
});

describe("teacherDisplayName", () => {
  it("uses the first part of the full name", () => {
    expect(teacherDisplayName({ full_name: "  Amy Chen ", email: "amy@x.edu" })).toBe("Amy");
  });

  it("returns null (no email fallback) when the full name is blank or whitespace", () => {
    expect(teacherDisplayName({ full_name: "   ", email: "amy@x.edu" })).toBeNull();
    expect(teacherDisplayName({ full_name: "", email: "bob@x.edu" })).toBeNull();
    expect(teacherDisplayName(null)).toBeNull();
  });
});

describe("nextClassSession", () => {
  it("returns today's later session before rolling to next week", () => {
    const now = new Date("2026-08-12T10:00:00+08:00");
    const next = nextClassSession({
      start_date: "2026-08-01",
      end_date: "2026-12-31",
      weekday: 2,
      start_time: "13:10:00",
    }, now);

    expect(next.toISOString()).toBe("2026-08-12T05:10:00.000Z");
  });
});

describe("teacher dashboard demo data", () => {
  it("fills every existing dashboard section without changing production data", () => {
    const now = new Date("2026-09-17T10:00:00+08:00");
    const demo = buildTeacherDashboardDemo(now);

    expect(demo.classes).toHaveLength(3);
    expect(nextClassSession(demo.classes[0], now)).not.toBeNull();
  });
});
