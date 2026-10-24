// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import { policyKeys, useUsernameRulesQuery } from "./policies";

const mocks = vi.hoisted(() => ({ getUsernameRules: vi.fn() }));
vi.mock("../services/policies", () => ({ PoliciesService: { getUsernameRules: mocks.getUsernameRules } }));

let host;
let root;
let seen;

function Probe({ enabled }) {
  const rules = useUsernameRulesQuery({ enabled });
  seen = { data: rules.data, isError: rules.isError, isFetching: rules.isFetching };
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

describe("useUsernameRulesQuery", () => {
  test("does not fetch until enabled, then keeps the rules", async () => {
    mocks.getUsernameRules.mockResolvedValue({ max_length: 32, reserved_prefixes: ["admin"] });
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<Probe enabled={false} />, client)); });
    await flush();
    expect(mocks.getUsernameRules).not.toHaveBeenCalled();

    await act(async () => { root.render(withQueryClient(<Probe enabled />, client)); });
    await flush();
    expect(mocks.getUsernameRules).toHaveBeenCalledTimes(1);
    expect(seen.data).toEqual({ max_length: 32, reserved_prefixes: ["admin"] });
    expect(client.getQueryData(policyKeys.usernameRules)).toEqual(seen.data);

    /* 關掉再打開不重抓：規則幾乎不變，staleTime 是 Infinity */
    await act(async () => { root.render(withQueryClient(<Probe enabled={false} />, client)); });
    await act(async () => { root.render(withQueryClient(<Probe enabled />, client)); });
    await flush();
    expect(mocks.getUsernameRules).toHaveBeenCalledTimes(1);
  });

  test("a failed load exposes isError without data", async () => {
    mocks.getUsernameRules.mockRejectedValue(new Error("500"));
    await act(async () => { root.render(withQueryClient(<Probe enabled />)); });
    await flush();
    expect(seen.isError).toBe(true);
    expect(seen.data).toBeUndefined();
  });
});
