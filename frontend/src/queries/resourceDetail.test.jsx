// @vitest-environment happy-dom
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createQueryClient } from "./queryClient";
import { createTestQueryClient, withQueryClient } from "./testing";
import { resourceKeys } from "./resources";
import {
  resourceDetailKeys,
  useBootOptionsQuery,
  useCredentialsQuery,
  useCurrentStatsQuery,
  useIsoImagesQuery,
  useLoadError,
  useQueryErrorEffect,
  useRefreshResourceDetail,
  useResourceDetailCacheUpdater,
  useResourceQuery,
  useResourceStatsQuery,
  useSharesQuery,
  useSnapshotCapabilityQuery,
  useSpecsQuery,
  useSshKeyQuery,
} from "./resourceDetail";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  getCredentials: vi.fn(),
  getSshKey: vi.fn(),
  getCurrentStats: vi.fn(),
  getStats: vi.fn(),
  listIsoImages: vi.fn(),
  listShares: vi.fn(),
  getSnapshotCapability: vi.fn(),
  getSpecs: vi.fn(),
}));
vi.mock("../services/resources", () => ({ ResourcesService: mocks }));

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

async function render(ui) {
  await act(async () => { root.render(withQueryClient(ui, client)); });
  await flush();
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

describe("resourceDetailKeys", () => {
  test("every read of one machine shares the vmid scope prefix", () => {
    const scope = resourceDetailKeys.scope(101);
    for (const key of [
      resourceDetailKeys.detail(101),
      resourceDetailKeys.currentStats("101"),
      resourceDetailKeys.stats(101, "day"),
      resourceDetailKeys.specs(101),
    ]) {
      expect(key.slice(0, scope.length)).toEqual(scope);
    }
    expect(resourceDetailKeys.stats(101, "day")).toEqual(["resourceDetail", "101", "stats", "day"]);
    expect(Object.isFrozen(resourceDetailKeys)).toBe(true);
  });
});

describe("reads", () => {
  /* useQuery 的結果是存取追蹤的：欄位要在 render 期間讀過，資料變了才會重繪 */
  function DetailProbe({ vmid }) {
    const q = useResourceQuery(vmid);
    seen = { data: q.data, isError: q.isError };
    return null;
  }

  test("useResourceQuery loads one machine by vmid", async () => {
    mocks.get.mockResolvedValue({ vmid: 101, status: "running" });
    await render(<DetailProbe vmid={101} />);

    expect(mocks.get).toHaveBeenCalledWith(101);
    expect(seen.data).toEqual({ vmid: 101, status: "running" });
  });

  test("useResourceQuery does not fetch without a vmid", async () => {
    await render(<DetailProbe vmid={null} />);
    expect(mocks.get).not.toHaveBeenCalled();
  });

  test("current stats are a new object on every fetch, even when the values are equal", async () => {
    /* 每次回應都是新解析的 JSON（值相同）；預設的 structural sharing 會沿用舊物件，這裡不能 */
    mocks.getCurrentStats.mockImplementation(() => Promise.resolve({ cpu: 0.1 }));
    const samples = [];
    function Probe() {
      const q = useCurrentStatsQuery(101, { refetchInterval: false });
      seen = { data: q.data };
      return null;
    }
    await render(<Probe />);
    samples.push(seen.data);
    await act(async () => { await client.refetchQueries({ queryKey: resourceDetailKeys.currentStats(101) }); });
    await flush();
    samples.push(seen.data);

    expect(mocks.getCurrentStats).toHaveBeenCalledTimes(2);
    expect(samples[1]).toEqual(samples[0]);
    expect(samples[1]).not.toBe(samples[0]);
  });

  test("RRD stats select the data array and keep the previous range while the next one loads", async () => {
    let resolveDay;
    mocks.getStats.mockImplementation((_vmid, timeframe) => (
      timeframe === "hour"
        ? Promise.resolve({ data: [{ time: 1 }] })
        : new Promise((resolve) => { resolveDay = resolve; })
    ));
    let setTimeframe;
    function Probe() {
      const [timeframe, setter] = useState("hour");
      setTimeframe = setter;
      const q = useResourceStatsQuery(101, timeframe, { refetchInterval: false });
      seen = { data: q.data, isPlaceholderData: q.isPlaceholderData };
      return null;
    }
    await render(<Probe />);
    expect(seen.data).toEqual([{ time: 1 }]);

    await act(async () => setTimeframe("day"));
    await flush();
    expect(mocks.getStats).toHaveBeenLastCalledWith(101, "day");
    expect(seen).toEqual({ data: [{ time: 1 }], isPlaceholderData: true });

    await act(async () => resolveDay({ data: [{ time: 2 }, { time: 3 }] }));
    await flush();
    expect(seen).toEqual({ data: [{ time: 2 }, { time: 3 }], isPlaceholderData: false });
  });

  test("ISO images that cannot be listed count as none; shares default to an empty list", async () => {
    mocks.listIsoImages.mockRejectedValue(new Error("storage offline"));
    mocks.listShares.mockResolvedValue(null);
    function Probe() {
      const iso = useIsoImagesQuery(101);
      const shares = useSharesQuery(101);
      seen = { iso: iso.data, isoError: iso.isError, shares: shares.data };
      return null;
    }
    await render(<Probe />);

    expect(seen).toEqual({ iso: [], isoError: false, shares: [] });
  });

  test("specs are a form read: refocusing the window does not refetch", async () => {
    mocks.getSpecs.mockResolvedValue({ cpu_cores: 2 });
    mocks.get.mockResolvedValue({ vmid: 101 });
    function Probe() {
      const specs = useSpecsQuery(101);
      const detail = useResourceQuery(101, { refetchInterval: false });
      seen = { data: specs.data, detail: detail.data };
      return null;
    }
    /* 正式環境的 client 切回視窗會重抓過期的查詢；詳情照常重抓（對照組），specs 要例外 */
    client.setDefaultOptions({ queries: { ...client.getDefaultOptions().queries, refetchOnWindowFocus: true } });
    await render(<Probe />);
    await act(async () => { window.dispatchEvent(new Event("visibilitychange")); });
    await flush();

    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.getSpecs).toHaveBeenCalledTimes(1);
    expect(seen.data).toEqual({ cpu_cores: 2 });
  });
});

