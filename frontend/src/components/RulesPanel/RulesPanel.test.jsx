// @vitest-environment happy-dom
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

// t 要是穩定參照（真的 i18next 也是），否則 load 的 useCallback 每次 render 都會重建
const i18n = vi.hoisted(() => ({ t: (key) => key }));
vi.mock("react-i18next", () => ({ useTranslation: () => i18n }));
vi.mock("../../services/firewall", () => fw);
const dialogProps = vi.hoisted(() => ({ current: null }));
vi.mock("../ConnectionDialog/ConnectionDialog", () => ({
  default: (props) => {
    dialogProps.current = props;
    return null;
  },
}));
vi.mock("../LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../hooks/useToast", () => ({ useToast: () => ({ error: vi.fn(), success: vi.fn() }) }));
vi.mock("../ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => async () => true }));
vi.mock("../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { role: "admin" } }) }));

import { createTestQueryClient, withQueryClient } from "../../queries/testing";
import RulesPanel from "./RulesPanel";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/* TanStack Query 的通知排在 setTimeout(0)：先等一個 timer tick 再等 microtask */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

let host;
let root;
let client;

beforeEach(() => {
  Object.values(fw).forEach((fn) => fn.mockReset());
  client = createTestQueryClient();
  dialogProps.current = null;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

const renderPanel = (node, props = {}) =>
  act(async () =>
    root.render(withQueryClient(
      <MemoryRouter>
        <RulesPanel node={node} onClose={() => {}} {...props} />
      </MemoryRouter>,
      client,
    )),
  );

test("a late response for the previously selected VM does not overwrite the current VM's rules", async () => {
  const pending = { 1: deferred(), 2: deferred() };
  fw.getVmRules.mockImplementation((vmid) => pending[vmid].promise);
  fw.getVmOptions.mockResolvedValue({ enable: 1 });
  fw.deleteVmRule.mockResolvedValue({});

  await renderPanel({ vmid: 1, name: "A" });
  await renderPanel({ vmid: 2, name: "B" });

  await act(async () => pending[2].resolve([{ pos: 7, type: "in", action: "ACCEPT", comment: "rule-of-B" }]));
  await flush();
  await act(async () => pending[1].resolve([{ pos: 3, type: "in", action: "DROP", comment: "rule-of-A" }]));
  await flush();

  expect(host.textContent).toContain("rule-of-B");
  expect(host.textContent).not.toContain("rule-of-A");
  expect(document.querySelector("[data-testid='loading']")).toBeNull();

  fw.getVmRules.mockResolvedValue([]);
  await act(async () => document.querySelector("[title='RulesPanel.deleteRule']").click());
  await flush();
  expect(fw.deleteVmRule).toHaveBeenCalledWith(2, 7);
  // 刪除後重抓的是目前這台的規則，清單跟著更新
  expect(fw.getVmRules).toHaveBeenLastCalledWith(2);
  expect(host.textContent).toContain("RulesPanel.noRules");
});

test("a failed first load shows the error; a failed reload after a write keeps the list", async () => {
  fw.getVmRules.mockRejectedValueOnce(new Error("pve down"));
  fw.getVmOptions.mockResolvedValue({ enable: 1 });
  await renderPanel({ vmid: 5, name: "E" });
  await flush();
  expect(host.textContent).toContain("pve down");

  fw.getVmRules.mockResolvedValue([{ pos: 1, type: "in", action: "ACCEPT", enable: 1, comment: "keep-me" }]);
  await act(async () => client.invalidateQueries());
  await flush();
  expect(host.textContent).toContain("keep-me");

  fw.updateVmRule.mockResolvedValue({});
  fw.getVmRules.mockRejectedValueOnce(new Error("reload failed"));
  await act(async () => document.querySelector("[aria-label='RulesPanel.enableSwitch']").click());
  await flush();
  expect(fw.updateVmRule).toHaveBeenCalledWith(5, 1, { enable: 0 });
  expect(host.textContent).toContain("keep-me");
  expect(host.textContent).not.toContain("reload failed");
});

test("adding a connection reloads this VM and asks the page to refresh the topology", async () => {
  fw.getVmRules.mockResolvedValue([]);
  fw.getVmOptions.mockResolvedValue({ enable: 1 });
  const onChanged = vi.fn();
  await renderPanel({ vmid: 3, name: "C" }, { onChanged });
  await flush();
  expect(fw.getVmRules).toHaveBeenCalledTimes(1);

  const addRule = () => act(async () => [...host.querySelectorAll("button")]
    .find((button) => button.textContent.includes("RulesPanel.addRule")).click());

  // 自訂規則不影響拓撲邊：只重抓這台，不要求拓撲刷新
  await addRule();
  await act(async () => dialogProps.current.onDone({ kind: "rule" }));
  await flush();
  expect(fw.getVmRules).toHaveBeenCalledTimes(2);
  expect(fw.getVmOptions).toHaveBeenCalledTimes(2);
  expect(onChanged).not.toHaveBeenCalled();

  // 建了連線：重抓這台，並照常呼叫 onChanged 讓防火牆頁重抓拓撲
  await addRule();
  await act(async () => dialogProps.current.onDone({ kind: "edge" }));
  await flush();
  expect(fw.getVmRules).toHaveBeenCalledTimes(3);
  expect(fw.getVmRules).toHaveBeenLastCalledWith(3);
  expect(onChanged).toHaveBeenCalledTimes(1);
});
