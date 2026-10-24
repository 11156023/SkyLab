// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  creatingPollInterval,
  selectActiveRequests,
  selectCreatingRequests,
  useMyActiveVmRequestsQuery,
  usePendingVmRequestsQuery,
  useVmRequestCacheUpdater,
} from "./vmRequests";

const mocks = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("../services/vmRequests", () => ({ VmRequestsService: { list: mocks.list } }));

const creating = { id: "r1", status: "approved", vmid: null };
const done = { id: "r2", status: "approved", vmid: 101 };
const consumed = { id: "r3", status: "approved", vmid: null, review_comment: "Resource deleted by user" };

describe("selectors", () => {
  test("creating = pending or approved without vmid, excluding consumed", () => {
    expect(selectCreatingRequests([creating, done, consumed, { id: "r4", status: "pending" }]).map((r) => r.id))
      .toEqual(["r1", "r4"]);
  });

  test("active = everything except consumed", () => {
    expect(selectActiveRequests([creating, done, consumed]).map((r) => r.id)).toEqual(["r1", "r2"]);
  });

  test("poll fast only while something is still creating", () => {
    expect(creatingPollInterval([done])).toBe(30_000);
    expect(creatingPollInterval([done, creating])).toBe(5_000);
    expect(creatingPollInterval(undefined)).toBe(30_000);
  });
});

let host;
let root;
let seen;

/* useQuery 的結果是存取追蹤的：欄位要在 render 期間讀過，資料變了才會重繪（頁面本來就是這樣用） */
function Probe() {
  const pending = usePendingVmRequestsQuery();
  const active = useMyActiveVmRequestsQuery();
  const updater = useVmRequestCacheUpdater();
  seen = { pending: pending.data, active: active.data, activeError: active.isError, updater };
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

describe("shared cache", () => {
  test("placeholder list and request list share one request", async () => {
    mocks.list.mockResolvedValue({ data: [creating, done, consumed] });
    await act(async () => { root.render(withQueryClient(<Probe />)); });
    await flush();

    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(seen.pending.map((r) => r.id)).toEqual(["r1"]);
    expect(seen.active.map((r) => r.id)).toEqual(["r1", "r2"]);
  });

  test("update() patches the row in place for every consumer", async () => {
    mocks.list.mockResolvedValue({ data: [creating, done] });
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();

    /* update() 會先取消在途的背景刷新再寫入（非同步），要等它完成 */
    await act(async () => { await seen.updater.update({ ...creating, vmid: 202 }); });
    await flush();

    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(seen.pending).toEqual([]);
    expect(seen.active.find((r) => r.id === "r1").vmid).toBe(202);
  });

  test("a failed load exposes isError without data", async () => {
    mocks.list.mockRejectedValue(new Error("500"));
    await act(async () => { root.render(withQueryClient(<Probe />)); });
    await flush();

    expect(seen.activeError).toBe(true);
    expect(seen.active).toBeUndefined();
  });
});