describe("useResourceDetailCacheUpdater", () => {
  function Probe({ vmid = 101 }) {
    const detail = useResourceQuery(vmid, { refetchInterval: false });
    const capability = useSnapshotCapabilityQuery(vmid);
    const cache = useResourceDetailCacheUpdater();
    const refresh = useRefreshResourceDetail();
    seen = { detail: detail.data, detailError: detail.isError, capability: capability.data, cache, refresh };
    return null;
  }

  test("update() writes a machine into its detail cache without refetching", async () => {
    mocks.get.mockResolvedValue({ vmid: 101, status: "stopped" });
    mocks.getSnapshotCapability.mockResolvedValue({ available: true });
    await render(<Probe />);

    await act(async () => seen.cache.update({ vmid: 101, status: "running" }));
    await flush();

    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(seen.detail).toEqual({ vmid: 101, status: "running" });
  });

  test("setCredentials()/setBootOptions() patch those caches in place", async () => {
    client.setQueryData(resourceDetailKeys.credentials(101), { authorized_keys: ["a"], username: "u" });
    function Writer() {
      /*
       * 同正式畫面：寫入的卡片本身在看這兩份快取。沒有 observer 時測試 client 的 gcTime 0
       * 會在 await act 收尾的 setImmediate 之前清掉快取（CI 上依事件迴圈時序偶發失敗）
       */
      useCredentialsQuery(101, { enabled: false });
      useBootOptionsQuery(101, { enabled: false });
      const cache = useResourceDetailCacheUpdater();
      seen = { cache };
      return null;
    }
    act(() => root.render(withQueryClient(<Writer />, client)));
    await act(async () => {
      await seen.cache.setCredentials(101, (prev) => ({ ...prev, authorized_keys: ["a", "b"] }));
      seen.cache.setBootOptions(101, { boot_order: ["scsi0"] });
    });

    expect(client.getQueryData(resourceDetailKeys.credentials(101))).toEqual({ authorized_keys: ["a", "b"], username: "u" });
    expect(client.getQueryData(resourceDetailKeys.bootOptions(101))).toEqual({ boot_order: ["scsi0"] });
  });

  test("setCredentials() cancels an in-flight refetch so a removed key cannot come back", async () => {
    let resolveStale;
    mocks.getCredentials
      .mockResolvedValueOnce({ username: "u", authorized_keys: ["a", "b"] })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveStale = resolve; }));
    function CredentialsProbe() {
      const q = useCredentialsQuery(101);
      const cache = useResourceDetailCacheUpdater();
      seen = { data: q.data, cache };
      return null;
    }
    await render(<CredentialsProbe />);

    /* 刪除前就送出的重抓（例如回到分頁時資料已過期） */
    act(() => { client.refetchQueries({ queryKey: resourceDetailKeys.credentials(101) }); });
    await act(async () => {
      await seen.cache.setCredentials(101, (prev) => ({ ...prev, authorized_keys: ["a"] }));
    });
    await act(async () => resolveStale({ username: "u", authorized_keys: ["a", "b"] }));
    await flush();

    expect(seen.data).toEqual({ username: "u", authorized_keys: ["a"] });
  });

  test("invalidate(vmid) refetches every active read of that machine only", async () => {
    mocks.get.mockImplementation((vmid) => Promise.resolve({ vmid }));
    mocks.getSnapshotCapability.mockResolvedValue({ available: true });
    function Both() {
      return <><Probe vmid={101} /><Probe vmid={102} /></>;
    }
    await render(<Both />);
    expect(mocks.get).toHaveBeenCalledTimes(2);

    await act(async () => { await seen.cache.invalidate(101); });
    await flush();

    expect(mocks.get.mock.calls.map(([vmid]) => vmid)).toEqual([101, 102, 101]);
    expect(mocks.getSnapshotCapability.mock.calls.map(([vmid]) => vmid)).toEqual([101, 102, 101]);
  });

  test("invalidateCapabilities() rechecks snapshot availability", async () => {
    mocks.get.mockResolvedValue({ vmid: 101 });
    mocks.getSnapshotCapability
      .mockResolvedValueOnce({ available: true })
      .mockResolvedValueOnce({ available: false });
    await render(<Probe />);
    expect(seen.capability).toEqual({ available: true });

    await act(async () => { await seen.cache.invalidateCapabilities(101); });
    await flush();
    expect(seen.capability).toEqual({ available: false });
  });

  test("refreshDetail() cancels an in-flight refresh so a stale response cannot overwrite the fresh one", async () => {
    let resolveStale;
    mocks.get
      .mockResolvedValueOnce({ vmid: 101, status: "stopped" })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveStale = resolve; }))
      .mockResolvedValueOnce({ vmid: 101, status: "running" });
    mocks.getSnapshotCapability.mockResolvedValue({ available: true });
    await render(<Probe />);

    /* 操作前就送出的背景刷新 */
    act(() => { client.refetchQueries({ queryKey: resourceDetailKeys.detail(101) }); });
    let fresh;
    await act(async () => { fresh = await seen.cache.refreshDetail(101); });
    await act(async () => resolveStale({ vmid: 101, status: "stopped" }));
    await flush();

    expect(fresh).toEqual({ vmid: 101, status: "running" });
    expect(seen.detail).toEqual({ vmid: 101, status: "running" });
  });

  test("refreshDetail() keeps the previous data and returns undefined when the refetch fails", async () => {
    mocks.get
      .mockResolvedValueOnce({ vmid: 101, status: "stopped" })
      .mockRejectedValueOnce(new Error("pve down"));
    mocks.getSnapshotCapability.mockResolvedValue({ available: true });
    await render(<Probe />);

    let fresh = "unset";
    await act(async () => { fresh = await seen.cache.refreshDetail(101); });
    await flush();

    expect(fresh).toBeUndefined();
    expect(seen.detail).toEqual({ vmid: 101, status: "stopped" });
  });

  test("useRefreshResourceDetail() also writes the fresh machine into the cached resource lists", async () => {
    /* 測試 client 的 gcTime 是 0：沒有元件在看的清單快取要留著 */
    client.setQueryDefaults(resourceKeys.mine, { gcTime: Infinity });
    client.setQueryData(resourceKeys.mine, {
      resources: [{ vmid: 101, status: "stopped" }, { vmid: 102, status: "stopped" }],
      sessions: [],
    });
    mocks.get
      .mockResolvedValueOnce({ vmid: 101, status: "stopped" })
      .mockResolvedValueOnce({ vmid: 101, status: "running" });
    mocks.getSnapshotCapability.mockResolvedValue({ available: true });
    await render(<Probe />);

    await act(async () => { await seen.refresh(101); });
    await flush();

    expect(client.getQueryData(resourceKeys.mine).resources).toEqual([
      { vmid: 101, status: "running" },
      { vmid: 102, status: "stopped" },
    ]);
    /* 管理員清單沒載過：不動、不建立 */
    expect(client.getQueryData(resourceKeys.everyone)).toBeUndefined();
  });
});

