// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  INSTALL_POLL_INTERVAL,
  gatewayKeys,
  installPollInterval,
  useGatewayCacheUpdater,
  useGatewayCertificateStatusQuery,
  useGatewayInstallStatusQuery,
  useGatewayServiceConfigQuery,
} from "./gateway";

const mocks = vi.hoisted(() => ({
  getCertificateStatus: vi.fn(),
  getInstallStatus: vi.fn(),
  readServiceConfig: vi.fn(),
}));
vi.mock("../services/gateway", () => ({
  GatewayService: {
    getCertificateStatus: mocks.getCertificateStatus,
    getInstallStatus: mocks.getInstallStatus,
    readServiceConfig: mocks.readServiceConfig,
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

let host;
let root;
let client;
let seen;

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
  client.clear();
});

describe("installPollInterval", () => {
  test("polls every 3 seconds only while an install is running", () => {
    expect(installPollInterval({ state: "running" })).toBe(INSTALL_POLL_INTERVAL);
    expect(installPollInterval({ state: "succeeded" })).toBe(false);
    expect(installPollInterval(undefined)).toBe(false);
  });
});

describe("certificate status check", () => {
  function Probe() {
    const query = useGatewayCertificateStatusQuery();
    seen = { data: query.data, isFetching: query.isFetching, cache: useGatewayCacheUpdater() };
    return null;
  }

  test("a refresh during a running check waits for it, drops its result, and several refreshes run only once more", async () => {
    const first = deferred();
    mocks.getCertificateStatus
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue({ configured: true, round: "after" });

    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();
    expect(mocks.getCertificateStatus).toHaveBeenCalledTimes(1);

    act(() => { seen.cache.refreshCertificateStatus(); });
    await flush();
    act(() => { seen.cache.refreshCertificateStatus(); });
    await flush();
    /* 第一條 SSH 檢查還沒結束，新的不會同時送出 */
    expect(mocks.getCertificateStatus).toHaveBeenCalledTimes(1);
    expect(seen.isFetching).toBe(true);

    await act(async () => { first.resolve({ configured: false, round: "before" }); });
    await flush();

    expect(mocks.getCertificateStatus).toHaveBeenCalledTimes(2);
    expect(seen.data).toEqual({ configured: true, round: "after" });
    expect(seen.isFetching).toBe(false);
  });
});

describe("install status", () => {
  function Probe() {
    const query = useGatewayInstallStatusQuery({ refetchInterval: false });
    seen = { data: query.data, refetch: query.refetch, cache: useGatewayCacheUpdater() };
    return null;
  }

  test("setInstallStatus cancels a refresh that is still in flight", async () => {
    const stale = deferred();
    mocks.getInstallStatus
      .mockResolvedValueOnce({ state: "succeeded" })
      .mockReturnValueOnce(stale.promise);

    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();

    act(() => { seen.refetch(); });
    await act(async () => { await seen.cache.setInstallStatus({ state: "running" }); });
    await act(async () => { stale.resolve({ state: "succeeded" }); });
    await flush();

    expect(seen.data).toEqual({ state: "running" });
  });
});

describe("service config", () => {
  function Probe() {
    const query = useGatewayServiceConfigQuery("nginx");
    seen = { data: query.data, cache: useGatewayCacheUpdater() };
    return null;
  }

  test("setServiceConfig replaces the cached file content without refetching", async () => {
    mocks.readServiceConfig.mockResolvedValue({ path: "/etc/nginx/nginx.conf", content: "old" });

    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();
    act(() => { seen.cache.setServiceConfig("nginx", "new"); });
    await flush();

    expect(seen.data).toEqual({ path: "/etc/nginx/nginx.conf", content: "new" });
    expect(client.getQueryData(gatewayKeys.serviceConfig("nginx")).content).toBe("new");
    expect(mocks.readServiceConfig).toHaveBeenCalledTimes(1);
  });
});
