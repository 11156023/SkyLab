// @vitest-environment happy-dom
/*
 * 資源詳情的防火牆卡背景刷新：規則、選項、迷你拓撲每 30 秒重抓、切回視窗也重抓。
 * 重抓失敗時沿用畫面上的資料（不把圖收掉），標題旁標「更新失敗」並說是幾點的資料；
 * 同一串失敗只跳一次 toast，成功過再失敗才再跳。從沒載到的規則表照舊提示，但背景重試期間不閃回載入中。
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

/* 帶 time 參數的字串把時間接在後面，才驗得到「幾點的資料」 */
const i18n = vi.hoisted(() => ({
  t: (key, options) => (options?.time ? `${key}:${options.time}` : key),
  i18n: { language: "zh-TW" },
}));
vi.mock("react-i18next", async (importOriginal) => ({ ...await importOriginal(), useTranslation: () => i18n }));
vi.mock("../../../../../services/firewall", () => fw);
vi.mock("../../../../../components/ConnectionDialog/ConnectionDialog", () => ({ default: () => null }));
vi.mock("../../../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../../../hooks/useToast", () => ({ useToast: () => toast }));
vi.mock("../../../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => () => Promise.resolve(true) }));
vi.mock("./MiniTopology", () => ({ default: () => <div data-testid="mini-topology" /> }));

import { firewallKeys } from "../../../../../queries/firewall";
import { LIST_REFRESH_INTERVAL } from "../../../../../queries/queryClient";
import { createTestQueryClient, withQueryClient } from "../../../../../queries/testing";
import FirewallCard from "./FirewallCard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

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

const SSH = { pos: 0, type: "in", action: "ACCEPT", proto: "tcp", dport: "22", comment: "ssh", enable: 1 };
const WEB = { pos: 1, type: "in", action: "ACCEPT", proto: "tcp", dport: "80", comment: "web", enable: 1 };
const TOPOLOGY = { nodes: [{ vmid: 101, name: "web-01", node_type: "vm", can_manage: true }], edges: [] };

let host;
let root;
let client;

beforeEach(() => {
  Object.values(fw).forEach((fn) => fn.mockReset());
  toast.error.mockReset();
  toast.success.mockReset();
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  fw.getVmOptions.mockResolvedValue({ enable: 1, policy_in: "DROP", policy_out: "ACCEPT" });
  fw.getVmRules.mockResolvedValue([SSH, WEB]);
  fw.getVmTopology.mockResolvedValue(TOPOLOGY);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

const renderCard = async () => {
  await act(async () => root.render(withQueryClient(<FirewallCard vmid={101} canManage />, client)));
  await flush();
};
/* 模擬一次背景刷新（每 30 秒那一次）：三個查詢一起重抓 */
const backgroundRefresh = async () => {
  await act(async () => { await client.refetchQueries({ queryKey: ["firewall", "vm", "101"] }); });
  await flush();
};
const failNextRefresh = () => {
  fw.getVmTopology.mockRejectedValueOnce(new Error("PVE 連線失敗"));
  fw.getVmRules.mockRejectedValueOnce(new Error("PVE 連線失敗"));
  fw.getVmOptions.mockRejectedValueOnce(new Error("PVE 連線失敗"));
};
const failedBadge = () => [...host.querySelectorAll("span")].find((el) => el.textContent.endsWith("FirewallCard.refreshFailed"));
const deleteButtons = () => [...host.querySelectorAll('button[title="FirewallCard.deleteRule"]')];
const refreshFailedToasts = () => toast.error.mock.calls.filter(([message]) => message === "FirewallCard.refreshFailedToast");

test("rules, options and the mini topology refresh every 30 seconds and when the window regains focus", async () => {
  await renderCard();
  for (const key of [firewallKeys.vmRules(101), firewallKeys.vmOptions(101), firewallKeys.vmTopology(101)]) {
    const { options } = client.getQueryCache().find({ queryKey: key }).observers[0];
    expect(options.refetchInterval).toBe(LIST_REFRESH_INTERVAL);
    expect(options.refetchOnWindowFocus).toBe(true);
  }
});

test("a failed background refresh keeps the map and rules, marks the card with the data's time, and toasts once per streak", async () => {
  await renderCard();
  expect(host.querySelector('[data-testid="mini-topology"]')).toBeTruthy();
  expect(failedBadge()).toBeUndefined();

  failNextRefresh();
  await backgroundRefresh();
  /* 圖、規則、選項徽章都還在 */
  expect(host.querySelector('[data-testid="mini-topology"]')).toBeTruthy();
  expect(deleteButtons()).toHaveLength(2);
  expect(host.textContent).toContain("FirewallCard.enabled");
  expect(failedBadge()).toBeTruthy();
  expect(failedBadge().title).toMatch(/^FirewallCard\.refreshFailedTitle:\d{2}:\d{2}$/);
  expect(refreshFailedToasts()).toHaveLength(1);

  /* 下一次又失敗：同一串，不再跳 */
  failNextRefresh();
  await backgroundRefresh();
  expect(failedBadge()).toBeTruthy();
  expect(refreshFailedToasts()).toHaveLength(1);

  /* 恢復：標記消失 */
  await backgroundRefresh();
  expect(failedBadge()).toBeUndefined();

  /* 成功過後再失敗：新的一串，再跳一次 */
  failNextRefresh();
  await backgroundRefresh();
  expect(failedBadge()).toBeTruthy();
  expect(refreshFailedToasts()).toHaveLength(2);
});

test("only one query failing is enough to mark the card", async () => {
  await renderCard();
  fw.getVmTopology.mockRejectedValueOnce(new Error("PVE 連線失敗"));
  await backgroundRefresh();
  expect(failedBadge()).toBeTruthy();
  expect(refreshFailedToasts()).toHaveLength(1);
});

test("rules that never loaded toast once, and background retries do not flash the loading state", async () => {
  fw.getVmRules.mockRejectedValue(new Error("PVE 連線失敗"));
  await renderCard();
  expect(toast.error).toHaveBeenCalledTimes(1);
  expect(toast.error).toHaveBeenCalledWith("PVE 連線失敗");
  expect(host.querySelector('[data-testid="loading"]')).toBeNull();
  expect(host.textContent).toContain("FirewallCard.noRules");

  const retry = deferred();
  fw.getVmRules.mockReturnValueOnce(retry.promise);
  await act(async () => { client.refetchQueries({ queryKey: firewallKeys.vmRules(101) }); });
  await flush();
  expect(host.querySelector('[data-testid="loading"]')).toBeNull();

  await act(async () => { retry.reject(new Error("PVE 連線失敗")); });
  await flush();
  expect(toast.error).toHaveBeenCalledTimes(1);
  /* 從沒載到資料不算「沿用舊資料」，不標更新失敗 */
  expect(failedBadge()).toBeUndefined();
});