describe("SSH key secrets", () => {
  function SshProbe({ vmid = 101 }) {
    const q = useSshKeyQuery(vmid);
    const cache = useResourceDetailCacheUpdater();
    seen = { data: q.data, isPending: q.isPending, isError: q.isError, cache };
    return null;
  }

  test("are dropped as soon as no screen shows them, even when other reads are kept for minutes", async () => {
    client = createQueryClient({ queries: { retry: false, gcTime: 5 * 60_000 } });
    mocks.getSshKey.mockResolvedValue({ login_password: "secret", ssh_private_key: "KEY" });
    await render(<SshProbe />);
    expect(mocks.getSshKey).toHaveBeenCalledWith(101, { signal: expect.any(AbortSignal) });
    expect(client.getQueryData(resourceDetailKeys.sshKey(101))).toMatchObject({ login_password: "secret" });

    act(() => root.render(withQueryClient(null, client)));
    await flush();

    expect(client.getQueryData(resourceDetailKeys.sshKey(101))).toBeUndefined();
  });

  test("a request still in flight when the screen goes away is aborted", async () => {
    let signal;
    mocks.getSshKey.mockImplementation((_vmid, options) => {
      signal = options.signal;
      return new Promise(() => {});
    });
    await render(<SshProbe />);
    expect(signal.aborted).toBe(false);

    act(() => root.render(withQueryClient(null, client)));
    await flush();

    expect(signal.aborted).toBe(true);
  });

  test("resetSshKey() never shows the old secret again, even when the refetch fails", async () => {
    let rejectNext;
    mocks.getSshKey
      .mockResolvedValueOnce({ login_password: "old" })
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectNext = reject; }));
    await render(<SshProbe />);
    expect(seen.data).toEqual({ login_password: "old" });

    await act(async () => { seen.cache.resetSshKey(101); });
    await flush();
    expect(seen.data).toBeUndefined();
    expect(seen.isPending).toBe(true);

    await act(async () => rejectNext(new Error("pve down")));
    await flush();
    expect(seen.data).toBeUndefined();
    expect(seen.isError).toBe(true);
  });
});

