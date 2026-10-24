// @vitest-environment happy-dom
/*
 * 資源詳情的防火牆卡與規則面板一樣以位置（pos）停用／刪除規則：清單重抓中要停用這兩個操作，
 * 確認刪除的視窗開著時清單變了，按確認也不可以照舊位置刪。
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

test("while the rule list is being refetched the position-based toggle and delete are disabled", async () => {
  fw.getVmRules.mockResolvedValueOnce([SSH, WEB]);
  await renderCard();
  await flush();
  expect(deleteButtons()).toHaveLength(2);
  expect(deleteButtons().every((b) => !b.disabled)).toBe(true);

  const refetch = deferred();
  fw.getVmRules.mockReturnValueOnce(refetch.promise);
  await act(async () => { client.invalidateQueries({ queryKey: firewallKeys.vmRules(101) }); });
  await flush();
  expect(deleteButtons().every((b) => b.disabled)).toBe(true);
  expect(switches().every((s) => s.disabled)).toBe(true);

  await act(async () => { refetch.resolve(SHIFTED); });
  await flush();
  expect(deleteButtons()).toHaveLength(2);
  expect(deleteButtons().every((b) => !b.disabled)).toBe(true);
});

test("a rule list that changed while the delete confirmation was open does not delete by the old position", async () => {
  fw.getVmRules.mockResolvedValueOnce([SSH, WEB]);
  await renderCard();
  await flush();

  const answer = deferred();
  confirmState.next = answer.promise;
  await act(async () => { deleteButtons()[1].click(); });

  fw.getVmRules.mockResolvedValueOnce(SHIFTED);
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
