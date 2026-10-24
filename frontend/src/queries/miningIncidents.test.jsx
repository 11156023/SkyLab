// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import { miningIncidentKeys, useMiningIncidentCacheUpdater, useMiningIncidentsQuery } from "./miningIncidents";

const mocks = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("../services/miningIncidents", () => ({ MiningIncidentsService: { list: mocks.list } }));

let host;
let root;
const seen = {};

function Probe({ name, params }) {
  const query = useMiningIncidentsQuery(params);
  const updater = useMiningIncidentCacheUpdater();
  seen[name] = { data: query.data, isError: query.isError, updater };
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

describe("miningIncidentKeys", () => {
  test("each status filter gets its own cache entry", () => {
    expect(miningIncidentKeys.list(undefined)).toEqual(["miningIncidents", "list", {}]);
    expect(miningIncidentKeys.list({ status: "detected", limit: 1000 }))
      .not.toEqual(miningIncidentKeys.list({ status: "suspended", limit: 1000 }));
  });
});

describe("useMiningIncidentsQuery", () => {
  test("without params it lists every incident with the service defaults", async () => {
    mocks.list.mockResolvedValue([{ id: "i1", status: "detected" }]);
    await act(async () => { root.render(withQueryClient(<Probe name="all" />)); });
    await flush();

    expect(mocks.list).toHaveBeenCalledWith(undefined);
    expect(seen.all.data).toEqual([{ id: "i1", status: "detected" }]);
  });

  test("a failed background refresh keeps the previous list", async () => {
    mocks.list
      .mockResolvedValueOnce([{ id: "i1", status: "detected" }])
      .mockRejectedValueOnce(new Error("503"));
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<Probe name="all" />, client)); });
    await flush();

    await act(async () => { await client.refetchQueries({ queryKey: miningIncidentKeys.all }); });
    await flush();

    expect(seen.all.isError).toBe(true);
    expect(seen.all.data).toEqual([{ id: "i1", status: "detected" }]);
  });

  test("invalidate() after ban/dismiss refetches every status filter (panel and dashboard counts)", async () => {
    mocks.list.mockResolvedValue([]);
    await act(async () => {
      root.render(withQueryClient(
        <>
          <Probe name="all" />
          <Probe name="detected" params={{ status: "detected", limit: 1000 }} />
        </>,
      ));
    });
    await flush();
    expect(mocks.list).toHaveBeenCalledTimes(2);

    await act(async () => { await seen.all.updater.invalidate(); });
    await flush();
    expect(mocks.list).toHaveBeenCalledTimes(4);
    expect(mocks.list.mock.calls.filter(([params]) => params?.status === "detected")).toHaveLength(2);
  });
});
