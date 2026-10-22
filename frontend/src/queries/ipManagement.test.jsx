// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  ipManagementKeys,
  useIpAllocationsQuery,
  useIpManagementCacheUpdater,
  useIpStatusQuery,
  useSubnetQuery,
} from "./ipManagement";

const mocks = vi.hoisted(() => ({ getStatus: vi.fn(), getSubnet: vi.fn(), listAllocations: vi.fn() }));
vi.mock("../services/ipManagement", () => ({ IpManagementService: mocks }));

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

function Probe({ statusStaleTime }) {
  const status = useIpStatusQuery({ refetchInterval: false, staleTime: statusStaleTime });
  const subnet = useSubnetQuery();
  const allocations = useIpAllocationsQuery({ refetchInterval: false });
  seen = {
    status: status.data,
    subnet: subnet.data,
    subnetStatus: subnet.status,
    allocations: allocations.data,
    cache: useIpManagementCacheUpdater(),
  };
  return null;
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.getStatus.mockResolvedValue({ configured: false });
  /* 尚未設定子網時後端回 null，要當成正常資料而不是錯誤 */
  mocks.getSubnet.mockResolvedValue(null);
  mocks.listAllocations.mockResolvedValue({ allocations: [] });
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

describe("ipManagement queries", () => {
  test("an unconfigured subnet (null) is a successful result", async () => {
    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();
    expect(seen.subnet).toBeNull();
    expect(seen.subnetStatus).toBe("success");
    expect(seen.status).toEqual({ configured: false });
  });

  test("the staleTime option keeps the status fresh for that long (the banner's 60 s window)", async () => {
    await act(async () => { root.render(withQueryClient(<Probe statusStaleTime={60_000} />, client)); });
    await flush();
    const query = client.getQueryCache().find({ queryKey: ipManagementKeys.status });
    expect(query.observers[0].options.staleTime).toBe(60_000);
    expect(query.isStale()).toBe(false);

    /* 測試用 client 的 staleTime 是 0：沒給選項時沿用它，馬上就過期 */
    act(() => root.unmount());
    root = createRoot(host);
    client.clear();
    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();
    expect(client.getQueryCache().find({ queryKey: ipManagementKeys.status }).observers[0].options.staleTime).toBe(0);
  });

  test("invalidate refetches status, subnet and allocations", async () => {
    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();
    await act(async () => { await seen.cache.invalidate(); });

    expect(mocks.getStatus).toHaveBeenCalledTimes(2);
    expect(mocks.getSubnet).toHaveBeenCalledTimes(2);
    expect(mocks.listAllocations).toHaveBeenCalledTimes(2);
  });
});