describe("useQueryErrorEffect", () => {
  test("reports each new failure once, with the error", async () => {
    mocks.get.mockRejectedValue(new Error("boom"));
    const onError = vi.fn();
    function Probe() {
      const q = useResourceQuery(101, { refetchInterval: false });
      useQueryErrorEffect(q, onError);
      seen = { isError: q.isError };
      return null;
    }
    await render(<Probe />);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0].message).toBe("boom");

    /* 再失敗一次（例如操作後重抓）就再通知一次；重繪本身不會重複通知 */
    await act(async () => { await client.refetchQueries({ queryKey: resourceDetailKeys.detail(101) }); });
    await flush();
    await render(<Probe />);
    expect(onError).toHaveBeenCalledTimes(2);
  });

  test("an error already in the cache when the component mounts is not reported again", async () => {
    let rejectRefetch;
    mocks.get
      .mockRejectedValueOnce(new Error("old"))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRefetch = reject; }));
    /* 測試 client 的 gcTime 是 0：這筆要留在快取裡等元件掛上 */
    await act(async () => {
      await client.prefetchQuery({ queryKey: resourceDetailKeys.detail(101), queryFn: () => mocks.get(101), gcTime: 60_000 });
    });
    const onError = vi.fn();
    function Probe() {
      const q = useResourceQuery(101, { refetchInterval: false });
      useQueryErrorEffect(q, onError);
      seen = { isError: q.isError, isFetching: q.isFetching };
      return null;
    }
    await render(<Probe />);
    /* 掛上時過期的錯誤查詢會重抓；舊的那次失敗不通知 */
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(onError).not.toHaveBeenCalled();

    await act(async () => rejectRefetch(new Error("new")));
    await flush();
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0].message).toBe("new");
  });
});

