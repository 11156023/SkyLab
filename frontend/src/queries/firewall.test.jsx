// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  firewallKeys,
  useFirewallCacheUpdater,
  useFirewallTopologyQuery,
  useVmFirewallOptionsQuery,
  useVmFirewallRulesQuery,
} from "./firewall";

const mocks = vi.hoisted(() => ({
  getTopology: vi.fn(),
  getVmRules: vi.fn(),
  getVmOptions: vi.fn(),
  getVmTopology: vi.fn(),
}));
vi.mock("../services/firewall", () => ({
  getTopology: mocks.getTopology,
  getVmRules: mocks.getVmRules,
  getVmOptions: mocks.getVmOptions,
  getVmTopology: mocks.getVmTopology,
}));

let host;
let root;
let seen;

/* useQuery 的結果是存取追蹤的：欄位要在 render 期間讀過，資料變了才會重繪（頁面本來就是這樣用） */
function Probe({ vmid = null }) {
  const topology = useFirewallTopologyQuery();
  const rules = useVmFirewallRulesQuery(vmid);
  const options = useVmFirewallOptionsQuery(vmid);
  const updater = useFirewallCacheUpdater();
  seen = {
    topology: topology.data,
    topologyError: topology.isError,
    rules: rules.data,
    options: options.data,
    updater,
  };
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

describe("firewall queries", () => {
  test("an empty topology response becomes { nodes: [], edges: [] }", async () => {
    mocks.getTopology.mockResolvedValue(null);
    await act(async () => { root.render(withQueryClient(<Probe />)); });
    await flush();

    expect(seen.topology).toEqual({ nodes: [], edges: [] });
    expect(mocks.getTopology).toHaveBeenCalledTimes(1);
    expect(mocks.getVmRules).not.toHaveBeenCalled();
    expect(mocks.getVmOptions).not.toHaveBeenCalled();
  });

  test("per-vm rules and options load only when a vmid is given, keyed by vmid", async () => {
    mocks.getTopology.mockResolvedValue({ nodes: [], edges: [] });
    mocks.getVmRules.mockResolvedValue([{ pos: 0 }]);
    mocks.getVmOptions.mockResolvedValue({ enable: 1 });
    await act(async () => { root.render(withQueryClient(<Probe vmid={101} />)); });
    await flush();

    expect(mocks.getVmRules).toHaveBeenCalledWith(101);
    expect(mocks.getVmOptions).toHaveBeenCalledWith(101);
    expect(seen.rules).toEqual([{ pos: 0 }]);
    expect(seen.options).toEqual({ enable: 1 });
    expect(firewallKeys.vmRules(101)).toEqual(["firewall", "vm", "101", "rules"]);
  });

  test("invalidateVm refetches that machine's rules and options but not the topology", async () => {
    mocks.getTopology.mockResolvedValue({ nodes: [], edges: [] });
    mocks.getVmRules.mockResolvedValue([]);
    mocks.getVmOptions.mockResolvedValue({});
    const client = createTestQueryClient();
    await act(async () => { root.render(withQueryClient(<Probe vmid={101} />, client)); });
    await flush();

    await act(async () => { await seen.updater.invalidateVm(101); });
    await flush();

    expect(mocks.getVmRules).toHaveBeenCalledTimes(2);
    expect(mocks.getVmOptions).toHaveBeenCalledTimes(2);
    expect(mocks.getTopology).toHaveBeenCalledTimes(1);
  });

  test("a failed topology load exposes isError without data", async () => {
    mocks.getTopology.mockRejectedValue(new Error("gateway down"));
    await act(async () => { root.render(withQueryClient(<Probe />)); });
    await flush();

    expect(seen.topologyError).toBe(true);
    expect(seen.topology).toBeUndefined();
  });
});
