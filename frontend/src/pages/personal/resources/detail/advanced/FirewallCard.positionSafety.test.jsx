// @vitest-environment happy-dom
/*
 * 資源詳情的防火牆卡與規則面板一樣以位置（pos）停用／刪除規則。卡片每 30 秒背景刷新，刷新失敗時沿用
 * 畫面上的舊清單：送出前一律向後端抓最新清單比對，位置變了（含這張卡從沒看到的變動）或抓不到都不送。
 * 背景刷新期間不停用開關，不然每 30 秒閃一次停用樣式。
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
}));
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
const confirmState = vi.hoisted(() => ({ next: null }));

const i18n = vi.hoisted(() => ({ t: (key) => key, i18n: { language: "zh-TW" } }));
vi.mock("react-i18next", async (importOriginal) => ({ ...await importOriginal(), useTranslation: () => i18n }));
vi.mock("../../../../../services/firewall", () => fw);
vi.mock("../../../../../components/ConnectionDialog/ConnectionDialog", () => ({ default: () => null }));
vi.mock("../../../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../../../hooks/useToast", () => ({ useToast: () => toast }));
vi.mock("../../../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => () => confirmState.next }));

import { firewallKeys } from "../../../../../queries/firewall";
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
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

const SSH = { pos: 0, type: "in", action: "ACCEPT", proto: "tcp", dport: "22", comment: "ssh", enable: 1 };
const WEB = { pos: 1, type: "in", action: "ACCEPT", proto: "tcp", dport: "80", comment: "web", enable: 1 };
const MANAGED = { pos: 0, type: "in", action: "ACCEPT", source: "10.20.0.12", comment: "SkyLab:conn", is_managed: true, enable: 1 };
const SHIFTED = [MANAGED, { ...SSH, pos: 1 }, { ...WEB, pos: 2 }];

let host;
let root;
let client;

beforeEach(() => {
  Object.values(fw).forEach((fn) => fn.mockReset());
  toast.error.mockReset();
  toast.success.mockReset();
  confirmState.next = Promise.resolve(true);
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  fw.getVmOptions.mockResolvedValue({ enable: 1, policy_in: "DROP", policy_out: "ACCEPT" });
  fw.getVmTopology.mockResolvedValue({ nodes: [{ vmid: 101, name: "web-01" }], edges: [] });
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

const renderCard = () => act(async () => root.render(withQueryClient(<FirewallCard vmid={101} canManage />, client)));
const deleteButtons = () => [...host.querySelectorAll('button[title="FirewallCard.deleteRule"]')];
const switches = () => [...host.querySelectorAll('[role="switch"]')];

test("a background refresh does not disable the toggle and delete (no flicker every 30 seconds)", async () => {
  fw.getVmRules.mockResolvedValueOnce([SSH, WEB]);
  await renderCard();
  await flush();
  expect(deleteButtons()).toHaveLength(2);

  const refetch = deferred();
  fw.getVmRules.mockReturnValueOnce(refetch.promise);
  await act(async () => { client.invalidateQueries({ queryKey: firewallKeys.vmRules(101) }); });
  await flush();
  expect(deleteButtons().every((b) => !b.disabled)).toBe(true);
  expect(switches().every((s) => !s.disabled)).toBe(true);

  await act(async () => { refetch.resolve([SSH, WEB]); });
  await flush();
});

test("a shift the card never saw (its refresh did not run or failed) still blocks the delete", async () => {
  fw.getVmRules.mockResolvedValueOnce([SSH, WEB]);
  await renderCard();
  await flush();

  /* 後端已經被別人插了一條受管規則，卡片畫面上還是舊的兩條 */
  fw.getVmRules.mockResolvedValue(SHIFTED);
  await act(async () => { deleteButtons()[1].click(); });
  await flush();

  expect(fw.deleteVmRule).not.toHaveBeenCalled();
  expect(toast.error).toHaveBeenCalledWith("FirewallCard.rulesChanged");
  /* 抓到的最新清單直接換上畫面，不用等下一次刷新 */
  expect(host.textContent).toContain("FirewallCard.managedByService");
});

test("a shift the card never saw also blocks the toggle", async () => {
  fw.getVmRules.mockResolvedValueOnce([SSH, WEB]);
  await renderCard();
  await flush();

  fw.getVmRules.mockResolvedValue(SHIFTED);
  await act(async () => { switches()[1].click(); });
  await flush();

  expect(fw.updateVmRule).not.toHaveBeenCalled();
  expect(toast.error).toHaveBeenCalledWith("FirewallCard.rulesChanged");
});

test("when the latest list cannot be fetched nothing is sent and the card is not marked as failed", async () => {
  fw.getVmRules.mockResolvedValueOnce([SSH, WEB]);
  await renderCard();
  await flush();

  fw.getVmRules.mockRejectedValueOnce(new Error("PVE 連線失敗"));
  await act(async () => { deleteButtons()[1].click(); });
  await flush();

  expect(fw.deleteVmRule).not.toHaveBeenCalled();
  expect(toast.error).toHaveBeenCalledTimes(1);
  expect(toast.error).toHaveBeenCalledWith("FirewallCard.verifyFailed");
  /* 只是送出前的確認沒抓到，不另外再跳「更新失敗」 */
  expect(host.textContent).not.toContain("FirewallCard.refreshFailed");
  expect(deleteButtons().every((b) => !b.disabled)).toBe(true);
});

test("a rule list that changed while the delete confirmation was open does not delete by the old position", async () => {
  fw.getVmRules.mockResolvedValueOnce([SSH, WEB]);
  await renderCard();
  await flush();

  const answer = deferred();
  confirmState.next = answer.promise;
  await act(async () => { deleteButtons()[1].click(); });

  fw.getVmRules.mockResolvedValue(SHIFTED);
  await act(async () => { await client.refetchQueries({ queryKey: firewallKeys.vmRules(101) }); });
  await flush();

  await act(async () => { answer.resolve(true); });
  await flush();

  expect(fw.deleteVmRule).not.toHaveBeenCalled();
  expect(toast.error).toHaveBeenCalledWith("FirewallCard.rulesChanged");
});

test("the delete still goes through when the list did not change", async () => {
  fw.getVmRules.mockResolvedValue([SSH, WEB]);
  fw.deleteVmRule.mockResolvedValue({});
  await renderCard();
  await flush();

  await act(async () => { deleteButtons()[1].click(); });
  await flush();

  expect(fw.deleteVmRule).toHaveBeenCalledWith(101, 1);
  expect(toast.error).not.toHaveBeenCalled();
});

test("the toggle still goes through when the list did not change", async () => {
  fw.getVmRules.mockResolvedValue([SSH, WEB]);
  fw.updateVmRule.mockResolvedValue({});
  await renderCard();
  await flush();

  await act(async () => { switches()[1].click(); });
  await flush();

  expect(fw.updateVmRule).toHaveBeenCalledWith(101, 1, { enable: 0 });
  expect(toast.error).not.toHaveBeenCalled();
});
