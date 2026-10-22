// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { focusManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createQueryClient } from "./queryClient";
import { withQueryClient } from "./testing";
import { ldapConfigKeys, useLdapConfigCacheUpdater, useLdapConfigQuery } from "./ldapConfig";

const mocks = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../services/ldapConfig", () => ({ LdapConfigService: { get: mocks.get } }));

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
  const query = useLdapConfigQuery();
  seen = { data: query.data, isError: query.isError, refetch: query.refetch, cache: useLdapConfigCacheUpdater() };
  return null;
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
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

describe("useLdapConfigQuery", () => {
  test("a failed load can be retried with refetch", async () => {
    mocks.get
      .mockRejectedValueOnce(new Error("503"))
      .mockResolvedValueOnce({ enabled: true, bind_password_set: true });
    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();
    expect(seen.isError).toBe(true);

    await act(async () => { await seen.refetch(); });
    await flush();
    expect(seen.data).toEqual({ enabled: true, bind_password_set: true });
  });

  test("does not refetch on window focus; setConfig stores the saved response", async () => {
    mocks.get.mockResolvedValue({ enabled: false });
    await act(async () => { root.render(withQueryClient(<Probe />, client)); });
    await flush();

    act(() => { focusManager.setFocused(false); });
    act(() => { focusManager.setFocused(true); });
    await flush();
    expect(mocks.get).toHaveBeenCalledTimes(1);

    act(() => { seen.cache.setConfig({ enabled: true }); });
    await flush();
    expect(client.getQueryData(ldapConfigKeys.config)).toEqual({ enabled: true });
    expect(seen.data).toEqual({ enabled: true });
  });
});
