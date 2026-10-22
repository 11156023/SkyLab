// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getStatus: vi.fn(),
  getGateway: vi.fn(),
  getGatewayInstallStatus: vi.fn(),
  getPlatformEntry: vi.fn(),
}));
vi.mock("../services/setup", () => ({
  SetupService: {
    getStatus: mocks.getStatus,
    getGateway: mocks.getGateway,
    getGatewayInstallStatus: mocks.getGatewayInstallStatus,
    getPlatformEntry: mocks.getPlatformEntry,
  },
}));

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

const INCOMPLETE = { completed: false, steps: { admin: true, proxmox: false, subnet: false } };

/* 「最後一次的初始化狀態」是模組層的值：每個測試重新載入模組，彼此不互相影響 */
let setup;
let withQueryClient;
let client;
let host;
let root;
let seen;

beforeEach(async () => {
  vi.resetAllMocks();
  vi.resetModules();
  setup = await import("./setup");
  const testing = await import("./testing");
  withQueryClient = testing.withQueryClient;
  client = testing.createTestQueryClient();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
  vi.useRealTimers();
});

describe("setup status", () => {
  function Probe({ name }) {
    const query = setup.useSetupStatusQuery();
    seen[name] = { data: query.data, isPending: query.isPending };
    return null;
  }

  test("two observers share one request and the status is not refetched afterwards", async () => {
    seen = {};
    mocks.getStatus.mockResolvedValue(INCOMPLETE);

    await act(async () => {
      root.render(withQueryClient(<><Probe name="login" /><Probe name="setup" /></>, client));
    });
    await flush();

    expect(mocks.getStatus).toHaveBeenCalledTimes(1);
    expect(seen.login.data).toEqual(INCOMPLETE);
    expect(seen.setup.data).toEqual(INCOMPLETE);

    /* 換頁再回來（重新掛載）也不再問 */
    act(() => root.render(withQueryClient(<></>, client)));
    await act(async () => { root.render(withQueryClient(<Probe name="again" />, client)); });
    await flush();
    expect(mocks.getStatus).toHaveBeenCalledTimes(1);
    expect(seen.again.data).toEqual(INCOMPLETE);
  });

  test("survives queryClient.clear() (identity switch) without another request", async () => {
    seen = {};
    mocks.getStatus.mockResolvedValue(INCOMPLETE);
    await act(async () => { root.render(withQueryClient(<Probe name="before" />, client)); });
    await flush();

    act(() => root.render(withQueryClient(<></>, client)));
    client.clear();
    await act(async () => { root.render(withQueryClient(<Probe name="after" />, client)); });

    /* 第一次 render 就有資料：登入頁不會先閃「驗證中」 */
    expect(seen.after.isPending).toBe(false);
    expect(seen.after.data).toEqual(INCOMPLETE);
    await flush();
    expect(mocks.getStatus).toHaveBeenCalledTimes(1);
  });

  test("setSetupStatusData updates the cache and what survives a clear", async () => {
    seen = {};
    mocks.getStatus.mockResolvedValue(INCOMPLETE);
    await act(async () => { root.render(withQueryClient(<Probe name="page" />, client)); });
    await flush();

    act(() => { setup.setSetupStatusData(client, (prev) => ({ ...prev, completed: true })); });
    await flush();
    expect(seen.page.data).toEqual({ ...INCOMPLETE, completed: true });

    /* 清掉之後 updater 拿到的是最後一次的狀態，不是 undefined */
    client.clear();
    const updater = vi.fn((prev) => ({ ...prev, marked: true }));
    setup.setSetupStatusData(client, updater);
    expect(updater).toHaveBeenCalledWith({ ...INCOMPLETE, completed: true });
    expect(client.getQueryData(setup.setupKeys.status)).toEqual({ ...INCOMPLETE, completed: true, marked: true });
    expect(mocks.getStatus).toHaveBeenCalledTimes(1);
  });

  test("a failed request leaves no status and is retried by the next mount", async () => {
    seen = {};
    mocks.getStatus.mockRejectedValueOnce(new Error("down")).mockResolvedValueOnce(INCOMPLETE);
    await act(async () => { root.render(withQueryClient(<Probe name="first" />, client)); });
    await flush();
    expect(seen.first.data).toBeUndefined();

    act(() => root.render(withQueryClient(<></>, client)));
    await act(async () => { root.render(withQueryClient(<Probe name="second" />, client)); });
    await flush();
    expect(mocks.getStatus).toHaveBeenCalledTimes(2);
    expect(seen.second.data).toEqual(INCOMPLETE);
  });
});

