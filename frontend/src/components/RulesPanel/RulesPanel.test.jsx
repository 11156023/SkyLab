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
vi.mock("../ConnectionDialog/ConnectionDialog", () => ({ default: () => null }));
vi.mock("../LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../hooks/useToast", () => ({ useToast: () => ({ error: vi.fn(), success: vi.fn() }) }));
vi.mock("../ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => async () => true }));
vi.mock("../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { role: "admin" } }) }));

import RulesPanel from "./RulesPanel";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

let host;
let root;

beforeEach(() => {
  Object.values(fw).forEach((fn) => fn.mockReset());
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

const renderPanel = (node) =>
  act(async () =>
    root.render(
      <MemoryRouter>
        <RulesPanel node={node} onClose={() => {}} />
      </MemoryRouter>,
    ),
  );

test("a late response for the previously selected VM does not overwrite the current VM's rules", async () => {
  const pending = { 1: deferred(), 2: deferred() };
  fw.getVmRules.mockImplementation((vmid) => pending[vmid].promise);
  fw.getVmOptions.mockResolvedValue({ enable: 1 });
  fw.deleteVmRule.mockResolvedValue({});

  await renderPanel({ vmid: 1, name: "A" });
  await renderPanel({ vmid: 2, name: "B" });

  await act(async () => pending[2].resolve([{ pos: 7, type: "in", action: "ACCEPT", comment: "rule-of-B" }]));
  await act(async () => pending[1].resolve([{ pos: 3, type: "in", action: "DROP", comment: "rule-of-A" }]));

  expect(host.textContent).toContain("rule-of-B");
  expect(host.textContent).not.toContain("rule-of-A");
  expect(document.querySelector("[data-testid='loading']")).toBeNull();

  fw.getVmRules.mockResolvedValue([]);
  await act(async () => document.querySelector("[title='RulesPanel.deleteRule']").click());
  expect(fw.deleteVmRule).toHaveBeenCalledWith(2, 7);
});
