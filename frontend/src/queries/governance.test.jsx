// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { focusManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createQueryClient } from "./queryClient";
import { withQueryClient } from "./testing";
import { useGovernanceCacheUpdater, useGovernanceConfigQuery } from "./governance";

const mocks = vi.hoisted(() => ({ getConfig: vi.fn() }));
vi.mock("../services/governance", () => ({ GovernanceService: { getConfig: mocks.getConfig } }));

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
  const query = useGovernanceConfigQuery();
  seen = { data: query.data, cache: useGovernanceCacheUpdater() };
  return null;
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  /* 用正式的全站預設（切回分頁會重抓），確認這支 hook 自己關掉了 */
  client = createQueryClient({ queries: { staleTime: 0, gcTime: 0 } });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
  focusManager.setFocused(undefined);
});

describe("useGovernanceConfigQuery", () => {
  test("loads once and does not refetch when the tab regains focus (would wipe unsaved edits)", async () => {
    mocks.getConfig.mockResolvedValue({ idle_detection_enabled: true });
    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();
    expect(seen.data).toEqual({ idle_detection_enabled: true });

    act(() => { focusManager.setFocused(false); });
    act(() => { focusManager.setFocused(true); });
    await flush();
    expect(mocks.getConfig).toHaveBeenCalledTimes(1);
  });

  test("setConfig stores the saved response", async () => {
    mocks.getConfig.mockResolvedValue({ idle_detection_enabled: true });
    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();

    act(() => { seen.cache.setConfig({ idle_detection_enabled: false }); });
    await flush();
    expect(seen.data).toEqual({ idle_detection_enabled: false });
  });
});
