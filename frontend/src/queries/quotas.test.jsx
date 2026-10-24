// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { focusManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createQueryClient } from "./queryClient";
import { withQueryClient } from "./testing";
import { useGlobalQuotaQuery, useQuotaCacheUpdater, useQuotasQuery } from "./quotas";

const mocks = vi.hoisted(() => ({ list: vi.fn(), getGlobal: vi.fn() }));
vi.mock("../services/quotas", () => ({ QuotasService: mocks }));

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

function Probe() {
  const list = useQuotasQuery();
  const global = useGlobalQuotaQuery();
  seen = { list: list.data, global: global.data, cache: useQuotaCacheUpdater() };
  return null;
}

beforeEach(async () => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.list.mockResolvedValue([{ id: "q1" }]);
  mocks.getGlobal.mockResolvedValue({ max_cpu_cores: 8 });
  client = createQueryClient({ queries: { staleTime: 0, gcTime: 0 } });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => { root.render(withQueryClient(<Probe />, client)); });
  await flush();
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
  focusManager.setFocused(undefined);
});

describe("quota queries", () => {
  test("setGlobal stores the saved defaults without refetching", async () => {
    act(() => { seen.cache.setGlobal({ max_cpu_cores: 16 }); });
    await flush();
    expect(seen.global).toEqual({ max_cpu_cores: 16 });
    expect(mocks.getGlobal).toHaveBeenCalledTimes(1);
  });

  test("the global defaults form is not refetched on window focus, the overrides list is", async () => {
    act(() => { focusManager.setFocused(false); });
    act(() => { focusManager.setFocused(true); });
    await flush();
    expect(mocks.getGlobal).toHaveBeenCalledTimes(1);
    expect(mocks.list).toHaveBeenCalledTimes(2);
  });

  test("invalidate refetches the overrides and the global defaults", async () => {
    await act(async () => { await seen.cache.invalidate(); });
    expect(mocks.list).toHaveBeenCalledTimes(2);
    expect(mocks.getGlobal).toHaveBeenCalledTimes(2);
  });
});
