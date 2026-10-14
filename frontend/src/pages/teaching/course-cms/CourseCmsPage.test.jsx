// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const state = vi.hoisted(() => ({ token: "old", progress: vi.fn() }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));
vi.mock("../../../services/auth", () => ({ AuthStorage: { getAccessToken: () => state.token } }));
vi.mock("../../../services/courses", () => ({
  CourseAdminService: {
    listPaths: async () => [{ id: "a", title: "A" }, { id: "b", title: "B" }],
    getPathProgress: state.progress,
  },
  courseProgressWsUrl: (path, token) => `ws://test/${path}?token=${token}`,
}));
vi.mock("../../../services/teachingClasses", () => ({ TeachingClassesService: { list: async () => [] } }));
vi.mock("../../../contexts/UnsavedChangesContext", () => ({ useUnsavedChanges: () => ({ confirmLeave: async () => true }) }));
vi.mock("./ContentEditor", () => ({ default: () => null }));

import CourseCmsPage from "./CourseCmsPage";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let root;
let container;
let sockets;
beforeEach(async () => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0.5);
  state.token = "old";
  state.progress.mockReset().mockResolvedValue({ students: [] });
  sockets = [];
  vi.stubGlobal("WebSocket", class {
    constructor(url) { this.url = url; sockets.push(this); }
    close() { this.onclose?.({ code: 1000 }); }
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<MemoryRouter initialEntries={["/?tab=progress&pathId=a"]}><CourseCmsPage /></MemoryRouter>);
  });
});
afterEach(async () => {
  if (root) await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("progress reconnect refreshes the snapshot and obtains the current token", async () => {
  expect(sockets).toHaveLength(1);
  await act(async () => sockets[0].onopen());
  const before = state.progress.mock.calls.length;
  await act(async () => sockets[0].onclose({ code: 1006 }));
  state.token = "new";
  await act(async () => vi.advanceTimersByTime(3750));
  expect(sockets[1].url).toBe("ws://test/a?token=new");
  await act(async () => sockets[1].onopen());
  expect(state.progress).toHaveBeenCalledTimes(before + 1);
});

test("changing paths and unmounting discard old events and reconnect timers", async () => {
  const stale = sockets[0];
  await act(async () => {
    const select = container.querySelector("select");
    select.value = "b";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(sockets[1].url).toContain("/b?");
  const calls = state.progress.mock.calls.length;
  await act(async () => {
    stale.onmessage({ data: "old" });
    stale.onclose({ code: 1006 });
    vi.advanceTimersByTime(60_000);
  });
  expect(state.progress).toHaveBeenCalledTimes(calls);
  expect(sockets).toHaveLength(2);
  await act(async () => {
    sockets[1].onclose({ code: 1006 });
    root.unmount();
  });
  root = null;
  vi.runAllTimers();
  expect(sockets).toHaveLength(2);
});

test("a slow pre-reconnect snapshot cannot replace the latest snapshot", async () => {
  let finishOld;
  state.progress.mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; }));
  await act(async () => sockets[0].onopen());
  await act(async () => sockets[0].onclose({ code: 1006 }));
  await act(async () => vi.advanceTimersByTime(3750));
  const report = (name) => ({ students: [{ user_id: name, user_name: name, rooms: [], progress_percent: 0 }] });
  state.progress.mockResolvedValueOnce(report("Latest student"));
  await act(async () => sockets[1].onopen());
  expect(container.textContent).toContain("Latest student");
  await act(async () => finishOld(report("Stale student")));
  expect(container.textContent).toContain("Latest student");
  expect(container.textContent).not.toContain("Stale student");
});

test("continuous progress events still refresh within the batching window", async () => {
  const before = state.progress.mock.calls.length;
  await act(async () => {
    for (let index = 0; index < 4; index += 1) {
      sockets[0].onmessage({ data: "progress" });
      vi.advanceTimersByTime(200);
    }
  });
  expect(state.progress).toHaveBeenCalledTimes(before + 1);
  await act(async () => {
    sockets[0].onmessage({ data: "progress" });
    vi.advanceTimersByTime(800);
  });
  expect(state.progress).toHaveBeenCalledTimes(before + 2);
});

test("a completed snapshot is shown while a newer request is still pending", async () => {
  let finishFirst;
  let finishSecond;
  state.progress.mockImplementationOnce(() => new Promise((resolve) => { finishFirst = resolve; }));
  state.progress.mockImplementationOnce(() => new Promise((resolve) => { finishSecond = resolve; }));
  await act(async () => sockets[0].onopen());
  await act(async () => {
    sockets[0].onmessage({ data: "progress" });
    vi.advanceTimersByTime(800);
  });
  const report = (name) => ({ students: [{ user_id: name, user_name: name, rooms: [], progress_percent: 0 }] });
  await act(async () => finishFirst(report("First student")));
  expect(container.textContent).toContain("First student");
  await act(async () => finishSecond(report("Second student")));
  expect(container.textContent).toContain("Second student");
});
