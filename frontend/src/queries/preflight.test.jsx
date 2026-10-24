// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { preflightKeys, useLoginPreflightQuery } from "./preflight";
import { createTestQueryClient, withQueryClient } from "./testing";

const mocks = vi.hoisted(() => ({ preflight: vi.fn() }));
vi.mock("../services/account", () => ({ AccountService: { preflight: mocks.preflight } }));

let host;
let root;
let client;
let seen;

function Probe(props) {
  const query = useLoginPreflightQuery(props);
  seen = { data: query.data, isError: query.isError, error: query.error, isPending: query.isPending };
  return null;
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
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
  vi.useRealTimers();
});

async function advance(ms) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

async function render(props) {
  await act(async () => { root.render(withQueryClient(<Probe {...props} />, client)); });
}

describe("useLoginPreflightQuery", () => {
  test("a quick answer is held back until the minimum duration", async () => {
    mocks.preflight.mockResolvedValue({ ok: true, checks: [] });
    await render({ attempt: 0, refresh: false, minDurationMs: 500 });
    await advance(400);
    expect(seen.isPending).toBe(true);

    await advance(120);
    expect(seen.data).toEqual({ ok: true, checks: [] });
    expect(mocks.preflight).toHaveBeenCalledWith({ refresh: false });
  });

  test("a failure is reported right away, not after the minimum duration", async () => {
    mocks.preflight.mockRejectedValue({ status: 503 });
    await render({ attempt: 0, minDurationMs: 5_000 });
    await advance(20);
    expect(seen.isError).toBe(true);
    expect(seen.error).toEqual({ status: 503 });
    expect(mocks.preflight).toHaveBeenCalledTimes(1);
  });

  test("every attempt is a fresh check with its own key; refresh is passed through", async () => {
    mocks.preflight.mockResolvedValue({ ok: false, checks: [] });
    await render({ attempt: 0, refresh: false });
    await advance(20);
    expect(seen.data).toEqual({ ok: false, checks: [] });

    await render({ attempt: 1, refresh: true });
    /* 新的一輪沒有上一輪的結果可沿用 */
    expect(seen.data).toBeUndefined();
    await advance(20);
    expect(mocks.preflight).toHaveBeenCalledTimes(2);
    expect(mocks.preflight).toHaveBeenLastCalledWith({ refresh: true });
    expect(seen.data).toEqual({ ok: false, checks: [] });
  });

  test("results are not kept once nobody shows them, so the next login checks again", async () => {
    mocks.preflight.mockResolvedValue({ ok: true, checks: [] });
    await render({ attempt: 0 });
    await advance(20);
    expect(client.getQueryData(preflightKeys.check(0, false))).toEqual({ ok: true, checks: [] });

    act(() => root.render(withQueryClient(<></>, client)));
    await advance(20);
    expect(client.getQueryData(preflightKeys.check(0, false))).toBeUndefined();

    await render({ attempt: 0 });
    await advance(20);
    expect(mocks.preflight).toHaveBeenCalledTimes(2);
  });

  test("an empty response becomes an empty (not passing) result", async () => {
    mocks.preflight.mockResolvedValue(undefined);
    await render({ attempt: 0 });
    await advance(20);
    expect(seen.isError).toBe(false);
    expect(seen.data).toEqual({});
  });

  test("window focus and reconnect do not rerun the check", async () => {
    mocks.preflight.mockResolvedValue({ ok: true, checks: [] });
    await render({ attempt: 0 });
    await advance(20);

    await act(async () => {
      window.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("focus"));
      window.dispatchEvent(new Event("online"));
    });
    await advance(20);
    expect(mocks.preflight).toHaveBeenCalledTimes(1);
  });
});
