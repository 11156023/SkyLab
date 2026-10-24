// @vitest-environment happy-dom
/*
 * 停用／刪除規則是用位置（pos）指定的。清單在背景重抓（打開時先顯示快取、連線變更後、切回視窗）期間
 * 畫面上的位置可能已經位移：重抓中要停用這兩個操作；確認刪除的視窗開著時清單變了，按確認也不可以照舊位置刪。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const fw = vi.hoisted(() => ({
  getVmRules: vi.fn(),
  getVmOptions: vi.fn(),
  deleteVmRule: vi.fn(),
  updateVmRule: vi.fn(),
}));
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
const confirmState = vi.hoisted(() => ({ next: null }));

const i18n = vi.hoisted(() => ({ t: (key) => key }));
vi.mock("react-i18next", () => ({ useTranslation: () => i18n }));
vi.mock("../../services/firewall", () => fw);
vi.mock("../ConnectionDialog/ConnectionDialog", () => ({ default: () => null }));
vi.mock("../LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../hooks/useToast", () => ({ useToast: () => toast }));
vi.mock("../ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => () => confirmState.next }));
vi.mock("../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { role: "admin" } }) }));

import { firewallKeys } from "../../queries/firewall";
import { createTestQueryClient, withQueryClient } from "../../queries/testing";
import RulesPanel from "./RulesPanel";

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

const SSH = { pos: 0, type: "in", action: "ACCEPT", proto: "tcp", dport: "22", comment: "ssh" };
const WEB = { pos: 1, type: "in", action: "ACCEPT", proto: "tcp", dport: "80", comment: "web" };
/* 新連線的受管規則插在最前面，原本的規則各往後移一格 */
const MANAGED = { pos: 0, type: "in", action: "ACCEPT", source: "10.20.0.12", comment: "SkyLab:conn", is_managed: true };
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
  fw.getVmOptions.mockResolvedValue({ enable: 1 });
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

const renderPanel = () => act(async () => root.render(withQueryClient(
  <MemoryRouter>
    <RulesPanel node={{ vmid: 101, name: "web-01" }} onClose={() => {}} />
  </MemoryRouter>,
  client,
)));

const deleteButtons = () => [...host.querySelectorAll('button[title="RulesPanel.deleteRule"]')];
const switches = () => [...host.querySelectorAll('[role="switch"]')];

test("while the rule list is being refetched the position-based toggle and delete are disabled", async () => {
  fw.getVmRules.mockResolvedValueOnce([SSH, WEB]);
  await renderPanel();
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
  /* 新清單回來才能操作，受管規則照舊上鎖 */
  expect(deleteButtons()).toHaveLength(2);
  expect(deleteButtons().every((b) => !b.disabled)).toBe(true);
});

test("a rule list that changed while the delete confirmation was open does not delete by the old position", async () => {
  fw.getVmRules.mockResolvedValueOnce([SSH, WEB]);
  await renderPanel();
  await flush();

  const answer = deferred();
  confirmState.next = answer.promise;
  /* 要刪 #1（web），確認視窗開著 */
  await act(async () => { deleteButtons()[1].click(); });

  /* 這時清單重抓回來：#1 變成 ssh */
  fw.getVmRules.mockResolvedValueOnce(SHIFTED);
  await act(async () => { await client.refetchQueries({ queryKey: firewallKeys.vmRules(101) }); });
  await flush();

  await act(async () => { answer.resolve(true); });
  await flush();

  expect(fw.deleteVmRule).not.toHaveBeenCalled();
  expect(toast.error).toHaveBeenCalledWith("RulesPanel.rulesChanged");
});

test("the delete still goes through when the list did not change", async () => {
  fw.getVmRules.mockResolvedValue([SSH, WEB]);
  fw.deleteVmRule.mockResolvedValue({});
  await renderPanel();
  await flush();

  await act(async () => { deleteButtons()[1].click(); });
  await flush();

  expect(fw.deleteVmRule).toHaveBeenCalledWith(101, 1);
  expect(toast.error).not.toHaveBeenCalled();
});
