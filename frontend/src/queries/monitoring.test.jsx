// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { withQueryClient } from "./testing";
import {
  monitoringKeys,
  useAlertsQuery,
  useMonitoringCacheUpdater,
  useNodeRrdQuery,
  usePveOverviewQuery,
  useSystemHealthQuery,
} from "./monitoring";

const mocks = vi.hoisted(() => ({
  getOverview: vi.fn(),
  listAlerts: vi.fn(),
  getSystemHealth: vi.fn(),
  getNodeRrd: vi.fn(),
}));
vi.mock("../services/monitoring", () => ({ MonitoringService: mocks }));

let host;
let root;
const seen = {};

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

describe("monitoringKeys", () => {
  test("alert filters and node/timeframe pairs get separate cache entries", () => {
    expect(monitoringKeys.alerts(undefined)).toEqual(["monitoring", "alerts", {}]);
    expect(monitoringKeys.alerts({ active: true })).not.toEqual(monitoringKeys.alerts({ active: true, limit: 100 }));
    expect(monitoringKeys.nodeRrd("pve1", "day")).toEqual(["monitoring", "nodeRrd", "pve1", "day"]);
  });
});

describe("monitoring queries", () => {
  test("overview and system health pass the abort signal to the service", async () => {
    mocks.getOverview.mockResolvedValue({ nodes_total: 1 });
    mocks.getSystemHealth.mockResolvedValue({ status: "ok", tasks: [] });
    function Probe() {
      const overview = usePveOverviewQuery();
      const health = useSystemHealthQuery();
      seen.overview = overview.data;
      seen.health = health.data;
      return null;
    }
    await act(async () => { root.render(withQueryClient(<Probe />)); });
    await flush();

    expect(seen.overview).toEqual({ nodes_total: 1 });
    expect(seen.health).toEqual({ status: "ok", tasks: [] });
    expect(mocks.getOverview.mock.calls[0][0].signal).toBeInstanceOf(AbortSignal);
    expect(mocks.getSystemHealth.mock.calls[0][0].signal).toBeInstanceOf(AbortSignal);
  });

  test("node RRD waits for a node name", async () => {
    mocks.getNodeRrd.mockResolvedValue([{ time: 1 }]);
    function Probe({ node }) {
      seen.rrd = useNodeRrdQuery(node, "day").data;
      return null;
    }
    await act(async () => { root.render(withQueryClient(<Probe node={null} />)); });
    await flush();
    expect(mocks.getNodeRrd).not.toHaveBeenCalled();

    await act(async () => { root.render(withQueryClient(<Probe node="pve1" />)); });
    await flush();
    expect(mocks.getNodeRrd).toHaveBeenCalledWith("pve1", "day");
    expect(seen.rrd).toEqual([{ time: 1 }]);
  });

  test("invalidateAlerts() refetches every alert filter but not the overview", async () => {
    mocks.listAlerts.mockResolvedValue([]);
    mocks.getOverview.mockResolvedValue({});
    function Probe() {
      seen.panel = useAlertsQuery({ active: true }).data;
      seen.dashboard = useAlertsQuery({ active: true, limit: 100 }).data;
      seen.overview = usePveOverviewQuery().data;
      seen.updater = useMonitoringCacheUpdater();
      return null;
    }
    await act(async () => { root.render(withQueryClient(<Probe />)); });
    await flush();
    expect(mocks.listAlerts).toHaveBeenCalledTimes(2);

    await act(async () => { await seen.updater.invalidateAlerts(); });
    await flush();
    expect(mocks.listAlerts).toHaveBeenCalledTimes(4);
    expect(mocks.getOverview).toHaveBeenCalledTimes(1);
  });
});
