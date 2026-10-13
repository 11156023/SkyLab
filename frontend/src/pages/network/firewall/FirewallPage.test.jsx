// @vitest-environment happy-dom
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import FirewallPage from "./FirewallPage";

const mocks = vi.hoisted(() => ({
  getTopology: vi.fn(),
  deleteConnection: vi.fn(),
  saveLayout: vi.fn(),
  confirm: vi.fn(),
  autoRefresh: null,
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
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: (refresh) => { mocks.autoRefresh = refresh; } }));
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

async function flush() {
  await act(async () => {
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

beforeEach(() => {
  vi.resetAllMocks();
  mocks.autoRefresh = null;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.saveLayout.mockResolvedValue({});
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

    await act(async () => { root.render(<FirewallPage />); });
    await flush();
    expect(shownNodes()).toEqual(["101", "102"]);

    let first;
    let second;
    act(() => { first = mocks.autoRefresh(); });
    act(() => { second = mocks.autoRefresh(); });
    await act(async () => { newer.resolve(topology(101)); await second; });
    await act(async () => { older.resolve(topology(101, 102)); await first; });
    await flush();

    expect(shownNodes()).toEqual(["101"]);
  });

  test("a first-load failure still shows the error when a background refresh overtook it", async () => {
    const firstLoad = deferred();
    const background = deferred();
    mocks.getTopology
      .mockReturnValueOnce(firstLoad.promise)
      .mockReturnValueOnce(background.promise)
      .mockResolvedValueOnce(topology(101));

    await act(async () => { root.render(<FirewallPage />); });
    expect(host.querySelector('[data-testid="loading"]')).toBeTruthy();

    let refresh;
    act(() => { refresh = mocks.autoRefresh(); });
    await act(async () => { background.reject(new Error("gateway down")); await refresh; });
    await flush();

    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(host.textContent).toContain("gateway down");

    // 被超車的首次載入晚回來不能蓋掉；重試照常恢復
    await act(async () => { firstLoad.resolve(topology(102)); });
    await flush();
    expect(host.textContent).toContain("gateway down");
    expect(shownNodes()).toEqual([]);

    await act(async () => { buttonByText("FirewallPage.retry").click(); });
    await flush();
    expect(host.textContent).not.toContain("gateway down");
    expect(shownNodes()).toEqual(["101"]);
  });

  test("a first-load failure with no overlap still shows the error", async () => {
    mocks.getTopology.mockRejectedValueOnce(new Error("boom"));

    await act(async () => { root.render(<FirewallPage />); });
    await flush();

    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(host.textContent).toContain("boom");
  });
});