describe("useLoadError", () => {
  function Probe({ refetchInterval = false }) {
    const q = useResourceQuery(101, { refetchInterval });
    const { error, retry } = useLoadError(q);
    seen = { data: q.data, error, retry };
    return null;
  }

  test("stays on the error while a background retry is in flight, then recovers on success", async () => {
    let resolveRetry;
    mocks.get
      .mockRejectedValueOnce(Object.assign(new Error("nope"), { status: 404 }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveRetry = resolve; }));
    await render(<Probe />);
    expect(seen.error?.status).toBe(404);

    /* 輪詢重抓：沒有資料時 TanStack Query 會暫時把 error 清掉，這裡要留著上一次的錯誤 */
    act(() => { client.refetchQueries({ queryKey: resourceDetailKeys.detail(101) }); });
    await flush();
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(seen.error?.status).toBe(404);

    await act(async () => resolveRetry({ vmid: 101 }));
    await flush();
    expect(seen.error).toBeNull();
    expect(seen.data).toEqual({ vmid: 101 });
  });

  test("retry() goes back to loading until the next result", async () => {
    let rejectRetry;
    mocks.get
      .mockRejectedValueOnce(new Error("down"))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRetry = reject; }));
    await render(<Probe />);
    expect(seen.error?.message).toBe("down");

    act(() => { seen.retry(); });
    await flush();
    expect(seen.error).toBeNull();

    await act(async () => rejectRetry(new Error("still down")));
    await flush();
    expect(seen.error?.message).toBe("still down");
  });

  test("an error left in the cache shows loading until this mount's own attempt fails", async () => {
    let rejectRefetch;
    mocks.get
      .mockRejectedValueOnce(new Error("old"))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRefetch = reject; }));
    await act(async () => {
      await client.prefetchQuery({ queryKey: resourceDetailKeys.detail(101), queryFn: () => mocks.get(101), gcTime: 60_000 });
    });
    await render(<Probe />);
    expect(seen.error).toBeNull();

    await act(async () => rejectRefetch(new Error("new")));
    await flush();
    expect(seen.error?.message).toBe("new");
  });
});
