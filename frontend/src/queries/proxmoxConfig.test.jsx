// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  usePveConfigQuery,
  usePveConnectionsQuery,
  usePveNodesQuery,
  usePveStoragesQuery,
  useProxmoxConfigCacheUpdater,
} from "./proxmoxConfig";

const mocks = vi.hoisted(() => ({
  listConnections: vi.fn(),
  getConfig: vi.fn(),
  getNodes: vi.fn(),
  getStorages: vi.fn(),
}));
vi.mock("../services/proxmoxConfig", () => ({ ProxmoxConfigService: mocks }));

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
  const connections = usePveConnectionsQuery({ refetchInterval: false });
  const nodes = usePveNodesQuery({ refetchInterval: false });
  const storages = usePveStoragesQuery();
  const config = usePveConfigQuery();
  seen = {
    connections: connections.data,
    nodes: nodes.data,
    storages: storages.data,
    config: config.data,
    cache: useProxmoxConfigCacheUpdater(),
  };
  return null;
}

beforeEach(async () => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.listConnections.mockResolvedValue([{ id: "c1" }]);
  mocks.getNodes.mockResolvedValue([{ id: 1, name: "pve1", enabled: true }, { id: 2, name: "pve2", enabled: true }]);
  mocks.getStorages.mockResolvedValue([{ id: 7, storage: "local-lvm", user_priority: 5 }]);
  mocks.getConfig.mockResolvedValue({ placement_strategy: "balanced" });
  client = createTestQueryClient();
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
});

describe("proxmoxConfig queries", () => {
  test("updateNode / updateStorage / setConfig patch the cache in place", async () => {
    act(() => {
      seen.cache.updateNode({ id: 2, name: "pve2", enabled: false });
      seen.cache.updateStorage({ id: 7, storage: "local-lvm", user_priority: 9 });
      seen.cache.setConfig({ placement_strategy: "packed" });
    });
    await flush();

    expect(seen.nodes).toEqual([{ id: 1, name: "pve1", enabled: true }, { id: 2, name: "pve2", enabled: false }]);
    expect(seen.storages[0].user_priority).toBe(9);
    expect(seen.config).toEqual({ placement_strategy: "packed" });
    expect(mocks.getNodes).toHaveBeenCalledTimes(1);
    expect(mocks.getStorages).toHaveBeenCalledTimes(1);
  });

  test("invalidateConnections refetches connections, nodes and storages but not the placement config", async () => {
    await act(async () => { await seen.cache.invalidateConnections(); });

    expect(mocks.listConnections).toHaveBeenCalledTimes(2);
    expect(mocks.getNodes).toHaveBeenCalledTimes(2);
    expect(mocks.getStorages).toHaveBeenCalledTimes(2);
    expect(mocks.getConfig).toHaveBeenCalledTimes(1);
  });
});
