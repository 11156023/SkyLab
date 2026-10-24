// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import { useAuditLogsQuery, useAuditStatsQuery } from "./auditLogs";

const mocks = vi.hoisted(() => ({ list: vi.fn(), stats: vi.fn() }));
vi.mock("../services/auditLogs", () => ({ AuditLogsService: mocks }));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
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

describe("useAuditLogsQuery", () => {
  function Probe({ params }) {
    const query = useAuditLogsQuery(params, { refetchInterval: false });
    seen = { data: query.data, isPlaceholderData: query.isPlaceholderData };
    return null;
  }

  test("changing filters keeps the previous page on screen until the new one arrives", async () => {
    const next = deferred();
    mocks.list
      .mockResolvedValueOnce({ data: [{ id: 1 }], count: 1 })
      .mockReturnValueOnce(next.promise);

    await act(async () => { root.render(withQueryClient(<Probe params={{ skip: 0, limit: 50 }} />, client)); });
    await flush();
    await act(async () => { root.render(withQueryClient(<Probe params={{ skip: 0, limit: 50, action: "login" }} />, client)); });
    await flush();

    expect(seen.data).toEqual({ data: [{ id: 1 }], count: 1 });
    expect(seen.isPlaceholderData).toBe(true);
    expect(mocks.list).toHaveBeenLastCalledWith({ skip: 0, limit: 50, action: "login" });

    await act(async () => { next.resolve({ data: [{ id: 2 }], count: 1 }); });
    await flush();
    expect(seen.data).toEqual({ data: [{ id: 2 }], count: 1 });
    expect(seen.isPlaceholderData).toBe(false);
  });
});

describe("useAuditStatsQuery", () => {
  function Probe({ params }) {
    const query = useAuditStatsQuery(params, { refetchInterval: false });
    seen = { data: query.data, isError: query.isError };
    return null;
  }

  test("a failed range has no data (the summary card is hidden instead of showing the previous range)", async () => {
    const next = deferred();
    mocks.stats
      .mockResolvedValueOnce({ total: 1 })
      .mockReturnValueOnce(next.promise);

    await act(async () => { root.render(withQueryClient(<Probe params={{}} />, client)); });
    await flush();
    await act(async () => { root.render(withQueryClient(<Probe params={{ startTime: "2025-01-01T00:00:00.000Z" }} />, client)); });
    await flush();
    expect(seen.data).toEqual({ total: 1 });

    await act(async () => { next.reject(new Error("503")); });
    await flush();
    expect(seen.isError).toBe(true);
    expect(seen.data).toBeUndefined();
  });
});