describe("gateway step queries", () => {
  function Probe({ enabled = true }) {
    const gateway = setup.useSetupGatewayQuery({ enabled });
    const install = setup.useSetupGatewayInstallQuery({ enabled });
    seen = {
      gateway: gateway.data,
      install: install.data,
      refetchInstall: install.refetch,
      cache: setup.useSetupCacheUpdater(),
    };
    return null;
  }

  test("disabled queries do not call the API", async () => {
    await act(async () => { root.render(withQueryClient(<Probe enabled={false} />, client)); });
    await flush();
    expect(mocks.getGateway).not.toHaveBeenCalled();
    expect(mocks.getGatewayInstallStatus).not.toHaveBeenCalled();
  });

  test("install status polls every 3 seconds only while running", async () => {
    vi.useFakeTimers();
    mocks.getGateway.mockResolvedValue({ is_configured: true });
    mocks.getGatewayInstallStatus
      .mockResolvedValueOnce({ state: "running" })
      .mockResolvedValueOnce({ state: "running" })
      .mockResolvedValue({ state: "succeeded" });

    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(seen.install).toEqual({ state: "running" });
    expect(mocks.getGatewayInstallStatus).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(mocks.getGatewayInstallStatus).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(mocks.getGatewayInstallStatus).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(seen.install).toEqual({ state: "succeeded" });

    /* 安裝結束就不再輪詢 */
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.getGatewayInstallStatus).toHaveBeenCalledTimes(3);
  });

  test("clearGatewayInstall drops the status and a read still in flight", async () => {
    const late = deferred();
    mocks.getGateway.mockResolvedValue({ is_configured: true });
    mocks.getGatewayInstallStatus
      .mockResolvedValueOnce({ state: "succeeded" })
      .mockReturnValueOnce(late.promise);

    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();
    expect(seen.install).toEqual({ state: "succeeded" });

    act(() => { seen.refetchInstall(); });
    act(() => { seen.cache.clearGatewayInstall(); });
    await act(async () => { late.resolve({ state: "idle", from: "old target" }); });
    await flush();

    expect(client.getQueryData(setup.setupKeys.gatewayInstall)).toBeUndefined();
    /* 連線設定不受影響 */
    expect(client.getQueryData(setup.setupKeys.gateway)).toEqual({ is_configured: true });
  });

  test("setGatewayInstall cancels a poll that is still in flight", async () => {
    const stale = deferred();
    mocks.getGateway.mockResolvedValue({ is_configured: true });
    mocks.getGatewayInstallStatus
      .mockResolvedValueOnce({ state: "succeeded" })
      .mockReturnValueOnce(stale.promise);

    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();

    act(() => { seen.refetchInstall(); });
    await act(async () => { await seen.cache.setGatewayInstall({ state: "running" }); });
    await act(async () => { stale.resolve({ state: "succeeded" }); });
    await flush();

    expect(seen.install).toEqual({ state: "running" });
  });
});

describe("platform entry query", () => {
  function Probe({ enabled }) {
    const query = setup.useSetupPlatformEntryQuery({ enabled });
    seen = { data: query.data, cache: setup.useSetupCacheUpdater() };
    return null;
  }

  test("loads only when enabled and setPlatformEntry replaces the cached copy", async () => {
    mocks.getPlatformEntry.mockResolvedValue({ enabled: false, domain: "" });
    await act(async () => { root.render(withQueryClient(<Probe enabled={false} />, client)); });
    await flush();
    expect(mocks.getPlatformEntry).not.toHaveBeenCalled();

    await act(async () => { root.render(withQueryClient(<Probe enabled />, client)); });
    await flush();
    expect(seen.data).toEqual({ enabled: false, domain: "" });

    act(() => { seen.cache.setPlatformEntry({ enabled: true, domain: "skylab.example.com" }); });
    await flush();
    expect(seen.data).toEqual({ enabled: true, domain: "skylab.example.com" });
    expect(mocks.getPlatformEntry).toHaveBeenCalledTimes(1);
  });
});
