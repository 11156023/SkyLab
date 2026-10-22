// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "../../queries/testing";
import { jobKeys } from "../../queries/jobs";
import JobsProvider, { useJobs } from "./JobsProvider";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  listReminders: vi.fn(),
  connect: vi.fn(),
  t: (key) => key,
}));

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), dismiss: vi.fn() }) }));
vi.mock("../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "admin" } }) }));
vi.mock("../../services/auth", () => ({ AuthStorage: { getAccessToken: () => "token" } }));
vi.mock("../../services/browserNotifications", () => ({
  dismissPrompt: vi.fn(),
  getPermission: () => "denied",
  isEnabled: () => false,
  isPageInBackground: () => false,
  isPromptDismissed: () => true,
  isSupported: () => false,
  requestPermission: vi.fn(),
  setEnabled: vi.fn(),
  showNotification: vi.fn(),
}));
vi.mock("../../services/webPush", () => ({
  isPushSupported: () => false,
  isPushSubscribed: vi.fn(),
  sendTestPush: vi.fn(),
  subscribePush: vi.fn(),
  unsubscribePush: vi.fn(),
}));
vi.mock("../../services/jobs", () => ({
  JobsService: { list: mocks.list },
  connectJobsWebSocket: (...args) => mocks.connect(...args),
}));
vi.mock("../../services/courses", () => ({
  CoursesService: { listReminders: mocks.listReminders },
  CourseAdminService: {},
}));
vi.mock("./JobDetailDialog", () => ({ default: () => null }));

const RUNNING_KEY = jobKeys.list({ statuses: ["running"], limit: 200, historyDays: 30 });

let host;
let root;
let client;
let ctx;
let ws;

function Probe() {
  ctx = useJobs();
  return null;
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

async function mount() {
  await act(async () => {
    root.render(withQueryClient(
      <MemoryRouter>
        <JobsProvider><Probe /></JobsProvider>
      </MemoryRouter>,
      client,
    ));
  });
  await flush();
}

const running = (id) => ({ id, kind: "deletion", status: "running", title: id });

function restObserver() {
  return client.getQueryCache().find({ queryKey: RUNNING_KEY }).observers[0];
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  ws = null;
  mocks.connect.mockImplementation((token, onSnapshot, options) => {
    ws = { onSnapshot, onStatusChange: options.onStatusChange };
    return () => {};
  });
  mocks.listReminders.mockResolvedValue([]);
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
});

describe("REST fallback for running jobs", () => {
  test("loads running jobs once on mount", async () => {
    mocks.list.mockResolvedValue({ items: [running("a")] });
    await mount();

    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(mocks.list).toHaveBeenCalledWith({ statuses: ["running"], limit: 200, historyDays: 30 });
    expect(ctx.items).toEqual([running("a")]);
  });

  test("polls every 15 seconds only while the WebSocket is down, and never on window focus", async () => {
    mocks.list.mockResolvedValue({ items: [] });
    await mount();
    expect(restObserver().options.refetchInterval).toBe(15_000);
    expect(restObserver().options.refetchOnWindowFocus).toBe(false);

    await act(async () => ws.onStatusChange(true));
    await flush();
    expect(restObserver().options.refetchInterval).toBe(false);
    expect(mocks.list).toHaveBeenCalledTimes(1);
  });

  test("a WebSocket disconnect triggers an immediate REST refresh and resumes polling", async () => {
    mocks.list.mockResolvedValue({ items: [] });
    await mount();
    await act(async () => ws.onStatusChange(true));
    await flush();

    await act(async () => ws.onStatusChange(false));
    await flush();
    expect(mocks.list).toHaveBeenCalledTimes(2);
    expect(restObserver().options.refetchInterval).toBe(15_000);
  });

  test("every successful REST response is written back, even when identical to the previous one", async () => {
    /* REST [a] → WS 推來 [a, b] → 斷線補抓 REST 又是 [a]：b 已結束，items 要回到 [a] */
    mocks.list.mockResolvedValue({ items: [running("a")] });
    await mount();
    await act(async () => ws.onSnapshot({ items: [running("a"), running("b")] }));
    expect(ctx.items).toEqual([running("a"), running("b")]);

    await act(async () => ws.onStatusChange(false));
    await flush();
    expect(mocks.list).toHaveBeenCalledTimes(2);
    expect(ctx.items).toEqual([running("a")]);
  });

  test("a failed REST refresh keeps the current items", async () => {
    mocks.list
      .mockResolvedValueOnce({ items: [running("a")] })
      .mockRejectedValueOnce(new Error("502"));
    await mount();

    await act(async () => { await client.refetchQueries({ queryKey: RUNNING_KEY }); });
    await flush();
    expect(ctx.items).toEqual([running("a")]);
  });
});

describe("reminders", () => {
  test("load once, refresh on demand and follow WebSocket snapshots", async () => {
    mocks.list.mockResolvedValue({ items: [] });
    mocks.listReminders
      .mockResolvedValueOnce([{ id: "r1", title: "t1" }])
      .mockResolvedValueOnce([{ id: "r1", title: "t1" }, { id: "r2", title: "t2" }]);
    await mount();
    expect(ctx.reminders).toEqual([{ id: "r1", title: "t1" }]);

    await act(async () => { await ctx.refreshReminders(); });
    await flush();
    expect(mocks.listReminders).toHaveBeenCalledTimes(2);
    expect(ctx.reminders.map((r) => r.id)).toEqual(["r1", "r2"]);

    await act(async () => ws.onSnapshot({ items: [], reminders: [{ id: "r3", title: "t3" }] }));
    expect(ctx.reminders.map((r) => r.id)).toEqual(["r3"]);
  });

  test("a failed first load falls back to an empty list instead of staying unloaded", async () => {
    mocks.list.mockResolvedValue({ items: [] });
    mocks.listReminders.mockRejectedValue(new Error("403"));
    await mount();
    expect(ctx.reminders).toEqual([]);
  });
});
