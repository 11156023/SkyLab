// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  aiApiKeys,
  nextUsageRecordsSkip,
  useAiApiCacheUpdater,
  useAiCredentialQuery,
  useAllAiCredentialsQuery,
  useMyAiCredentialsQuery,
  useMyAiRequestsQuery,
  useMyAiUsageRecordsInfiniteQuery,
} from "./aiApi";

const mocks = vi.hoisted(() => ({
  listMyRequests: vi.fn(),
  listMyCredentials: vi.fn(),
  getCredential: vi.fn(),
  listAllCredentials: vi.fn(),
  getMyUsageRecords: vi.fn(),
}));
vi.mock("../services/aiApi", () => ({ AiApiService: mocks }));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

let host;
let root;
let client;
let seen;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

function render(ui) {
  return act(async () => { root.render(withQueryClient(ui, client)); });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

/* useQuery 的結果是存取追蹤的：欄位要在 render 期間讀過，資料變了才會重繪（頁面本來就是這樣用） */
function MineProbe() {
  const requests = useMyAiRequestsQuery({ refetchInterval: false });
  const credentials = useMyAiCredentialsQuery({ refetchInterval: false });
  const cache = useAiApiCacheUpdater();
  seen = {
    requests: requests.data,
    credentials: credentials.data,
    credentialsError: credentials.isError,
    credentialsFetching: credentials.isFetching,
    cache,
  };
  return null;
}

function CredentialProbe({ id }) {
  const query = useAiCredentialQuery(id);
  const cache = useAiApiCacheUpdater();
  seen = { data: query.data, isError: query.isError, isPending: query.isPending, cache };
  return null;
}

describe("useAiCredentialQuery", () => {
  test("passes an abort signal and stays idle without an id", async () => {
    mocks.getCredential.mockResolvedValue({ id: "c1", api_key: "ccai_secret" });
    await render(<CredentialProbe id={null} />);
    await flush();
    expect(mocks.getCredential).not.toHaveBeenCalled();

    await render(<CredentialProbe id="c1" />);
    await flush();
    expect(mocks.getCredential).toHaveBeenCalledWith("c1", { signal: expect.any(AbortSignal) });
    expect(seen.data.api_key).toBe("ccai_secret");
  });

  test("cancelCredential aborts the request and a late key never reaches the cache", async () => {
    const pending = deferred();
    mocks.getCredential.mockReturnValueOnce(pending.promise);
    await render(<CredentialProbe id="c1" />);
    const { signal } = mocks.getCredential.mock.calls[0][1];

    await act(async () => { await seen.cache.cancelCredential("c1"); });
    expect(signal.aborted).toBe(true);
    await act(async () => { pending.resolve({ id: "c1", api_key: "ccai_late" }); });
    await flush();

    expect(seen.data).toBeUndefined();
    expect(client.getQueryData(aiApiKeys.credential("c1"))).toBeUndefined();
  });

  test("the full key is dropped from the cache once nobody is looking at it", async () => {
    mocks.getCredential.mockResolvedValue({ id: "c1", api_key: "ccai_secret" });
    await render(<CredentialProbe id="c1" />);
    await flush();
    expect(client.getQueryData(aiApiKeys.credential("c1"))).toBeDefined();

    await render(<span />);
    await flush();
    expect(client.getQueryCache().find({ queryKey: aiApiKeys.credential("c1") })).toBeUndefined();
  });
});

describe("useAiApiCacheUpdater", () => {
  const cred = (id) => ({ id, api_key_name: id });

  test("removeMyCredential drops the row and an in-flight refresh cannot bring it back", async () => {
    const refresh = deferred();
    mocks.listMyRequests.mockResolvedValue({ data: [] });
    mocks.listMyCredentials
      .mockResolvedValueOnce({ data: [cred("a"), cred("b")], public_base_url: "https://x" })
      .mockReturnValueOnce(refresh.promise);
    await render(<MineProbe />);
    await flush();

    await act(async () => { client.refetchQueries({ queryKey: aiApiKeys.myCredentials }); });
    await act(async () => { await seen.cache.removeMyCredential("b"); });
    await flush();
    expect(seen.credentials).toEqual({ data: [cred("a")], public_base_url: "https://x" });

    await act(async () => { refresh.resolve({ data: [cred("a"), cred("b")] }); });
    await flush();
    expect(seen.credentials.data).toEqual([cred("a")]);
  });

  test("removeMyCredential before the first load is a no-op and does not cancel it", async () => {
    const first = deferred();
    mocks.listMyRequests.mockResolvedValue({ data: [] });
    mocks.listMyCredentials.mockReturnValueOnce(first.promise);
    await render(<MineProbe />);
    await act(async () => { await seen.cache.removeMyCredential("a"); });
    await act(async () => { first.resolve({ data: [cred("a")] }); });
    await flush();

    expect(seen.credentials.data).toEqual([cred("a")]);
  });

  test("invalidateMine({ throwOnError }) surfaces a failed refetch but keeps the cached rows", async () => {
    mocks.listMyRequests.mockResolvedValue({ data: [] });
    mocks.listMyCredentials
      .mockResolvedValueOnce({ data: [cred("a")] })
      .mockRejectedValueOnce(new Error("refresh failed"))
      .mockRejectedValueOnce(new Error("quiet failure"));
    await render(<MineProbe />);
    await flush();

    let caught;
    await act(async () => { await seen.cache.invalidateMine({ throwOnError: true }).catch((e) => { caught = e; }); });
    expect(caught?.message).toBe("refresh failed");
    await flush();
    expect(seen.credentials.data).toEqual([cred("a")]);

    // 沒帶 throwOnError：失敗只留在查詢狀態，不丟出
    await act(async () => { await seen.cache.invalidateMine(); });
    await flush();
    expect(seen.credentialsError).toBe(true);
    expect(seen.credentials.data).toEqual([cred("a")]);
  });

  test("a refetch superseded by a newer one is not reported as a failure", async () => {
    const slow = deferred();
    mocks.listMyRequests.mockResolvedValue({ data: [] });
    mocks.listMyCredentials
      .mockResolvedValueOnce({ data: [cred("a")] })
      .mockReturnValueOnce(slow.promise)
      .mockResolvedValueOnce({ data: [cred("b")] });
    await render(<MineProbe />);
    await flush();

    let first;
    await act(async () => { first = seen.cache.invalidateMine({ throwOnError: true }); });
    await act(async () => { await seen.cache.invalidateMine({ throwOnError: true }); });
    await expect(first).resolves.toBeDefined();
    await flush();
    expect(seen.credentials.data).toEqual([cred("b")]);
  });
});

describe("useAllAiCredentialsQuery", () => {
  function AllProbe({ params }) {
    const query = useAllAiCredentialsQuery(params, { refetchInterval: false });
    seen = { data: query.data, isPlaceholderData: query.isPlaceholderData };
    return null;
  }

  test("changing filters keeps the previous page on screen until the new one arrives", async () => {
    const next = deferred();
    mocks.listAllCredentials
      .mockResolvedValueOnce({ data: [{ id: "active-1" }], count: 1 })
      .mockReturnValueOnce(next.promise);
    await render(<AllProbe params={{ status: "active", skip: 0, limit: 50 }} />);
    await flush();

    await render(<AllProbe params={{ status: "inactive", skip: 0, limit: 50 }} />);
    await flush();
    expect(mocks.listAllCredentials).toHaveBeenLastCalledWith({ status: "inactive", skip: 0, limit: 50 });
    expect(seen.isPlaceholderData).toBe(true);
    expect(seen.data.data).toEqual([{ id: "active-1" }]);

    await act(async () => { next.resolve({ data: [{ id: "inactive-1" }], count: 1 }); });
    await flush();
    expect(seen.isPlaceholderData).toBe(false);
    expect(seen.data.data).toEqual([{ id: "inactive-1" }]);
  });
});

describe("useMyAiUsageRecordsInfiniteQuery", () => {
  test("nextUsageRecordsSkip continues until every record is loaded", () => {
    expect(nextUsageRecordsSkip({ data: [1, 2], count: 5 }, [{ data: [1, 2] }])).toBe(2);
    expect(nextUsageRecordsSkip({ data: [3], count: 3 }, [{ data: [1, 2] }, { data: [3] }])).toBeUndefined();
    expect(nextUsageRecordsSkip({ data: [], count: 9 }, [{ data: [1] }, { data: [] }])).toBeUndefined();
  });

  function RecordsProbe({ params }) {
    const query = useMyAiUsageRecordsInfiniteQuery(params);
    seen = {
      pages: query.data?.pages,
      hasNextPage: query.hasNextPage,
      fetchNextPage: query.fetchNextPage,
    };
    return null;
  }

  test("load more asks for the next page by skip and appends it", async () => {
    const params = { start_date: "s", end_date: "e", limit: 2 };
    mocks.getMyUsageRecords
      .mockResolvedValueOnce({ data: [{ id: 1 }, { id: 2 }], count: 3 })
      .mockResolvedValueOnce({ data: [{ id: 3 }], count: 3 });
    await render(<RecordsProbe params={params} />);
    await flush();
    expect(mocks.getMyUsageRecords).toHaveBeenLastCalledWith({ ...params, skip: 0 });
    expect(seen.hasNextPage).toBe(true);

    await act(async () => { await seen.fetchNextPage(); });
    await flush();
    expect(mocks.getMyUsageRecords).toHaveBeenLastCalledWith({ ...params, skip: 2 });
    expect(seen.pages.flatMap((page) => page.data).map((item) => item.id)).toEqual([1, 2, 3]);
    expect(seen.hasNextPage).toBe(false);
  });
});
