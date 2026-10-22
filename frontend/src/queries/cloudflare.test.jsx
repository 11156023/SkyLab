// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  cloudflareKeys,
  useCloudflareCacheUpdater,
  useCloudflareConfigQuery,
  useCloudflareDnsRecordsQuery,
} from "./cloudflare";

const mocks = vi.hoisted(() => ({ getConfig: vi.fn(), listDnsRecords: vi.fn() }));
vi.mock("../services/cloudflare", () => ({
  CloudflareService: { getConfig: mocks.getConfig, listDnsRecords: mocks.listDnsRecords },
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

describe("useCloudflareDnsRecordsQuery", () => {
  function Probe({ zoneId }) {
    const query = useCloudflareDnsRecordsQuery(zoneId, { per_page: 100 });
    seen = { data: query.data, fetchStatus: query.fetchStatus, cache: useCloudflareCacheUpdater() };
    return null;
  }

  test("does not fetch until a zone is selected", async () => {
    await act(async () => { root.render(withQueryClient(<Probe zoneId={null} />, client)); });
    await flush();
    expect(mocks.listDnsRecords).not.toHaveBeenCalled();
    expect(seen.fetchStatus).toBe("idle");
  });

  test("switching zones never shows the previous zone's records while the new ones load", async () => {
    const zoneB = deferred();
    mocks.listDnsRecords
      .mockResolvedValueOnce({ items: [{ id: "a1" }] })
      .mockReturnValueOnce(zoneB.promise);

    await act(async () => { root.render(withQueryClient(<Probe zoneId="zone-a" />, client)); });
    await flush();
    expect(seen.data.items).toEqual([{ id: "a1" }]);

    await act(async () => { root.render(withQueryClient(<Probe zoneId="zone-b" />, client)); });
    await flush();
    /* 這時按編輯／刪除會拿 A 的 record id 打到 B 的 zone，所以不能先顯示 A 的紀錄 */
    expect(seen.data).toBeUndefined();

    await act(async () => { zoneB.resolve({ items: [{ id: "b1" }] }); });
    await flush();
    expect(seen.data.items).toEqual([{ id: "b1" }]);
  });

  test("invalidateDnsRecords(zoneId) refetches only that zone; without a zone it refetches every zone", async () => {
    mocks.listDnsRecords.mockResolvedValue({ items: [] });
    function Other() {
      useCloudflareDnsRecordsQuery("zone-b", { per_page: 100 });
      return null;
    }
    await act(async () => {
      root.render(withQueryClient(<><Probe zoneId="zone-a" /><Other /></>, client));
    });
    await flush();
    expect(mocks.listDnsRecords).toHaveBeenCalledTimes(2);

    await act(async () => { await seen.cache.invalidateDnsRecords("zone-a"); });
    expect(mocks.listDnsRecords).toHaveBeenCalledTimes(3);
    expect(mocks.listDnsRecords).toHaveBeenLastCalledWith("zone-a", { per_page: 100 });
    expect(client.getQueryState(cloudflareKeys.dnsRecords("zone-b", { per_page: 100 })).isInvalidated).toBe(false);

    await act(async () => { await seen.cache.invalidateDnsRecords(); });
    expect(mocks.listDnsRecords).toHaveBeenCalledTimes(5);
  });
});

describe("useCloudflareConfigQuery", () => {
  function Probe() {
    const query = useCloudflareConfigQuery();
    seen = { data: query.data, cache: useCloudflareCacheUpdater() };
    return null;
  }

  test("setConfig writes the saved config into the cache without refetching", async () => {
    mocks.getConfig.mockResolvedValue({ is_configured: false });
    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();

    act(() => { seen.cache.setConfig({ is_configured: true }); });
    await flush();
    expect(seen.data).toEqual({ is_configured: true });
    expect(mocks.getConfig).toHaveBeenCalledTimes(1);
  });
});
