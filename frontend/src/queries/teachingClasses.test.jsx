// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createQueryClient } from "./queryClient";
import { createTestQueryClient, withQueryClient } from "./testing";
import {
  teachingClassKeys,
  useClassProvisionStatusQuery,
  useTeachingClassCacheUpdater,
  useTeachingClassesQuery,
  useTeachingClassQuery,
} from "./teachingClasses";

const mocks = vi.hoisted(() => ({ list: vi.fn(), get: vi.fn(), provisionStatus: vi.fn(), reconcile: vi.fn() }));
vi.mock("../services/teachingClasses", () => ({
  TeachingClassesService: {
    list: mocks.list, get: mocks.get, provisionStatus: mocks.provisionStatus, reconcile: mocks.reconcile,
  },
}));

let host;
let root;
let seen;

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

/* 全站預設（staleTime 5 秒）但不靠視窗焦點重抓，才看得出「快取還新鮮時會不會重抓」 */
function siteDefaultClient() {
  return createQueryClient({ queries: { refetchOnWindowFocus: false } });
}

describe("useTeachingClassQuery", () => {
  function DetailProbe({ classId, options }) {
    const query = useTeachingClassQuery(classId, options);
    seen = { data: query.data, isFetchedAfterMount: query.isFetchedAfterMount, fetchStatus: query.fetchStatus };
    return null;
  }

  test("a fresh cached class is reused as-is by default", async () => {
    const client = siteDefaultClient();
    client.setQueryData(teachingClassKeys.detail("c1"), { id: "c1", name: "cached" });
    await act(async () => { root.render(withQueryClient(<DetailProbe classId="c1" />, client)); });
    await flush();

    expect(mocks.get).not.toHaveBeenCalled();
    expect(seen.data).toEqual({ id: "c1", name: "cached" });
  });

  test("forEditing always asks the server on mount, even when the cache is fresh", async () => {
    const client = siteDefaultClient();
    client.setQueryData(teachingClassKeys.detail("c1"), { id: "c1", name: "cached" });
    mocks.get.mockResolvedValue({ id: "c1", name: "server" });
    await act(async () => { root.render(withQueryClient(<DetailProbe classId="c1" options={{ forEditing: true }} />, client)); });
    await flush();

    expect(mocks.get).toHaveBeenCalledExactlyOnceWith("c1");
    expect(seen.isFetchedAfterMount).toBe(true);
    expect(seen.data).toEqual({ id: "c1", name: "server" });
  });

  test("a null id does not fetch", async () => {
    await act(async () => { root.render(withQueryClient(<DetailProbe classId={null} />)); });
    await flush();
    expect(mocks.get).not.toHaveBeenCalled();
    expect(seen.fetchStatus).toBe("idle");
  });
});

describe("useClassProvisionStatusQuery", () => {
  function ProgressProbe() {
    const query = useClassProvisionStatusQuery("c1", { reconcileEvery: 5 });
    seen = { data: query.data, refetch: query.refetch };
    return null;
  }

  test("every fifth fetch (1st, 6th…) asks the backend to reconcile instead of reading progress", async () => {
    mocks.reconcile.mockResolvedValue({ id: "c1", status: "provisioning", via: "reconcile" });
    mocks.provisionStatus.mockResolvedValue({ id: "c1", status: "provisioning", via: "status" });
    await act(async () => { root.render(withQueryClient(<ProgressProbe />)); });
    await flush();
    expect(seen.data.via).toBe("reconcile");

    for (let i = 0; i < 5; i += 1) {
      await act(async () => { await seen.refetch(); });
    }
    await flush();

    expect(mocks.reconcile).toHaveBeenCalledTimes(2);
    expect(mocks.provisionStatus).toHaveBeenCalledTimes(4);
    expect(seen.data.via).toBe("reconcile");
  });
});

describe("useTeachingClassCacheUpdater", () => {
  function CacheProbe() {
    const list = useTeachingClassesQuery({ refetchInterval: false });
    const detail = useTeachingClassQuery("7");
    const updater = useTeachingClassCacheUpdater();
    seen = { list: list.data, detail: detail.data, updater };
    return null;
  }

  async function mountCache() {
    mocks.list.mockResolvedValue([{ id: 7, name: "A", member_count: 1 }]);
    mocks.get.mockResolvedValue({ id: 7, name: "A", students: [{ id: 1 }] });
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<CacheProbe />, client)); });
    await flush();
    return client;
  }

  test("update() writes the detail and refetches the summary list instead of merging stale counts into it", async () => {
    await mountCache();
    const firstUpdater = seen.updater;
    mocks.list.mockResolvedValue([{ id: 7, name: "A", member_count: 2 }]);

    await act(async () => { seen.updater.update({ id: 7, name: "A", students: [{ id: 1 }, { id: 2 }] }); });
    await flush();

    expect(seen.detail.students).toHaveLength(2);
    expect(mocks.list).toHaveBeenCalledTimes(2);
    expect(seen.list).toEqual([{ id: 7, name: "A", member_count: 2 }]);
    expect(mocks.get).toHaveBeenCalledTimes(1);
    /* 只包著 queryClient：重畫後還是同一個物件，放進 effect 依賴不會一直重跑 */
    expect(seen.updater).toBe(firstUpdater);
  });

  test("setDetail() only replaces the detail (progress polling must not churn the list)", async () => {
    await mountCache();
    await act(async () => { seen.updater.setDetail({ id: 7, name: "A", status: "provisioning" }); });
    await flush();

    expect(seen.detail.status).toBe("provisioning");
    expect(mocks.list).toHaveBeenCalledTimes(1);
  });

  test("invalidate(classId) refetches that class and the list", async () => {
    await mountCache();
    await act(async () => { await seen.updater.invalidate("7"); });
    await flush();

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.list).toHaveBeenCalledTimes(2);
  });
});
