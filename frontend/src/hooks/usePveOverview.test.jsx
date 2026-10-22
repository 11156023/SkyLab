// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "../queries/testing";
import usePveOverview from "./usePveOverview";

const mocks = vi.hoisted(() => ({ getOverview: vi.fn() }));
vi.mock("../services/monitoring", () => ({ MonitoringService: { getOverview: mocks.getOverview } }));

let host;
let root;
let seen;
const history = [];

function Probe() {
  seen = usePveOverview();
  history.push({ loading: seen.loading, refreshing: seen.refreshing, error: seen.error });
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
  history.length = 0;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

test("loading until the first result, then the overview", async () => {
  mocks.getOverview.mockResolvedValue({ nodes_total: 2 });
  await act(async () => { root.render(withQueryClient(<Probe />)); });
  expect(history[0]).toEqual({ loading: true, refreshing: false, error: false });
  await flush();

  expect(seen.overview).toEqual({ nodes_total: 2 });
  expect(seen.loading).toBe(false);
  expect(seen.error).toBe(false);
});

test("a failed background refresh keeps the overview and flags error until the next success", async () => {
  mocks.getOverview
    .mockResolvedValueOnce({ nodes_total: 2 })
    .mockRejectedValueOnce(new Error("503"))
    .mockResolvedValueOnce({ nodes_total: 3 });
  const client = createTestQueryClient();
  await act(async () => { root.render(withQueryClient(<Probe />, client)); });
  await flush();

  await act(async () => { await seen.reload(); });
  await flush();
  expect(seen.overview).toEqual({ nodes_total: 2 });
  expect(seen.error).toBe(true);
  expect(seen.loading).toBe(false);

  await act(async () => { await seen.reload(); });
  await flush();
  expect(seen.overview).toEqual({ nodes_total: 3 });
  expect(seen.error).toBe(false);
  expect(history.slice(1).every((h) => !h.loading)).toBe(true);
});

test("refetching with data counts as refreshing, not loading", async () => {
  let resolveSecond;
  mocks.getOverview
    .mockResolvedValueOnce({ nodes_total: 2 })
    .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
  await act(async () => { root.render(withQueryClient(<Probe />)); });
  await flush();

  let reloading;
  await act(async () => { reloading = seen.reload(); });
  await flush();
  expect(seen.refreshing).toBe(true);
  expect(seen.loading).toBe(false);
  expect(seen.overview).toEqual({ nodes_total: 2 });

  await act(async () => { resolveSecond({ nodes_total: 4 }); await reloading; });
  await flush();
  expect(seen.refreshing).toBe(false);
  expect(seen.overview).toEqual({ nodes_total: 4 });
});

test("retrying while PVE is still unreachable does not flash back to loading", async () => {
  mocks.getOverview.mockRejectedValue(new Error("PVE down"));
  await act(async () => { root.render(withQueryClient(<Probe />)); });
  await flush();
  expect(seen.loading).toBe(false);
  expect(seen.error).toBe(true);
  expect(seen.overview).toBeNull();

  history.length = 0;
  await act(async () => { await seen.reload(); });
  await flush();
  expect(mocks.getOverview).toHaveBeenCalledTimes(2);
  expect(history.every((h) => !h.loading && h.error)).toBe(true);
});
