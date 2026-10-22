// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createQueryClient } from "./queryClient";
import { createTestQueryClient, withQueryClient } from "./testing";
import {
  courseEnvironmentKeys,
  useCourseEnvironmentCacheUpdater,
  useCourseEnvironmentQuery,
  useCourseEnvironmentsQuery,
  usePublishedCourseEnvironmentsQuery,
} from "./courseEnvironments";

const mocks = vi.hoisted(() => ({ list: vi.fn(), listPublished: vi.fn(), get: vi.fn() }));
vi.mock("../services/courseEnvironments", () => ({
  CourseEnvironmentsService: { list: mocks.list, listPublished: mocks.listPublished, get: mocks.get },
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

describe("useCourseEnvironmentQuery forEditing", () => {
  function EditorProbe() {
    const query = useCourseEnvironmentQuery("env-1", { forEditing: true });
    seen = { data: query.data, isFetchedAfterMount: query.isFetchedAfterMount };
    return null;
  }

  test("asks the server on mount even with a fresh cache, then ignores window focus", async () => {
    /* 全站預設：staleTime 5 秒、切回分頁會重抓 */
    const client = createQueryClient();
    client.setQueryData(courseEnvironmentKeys.detail("env-1"), { id: "env-1", name: "cached" });
    mocks.get.mockResolvedValue({ id: "env-1", name: "server" });
    await act(async () => { root.render(withQueryClient(<EditorProbe />, client)); });
    await flush();
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(seen.data.name).toBe("server");

    await act(async () => { window.dispatchEvent(new Event("visibilitychange")); window.dispatchEvent(new Event("focus")); });
    await flush();
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });
});

describe("useCourseEnvironmentCacheUpdater", () => {
  function ListProbe() {
    const list = useCourseEnvironmentsQuery({ refetchInterval: false });
    const published = usePublishedCourseEnvironmentsQuery();
    const updater = useCourseEnvironmentCacheUpdater();
    seen = { list: list.data, published: published.data, updater };
    return null;
  }

  async function mountLists() {
    mocks.list.mockResolvedValue([{ id: "a", name: "A", status: "draft" }, { id: "b", name: "B", status: "published" }]);
    mocks.listPublished.mockResolvedValue([{ id: "b", name: "B", status: "published" }]);
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<ListProbe />, client)); });
    await flush();
    return client;
  }

  test("update() replaces the row in place; saving a draft leaves the published list alone", async () => {
    const client = await mountLists();
    /* 測試用 client 的 gcTime 是 0：沒有人訂閱的詳情會在下一個 timer tick 被回收。
       在同一個同步 act 裡寫入並讀出，不讓 act(async) 結束前跑掉回收的 timer（否則偶發失敗） */
    let detail;
    act(() => {
      seen.updater.update({ id: "a", name: "A2", status: "draft" });
      detail = client.getQueryData(courseEnvironmentKeys.detail("a"));
    });
    expect(detail).toEqual({ id: "a", name: "A2", status: "draft" });
    await flush();

    expect(seen.list[0]).toEqual({ id: "a", name: "A2", status: "draft" });
    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(mocks.listPublished).toHaveBeenCalledTimes(1);
  });

  test("update() of an environment the list does not have yet refetches the list; publishing refreshes the published list", async () => {
    await mountLists();
    mocks.list.mockResolvedValue([{ id: "a" }, { id: "b" }, { id: "c", status: "published" }]);
    mocks.listPublished.mockResolvedValue([{ id: "b" }, { id: "c", status: "published" }]);
    await act(async () => { seen.updater.update({ id: "c", name: "C", status: "published" }); });
    await flush();

    expect(mocks.list).toHaveBeenCalledTimes(2);
    expect(mocks.listPublished).toHaveBeenCalledTimes(2);
    expect(seen.published.map((row) => row.id)).toEqual(["b", "c"]);
  });

  test("update() ignores a response without an id", async () => {
    const client = await mountLists();
    await act(async () => { seen.updater.update({ files: [] }); });
    expect(client.getQueryData(courseEnvironmentKeys.detail(undefined))).toBeUndefined();
  });

  test("remove() drops the row from both lists without refetching", async () => {
    const client = await mountLists();
    client.setQueryData(courseEnvironmentKeys.detail("b"), { id: "b" });
    expect(client.getQueryData(courseEnvironmentKeys.detail("b"))).toEqual({ id: "b" });
    await act(async () => { await seen.updater.remove("b"); });
    expect(client.getQueryData(courseEnvironmentKeys.detail("b"))).toBeUndefined();
    await flush();

    expect(seen.list.map((row) => row.id)).toEqual(["a"]);
    expect(seen.published).toEqual([]);
    expect(mocks.list).toHaveBeenCalledTimes(1);
  });
});
