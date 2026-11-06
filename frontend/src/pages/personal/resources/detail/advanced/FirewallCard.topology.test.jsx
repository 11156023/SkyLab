// @vitest-environment happy-dom
/*
 * 資源詳情的防火牆卡：迷你拓撲上拉線開連線表單、點線刪線，以及權限。
 * 連線要同時寫兩端的規則——這台不能管（學生的課堂機）、或另一端是別人的機器時，
 * 不能刪也不能拉，而且要講原因，不是把按鈕藏起來就算了。
 * 畫布本身（ReactFlow）換成一個把回呼露出來的替身，這裡只驗卡片的邏輯。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const fw = vi.hoisted(() => ({
  getVmRules: vi.fn(),
  getVmOptions: vi.fn(),
  getVmTopology: vi.fn(),
  deleteVmRule: vi.fn(),
  updateVmRule: vi.fn(),
  deleteConnection: vi.fn(),
}));
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
const confirmState = vi.hoisted(() => ({ next: null, calls: [] }));
const canvas = vi.hoisted(() => ({ props: null }));
const dialog = vi.hoisted(() => ({ props: null }));

const i18n = vi.hoisted(() => ({ t: (key) => key, i18n: { language: "zh-TW" } }));
vi.mock("react-i18next", async (importOriginal) => ({ ...await importOriginal(), useTranslation: () => i18n }));
vi.mock("../../../../../services/firewall", () => fw);
vi.mock("../../../../../components/ConnectionDialog/ConnectionDialog", () => ({
  default: (props) => { dialog.props = props; return <div data-testid="connection-dialog" />; },
}));
vi.mock("../../../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../../../hooks/useToast", () => ({ useToast: () => toast }));
vi.mock("../../../../../hooks/useDialogPresence", () => ({
  default: (value) => ({ open: Boolean(value), item: value || null, closing: false }),
}));
vi.mock("../../../../../components/ConfirmDialog/ConfirmProvider", () => ({
  useConfirm: () => (options) => { confirmState.calls.push(options); return confirmState.next; },
}));
vi.mock("./MiniTopology", () => ({
  default: (props) => { canvas.props = props; return <div data-testid="mini-topology">{props.children}</div>; },
}));

import { createTestQueryClient, withQueryClient } from "../../../../../queries/testing";
import FirewallCard from "./FirewallCard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

/* 這台是 101；201 是自己能管的機器，202 是別人的機器 */
const TOPOLOGY = {
  nodes: [
    { vmid: 201, name: "db-01", node_type: "vm", can_manage: true },
    { vmid: 202, name: "others-vm", node_type: "vm", can_manage: false },
    { vmid: 101, name: "web-01", node_type: "vm", can_manage: true },
    { vmid: null, name: "Internet", node_type: "gateway" },
  ],
  edges: [
    { source_vmid: 101, target_vmid: 201, direction: "one_way", ports: [{ port: 3306, protocol: "tcp" }] },
    { source_vmid: 202, target_vmid: 101, direction: "one_way", ports: [{ port: 22, protocol: "tcp" }] },
  ],
};
const EDGE_TO_OWN = "edge-101-201";
const EDGE_FROM_OTHERS = "edge-202-101";

let host;
let root;

beforeEach(() => {
  Object.values(fw).forEach((fn) => fn.mockReset());
  toast.error.mockReset();
  toast.success.mockReset();
  confirmState.next = Promise.resolve(true);
  confirmState.calls = [];
  canvas.props = null;
  dialog.props = null;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  fw.getVmOptions.mockResolvedValue({ enable: 1, policy_in: "DROP", policy_out: "ACCEPT" });
  fw.getVmRules.mockResolvedValue([]);
  fw.getVmTopology.mockResolvedValue(TOPOLOGY);
  fw.deleteConnection.mockResolvedValue({ message: "ok" });
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

async function renderCard({ canManage = true, onChanged } = {}) {
  await act(async () => {
    root.render(withQueryClient(<FirewallCard vmid={101} canManage={canManage} onChanged={onChanged} />, createTestQueryClient()));
  });
  await flush();
}
const selectEdge = (id) => act(async () => { canvas.props.onSelectEdge(null, id); });
const deleteButton = () => [...host.querySelectorAll("button")].find((b) => b.textContent.includes("ConnectionPanel.delete"));

test("clicking a connection to a machine I manage offers delete, which removes it after confirming", async () => {
  const onChanged = vi.fn();
  await renderCard({ onChanged });
  expect(host.textContent).toContain("FirewallCard.topologyHint");

  await selectEdge(EDGE_TO_OWN);
  expect(canvas.props.selectedEdgeId).toBe(EDGE_TO_OWN);
  await act(async () => { deleteButton().click(); });
  await flush();

  /* 確認框講清楚是哪一條、開了什麼 */
  expect(confirmState.calls[0].danger).toBe(true);
  expect(confirmState.calls[0].message).toContain("web-01 → db-01");
  expect(confirmState.calls[0].message).toContain("3306");
  expect(fw.deleteConnection).toHaveBeenCalledWith({ source_vmid: 101, target_vmid: 201, ports: null });
  expect(toast.success).toHaveBeenCalledWith("FirewallCard.connectionDeleted");
  /* 對外網址可能跟著變，通知上層重載資源；面板關掉 */
  expect(onChanged).toHaveBeenCalled();
  expect(canvas.props.selectedEdgeId).toBeNull();
});

test("a connection whose other end belongs to someone else can only be viewed, with the reason shown", async () => {
  await renderCard();

  await selectEdge(EDGE_FROM_OTHERS);

  expect(deleteButton()).toBeUndefined();
  expect(host.textContent).toContain("FirewallCard.edgeLockedPeer");
});

test("without management rights on this machine the whole map is view-only", async () => {
  await renderCard({ canManage: false });

  expect(canvas.props.canManage).toBe(false);
  expect(host.textContent).toContain("FirewallCard.topologyReadOnlyHint");
  await selectEdge(EDGE_TO_OWN);
  expect(deleteButton()).toBeUndefined();

  /* 仍然拉了線（替身直接呼叫）：不開表單，說明原因 */
  await act(async () => { canvas.props.onBlocked(TOPOLOGY.nodes[2]); });
  expect(toast.error).toHaveBeenCalledWith("FirewallCard.topologyReadOnlyHint");
  expect(dialog.props).toBeNull();
});

test("dragging a line opens the shared connection form with both ends filled in the direction drawn", async () => {
  await renderCard();

  await act(async () => { canvas.props.onConnectRequest({ source: "internet", target: "101" }); });

  expect(host.querySelector('[data-testid="connection-dialog"]')).not.toBeNull();
  expect(dialog.props.initialSource).toBe("internet");
  expect(dialog.props.initialTarget).toBe("101");
  /* 不鎖定這台（否則「別台 → 這台」會被翻成「這台 → 別台」）；可選的只有圖上能管的機器 */
  expect(dialog.props.fixedVmid).toBeUndefined();
  expect(dialog.props.nodes.map((node) => node.vmid)).toEqual([201, 101]);
});

test("dragging to someone else's machine is refused with the reason instead of opening the form", async () => {
  await renderCard();

  await act(async () => { canvas.props.onBlocked(TOPOLOGY.nodes[1]); });

  expect(toast.error).toHaveBeenCalledWith("FirewallCard.blockedPeer");
  expect(dialog.props).toBeNull();
});
