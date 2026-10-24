// @vitest-environment happy-dom
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import FirewallPage from "./FirewallPage";
import { firewallKeys } from "../../../queries/firewall";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  getTopology: vi.fn(),
  deleteConnection: vi.fn(),
  saveLayout: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@xyflow/react", () => {
  const useFlowState = (initial) => {
    const [value, setValue] = useState(initial);
    return [value, setValue, () => {}];
  };
  return {
    ReactFlow: ({ nodes = [], children }) => (
      <div data-testid="flow">
        {nodes.map((node) => <span key={node.id} data-node={node.id}>{node.data?.name}</span>)}
        {children}
      </div>
    ),
    useNodesState: useFlowState,
    useEdgesState: useFlowState,
    Background: () => null,
    BackgroundVariant: { Dots: "dots" },
    Controls: () => null,
    MiniMap: () => null,
    Panel: () => null,
  };
});
vi.mock("@xyflow/react/dist/style.css", () => ({}));
vi.mock("../../../services/firewall", () => ({
  getTopology: mocks.getTopology,
  deleteConnection: mocks.deleteConnection,
  saveLayout: mocks.saveLayout,
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../contexts/ThemeContext", () => ({ useTheme: () => ({ theme: "light" }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/EmptyState/EmptyState", () => ({ default: () => null }));
vi.mock("../../../components/RulesPanel/RulesPanel", () => ({ default: () => null }));
vi.mock("../../../components/ConnectionDialog/ConnectionDialog", () => ({ default: () => null }));
vi.mock("./ConnectionDetailPanel", () => ({ default: () => null }));
vi.mock("./nodes/GatewayNode", () => ({ default: () => null }));
vi.mock("./nodes/VMNode", () => ({ default: () => null }));
vi.mock("./nodes/GroupNode", () => ({ default: () => null }));
vi.mock("./edges/ConnectionEdge", () => ({ default: () => null }));

let root;
let host;
let client;

/* TanStack Query 以 macrotask 批次通知，先等一個 timer tick */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const vmNode = (vmid) => ({
  vmid, name: `vm-${vmid}`, node_type: "vm", position_x: 0, position_y: vmid, ip_address: `10.0.0.${vmid % 250}`,
});
const topology = (...vmids) => ({ nodes: vmids.map(vmNode), edges: [] });

function shownNodes() {
  return [...host.querySelectorAll("[data-node]")].map((el) => el.dataset.node);
}

function buttonByText(text) {
  return [...host.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

/** 背景自動刷新（原 useAutoRefresh 的那一輪）：對同一把 key 重抓 */
function backgroundRefresh() {
  return client.refetchQueries({ queryKey: firewallKeys.topology });
}

async function renderPage() {
  await act(async () => { root.render(withQueryClient(<FirewallPage />, client)); });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.saveLayout.mockResolvedValue({});
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("FirewallPage topology loading", () => {
  test("an older topology response arriving after a newer one is ignored", async () => {
    const older = deferred();
    const newer = deferred();
    mocks.getTopology
      .mockResolvedValueOnce(topology(101, 102))
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);

    await renderPage();
    await flush();
    expect(shownNodes()).toEqual(["101", "102"]);

    /* 第二輪重抓會取消還在路上的第一輪（cancelRefetch），舊回應晚到也不會蓋回去 */
    let second;
    act(() => { backgroundRefresh(); });
    act(() => { second = backgroundRefresh(); });
    await act(async () => { newer.resolve(topology(101)); await second; });
    await act(async () => { older.resolve(topology(101, 102)); });
    await flush();

    expect(mocks.getTopology).toHaveBeenCalledTimes(3);
    expect(shownNodes()).toEqual(["101"]);
  });

  test("a background refresh during the first load joins it, so a first-load failure still shows the error", async () => {
    const firstLoad = deferred();
    mocks.getTopology
      .mockReturnValueOnce(firstLoad.promise)
      .mockResolvedValueOnce(topology(101));

    await renderPage();
    expect(host.querySelector('[data-testid="loading"]')).toBeTruthy();

    /* 還沒有任何拓撲時背景重抓不另開請求，併入首次載入 */
    let refresh;
    act(() => { refresh = backgroundRefresh(); });
    await act(async () => { firstLoad.reject(new Error("gateway down")); await refresh; });
    await flush();

    expect(mocks.getTopology).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(host.textContent).toContain("gateway down");
    expect(shownNodes()).toEqual([]);

    await act(async () => { buttonByText("FirewallPage.retry").click(); });
    await flush();
    expect(host.textContent).not.toContain("gateway down");
    expect(shownNodes()).toEqual(["101"]);
  });

  test("a first-load failure with no overlap still shows the error", async () => {
    mocks.getTopology.mockRejectedValueOnce(new Error("boom"));

    await renderPage();
    await flush();

    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(host.textContent).toContain("boom");
  });

  test("a background refresh failure keeps the graph that is already shown", async () => {
    mocks.getTopology
      .mockResolvedValueOnce(topology(101))
      .mockRejectedValueOnce(new Error("gateway down"));

    await renderPage();
    await flush();
    expect(shownNodes()).toEqual(["101"]);

    await act(async () => { await backgroundRefresh(); });
    await flush();

    expect(shownNodes()).toEqual(["101"]);
    expect(host.textContent).not.toContain("gateway down");
  });
});
