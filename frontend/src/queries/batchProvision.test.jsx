// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  batchProvisionKeys,
  useAllBatchProvisionQuery,
  useBatchProvisionCacheUpdater,
  useRecurrencePreviewQuery,
} from "./batchProvision";

const mocks = vi.hoisted(() => ({ listAll: vi.fn(), getRecurrencePreview: vi.fn() }));
vi.mock("../services/batchProvision", () => ({
  BatchProvisionService: { listAll: mocks.listAll, getRecurrencePreview: mocks.getRecurrencePreview },
}));

let host;
let root;
let seen;

/* useQuery 的結果是存取追蹤的：欄位要在 render 期間讀過，資料變了才會重繪 */
function ListProbe({ params }) {
  const query = useAllBatchProvisionQuery(params);
  const updater = useBatchProvisionCacheUpdater();
  seen = { data: query.data, isError: query.isError, updater };
  return null;
}

function PreviewProbe({ jobId }) {
  const query = useRecurrencePreviewQuery(jobId, 5, { enabled: false });
  const updater = useBatchProvisionCacheUpdater();
  seen = { data: query.data, isFetching: query.isFetching, updater };
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

describe("batchProvisionKeys", () => {
  test("list keys differ by params; preview keys stringify the job id", () => {
    expect(batchProvisionKeys.list({ limit: 200 })).not.toEqual(batchProvisionKeys.list({ status: "pending_review", limit: 200 }));
    expect(batchProvisionKeys.list()).toEqual(["batchProvision", "list", {}]);
    expect(batchProvisionKeys.recurrencePreview(7, 5)).toEqual(["batchProvision", "recurrencePreview", "7", 5]);
  });
});

describe("useAllBatchProvisionQuery", () => {
  test("passes params through and invalidate() refetches the list", async () => {
    mocks.listAll.mockResolvedValueOnce([{ id: "a" }]).mockResolvedValueOnce([{ id: "a" }, { id: "b" }]);
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<ListProbe params={{ status: "pending_review", limit: 200 }} />, client)); });
    await flush();

    expect(mocks.listAll).toHaveBeenCalledWith({ status: "pending_review", limit: 200 });
    expect(seen.data).toEqual([{ id: "a" }]);

    await act(async () => { await seen.updater.invalidate(); });
    await flush();
    expect(mocks.listAll).toHaveBeenCalledTimes(2);
    expect(seen.data).toEqual([{ id: "a" }, { id: "b" }]);
  });
});

describe("useRecurrencePreviewQuery + loadRecurrencePreview", () => {
  test("with enabled: false nothing is fetched until loadRecurrencePreview, which fills the cache", async () => {
    let resolvePreview;
    mocks.getRecurrencePreview.mockImplementation(() => new Promise((resolve) => { resolvePreview = resolve; }));
    await act(async () => { root.render(withQueryClient(<PreviewProbe jobId="job-1" />)); });
    await flush();
    expect(mocks.getRecurrencePreview).not.toHaveBeenCalled();
    expect(seen.data).toBeUndefined();

    let loading;
    await act(async () => { loading = seen.updater.loadRecurrencePreview("job-1", 5); });
    await flush();
    /* 主動載入時同一份快取的觀察者看得到載入中 */
    expect(seen.isFetching).toBe(true);
    expect(mocks.getRecurrencePreview).toHaveBeenCalledWith("job-1", 5);

    await act(async () => { resolvePreview({ windows: [["s", "e"]] }); await loading; });
    await flush();
    expect(seen.isFetching).toBe(false);
    expect(seen.data).toEqual({ windows: [["s", "e"]] });
  });

  test("a failed load rejects so the caller can show a toast", async () => {
    mocks.getRecurrencePreview.mockRejectedValue(new Error("boom"));
    await act(async () => { root.render(withQueryClient(<PreviewProbe jobId="job-1" />)); });
    await flush();

    await expect(seen.updater.loadRecurrencePreview("job-1", 5)).rejects.toThrow("boom");
    await flush();
    expect(seen.data).toBeUndefined();
    expect(seen.isFetching).toBe(false);
  });
});
