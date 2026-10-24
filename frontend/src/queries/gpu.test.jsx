// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import { gpuKeys, useGpuCacheUpdater, useGpuMappingsQuery, useGpuOptionsQuery } from "./gpu";

const mocks = vi.hoisted(() => ({ listOptions: vi.fn(), listMappings: vi.fn() }));
vi.mock("../services/gpu", () => ({
  GpuService: { listOptions: mocks.listOptions, listMappings: mocks.listMappings },
}));

let host;
let root;
let seen;

/* useQuery 的結果是存取追蹤的：欄位要在 render 期間讀過，資料變了才會重繪 */
function OptionsProbe({ params, enabled }) {
  const query = useGpuOptionsQuery(params, { enabled });
  seen = { data: query.data, isError: query.isError };
  return null;
}

function MappingsProbe() {
  const query = useGpuMappingsQuery();
  const updater = useGpuCacheUpdater();
  seen = { data: query.data, isError: query.isError, updater };
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

describe("gpuKeys", () => {
  test("option keys are per params", () => {
    expect(gpuKeys.options({ node: "pve-a" })).toEqual(["gpu", "options", { node: "pve-a" }]);
    expect(gpuKeys.options(undefined)).toEqual(["gpu", "options", {}]);
  });
});

describe("useGpuOptionsQuery", () => {
  test("does not fetch while disabled; fetches with the node filter once enabled", async () => {
    mocks.listOptions.mockResolvedValue([{ mapping_id: "h200" }]);
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<OptionsProbe params={{ node: "pve-a" }} enabled={false} />, client)); });
    await flush();
    expect(mocks.listOptions).not.toHaveBeenCalled();

    await act(async () => { root.render(withQueryClient(<OptionsProbe params={{ node: "pve-a" }} enabled />, client)); });
    await flush();
    expect(mocks.listOptions).toHaveBeenCalledWith({ node: "pve-a" });
    expect(seen.data).toEqual([{ mapping_id: "h200" }]);
  });
});

describe("useGpuMappingsQuery", () => {
  test("loads mappings and invalidate() refetches them", async () => {
    mocks.listMappings
      .mockResolvedValueOnce({ data: [{ id: "a" }] })
      .mockResolvedValueOnce({ data: [] });
    await act(async () => { root.render(withQueryClient(<MappingsProbe />)); });
    await flush();
    expect(seen.data).toEqual({ data: [{ id: "a" }] });

    await act(async () => { await seen.updater.invalidate(); });
    await flush();
    expect(mocks.listMappings).toHaveBeenCalledTimes(2);
    expect(seen.data).toEqual({ data: [] });
  });

  test("a failed background refresh keeps the previous mappings", async () => {
    mocks.listMappings
      .mockResolvedValueOnce({ data: [{ id: "a" }] })
      .mockRejectedValueOnce(new Error("pve down"));
    await act(async () => { root.render(withQueryClient(<MappingsProbe />)); });
    await flush();

    await act(async () => { await seen.updater.invalidate(); });
    await flush();
    expect(seen.isError).toBe(true);
    expect(seen.data).toEqual({ data: [{ id: "a" }] });
  });
});
