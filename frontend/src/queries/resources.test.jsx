// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import { bootingPollInterval, useMyResourcesQuery, useResourceCacheUpdater } from "./resources";

const mocks = vi.hoisted(() => ({ list: vi.fn(), listMySessions: vi.fn() }));
vi.mock("../services/resources", () => ({ ResourcesService: { list: mocks.list } }));
vi.mock("../services/quickPractice", () => ({ QuickPracticeService: { listMySessions: mocks.listMySessions } }));

describe("bootingPollInterval", () => {
  test("polls fast while any machine is starting", () => {
    expect(bootingPollInterval({ resources: [{ status: "running" }] })).toBe(30_000);
    expect(bootingPollInterval({ resources: [{ status: "starting" }] })).toBe(5_000);
    expect(bootingPollInterval(undefined)).toBe(30_000);
  });
});

let host;
let root;
let seen;

/* useQuery 的結果是存取追蹤的：欄位要在 render 期間讀過，資料變了才會重繪（頁面本來就是這樣用） */
function Probe() {
  const query = useMyResourcesQuery();
  const updater = useResourceCacheUpdater();
  seen = { data: query.data, isError: query.isError, fetchStatus: query.fetchStatus, refetch: query.refetch, updater };
  return null;
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("useMyResourcesQuery", () => {
  test("bundles machines and quick-practice sessions; a session failure yields an empty list", async () => {
    mocks.list.mockResolvedValue([{ vmid: 101, status: "running" }]);
    mocks.listMySessions.mockRejectedValue(new Error("503"));
    await act(async () => { root.render(withQueryClient(<Probe />)); });
    await flush();

    expect(seen.data).toEqual({ resources: [{ vmid: 101, status: "running" }], sessions: [] });
    expect(seen.isError).toBe(false);
  });

  test("update()/remove() patch the cached list without refetching", async () => {
    mocks.list.mockResolvedValue([{ vmid: 101, status: "stopped" }, { vmid: 102, status: "stopped" }]);
    mocks.listMySessions.mockResolvedValue([]);
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();

    await act(async () => { await seen.updater.update({ vmid: 101, status: "running" }); });
    await act(async () => { await seen.updater.remove(102); });
    await flush();

    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(seen.data.resources).toEqual([{ vmid: 101, status: "running" }]);
  });

  test("an in-flight background refresh is cancelled so it cannot overwrite a local update", async () => {
    let resolveSecond;
    mocks.list
      .mockResolvedValueOnce([{ vmid: 101, status: "stopped" }])
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
    mocks.listMySessions.mockResolvedValue([]);
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();

    let refetching;
    await act(async () => { refetching = seen.refetch(); });
    expect(mocks.list).toHaveBeenCalledTimes(2);
    await act(async () => { await seen.updater.update({ vmid: 101, status: "running" }); });
    /* 取消後 refetch 的 promise 立刻結束，不等那個還沒回來的 API */
    await act(async () => { await refetching; });
    await flush();
    expect(seen.fetchStatus).toBe("idle");
    expect(seen.data.resources).toEqual([{ vmid: 101, status: "running" }]);

    await act(async () => { resolveSecond([{ vmid: 101, status: "stopped" }]); });
    await flush();
    expect(seen.data.resources).toEqual([{ vmid: 101, status: "running" }]);
  });

  test("update() before the first load completes is a no-op and does not cancel the load", async () => {
    let resolveList;
    mocks.list.mockImplementationOnce(() => new Promise((resolve) => { resolveList = resolve; }));
    mocks.listMySessions.mockResolvedValue([]);
    await act(async () => { root.render(withQueryClient(<Probe />)); });
    await act(async () => { await seen.updater.update({ vmid: 101, status: "running" }); });
    await act(async () => { resolveList([{ vmid: 101, status: "stopped" }]); });
    await flush();

    expect(seen.data.resources).toEqual([{ vmid: 101, status: "stopped" }]);
  });
});
