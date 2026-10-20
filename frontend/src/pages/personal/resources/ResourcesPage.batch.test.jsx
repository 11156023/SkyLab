// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import ResourcesPage from "./ResourcesPage";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  batchActionInChunks: vi.fn(),
  listMySessions: vi.fn(),
  fetchPendingResources: vi.fn(),
  confirm: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
  t: (key, opts) => (opts && typeof opts.count === "number" ? `${key}:${opts.count}` : key),
}));

vi.mock("../../../services/resources", () => ({
  ResourcesService: { list: mocks.list, batchActionInChunks: mocks.batchActionInChunks },
}));
vi.mock("../../../services/quickPractice", () => ({ QuickPracticeService: { listMySessions: mocks.listMySessions } }));
vi.mock("../../../services/pendingResources", () => ({
  PENDING_IDLE_POLL_INTERVAL: 60_000,
  PENDING_POLL_INTERVAL: 5_000,
  cancelVmRequest: vi.fn(),
  fetchPendingResources: mocks.fetchPendingResources,
  pendingSignature: () => "",
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "student" } }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/QuotaUsageBar/QuotaUsageBar", () => ({ default: () => null }));
vi.mock("../../../components/MachineKindBadge/MachineKindBadge", () => ({ default: () => null }));

let root;
let host;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

const machines = [
  { vmid: 101, name: "web-01", type: "qemu", status: "running", request_id: 1 },
  { vmid: 102, name: "db-01", type: "lxc", status: "stopped", request_id: 2 },
  /* 共享給我的：可以開關機、不能刪 */
  { vmid: 103, name: "shared-01", type: "qemu", status: "stopped", request_id: 3, can_delete: false, machine_kind: "shared" },
];

function rowOf(name) {
  return [...host.querySelectorAll("tbody tr")].find((tr) => tr.textContent.includes(name));
}

function checkboxOf(name) {
  return rowOf(name).querySelector('input[type="checkbox"]');
}

function buttonByText(text) {
  return [...host.querySelectorAll("button")].find((button) => button.textContent.includes(text));
}

async function renderWith(list) {
  mocks.list.mockResolvedValue(list);
  await act(async () => { root.render(<ResourcesPage />); });
  await flush();
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.listMySessions.mockResolvedValue([]);
  mocks.fetchPendingResources.mockResolvedValue([]);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("ResourcesPage batch actions", () => {
  test("每台自己的機器都有勾選框，勾了之後工具列換成批量列", async () => {
    await renderWith(machines);
    expect(checkboxOf("web-01")).toBeTruthy();
    expect(host.querySelector('input[aria-label="ResourcesPage.selectAllAria"]')).toBeTruthy();
    expect(buttonByText("ResourceMgmtPage.delete")).toBeUndefined();

    await act(async () => { checkboxOf("web-01").click(); });
    await flush();
    expect(host.textContent).toContain("ResourceMgmtPage.selectedCount:1");
    expect(buttonByText("ResourceMgmtPage.delete")).toBeTruthy();
  });

  test("批量刪除先確認，再把勾選的機器一起送到批量 API；不能刪的共享機會被略過", async () => {
    await renderWith(machines);
    const selectAll = host.querySelector('input[aria-label="ResourcesPage.selectAllAria"]');
    await act(async () => { selectAll.click(); });
    await flush();
    expect(host.textContent).toContain("ResourceMgmtPage.selectedCount:3");

    mocks.confirm.mockResolvedValue(true);
    mocks.batchActionInChunks.mockResolvedValue({
      total: 2, succeeded: 2, failed: 0, results: [{ vmid: 101, success: true }, { vmid: 102, success: true }],
    });
    await act(async () => { buttonByText("ResourceMgmtPage.delete").click(); });
    await flush();

    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    expect(mocks.confirm.mock.calls[0][0].danger).toBe(true);
    expect(mocks.batchActionInChunks).toHaveBeenCalledWith([101, 102], "delete", expect.any(Object));
    /* 做完靜默重抓清單 */
    expect(mocks.list).toHaveBeenCalledTimes(2);
  });

  test("取消確認就不送出", async () => {
    await renderWith(machines);
    await act(async () => { checkboxOf("db-01").click(); });
    await flush();
    mocks.confirm.mockResolvedValue(false);
    await act(async () => { buttonByText("ResourceMgmtPage.delete").click(); });
    await flush();
    expect(mocks.batchActionInChunks).not.toHaveBeenCalled();
  });

  test("勾選狀態下點整列是勾選；取消全部勾選後批量列消失", async () => {
    await renderWith(machines);
    await act(async () => { checkboxOf("web-01").click(); });
    await flush();
    await act(async () => { rowOf("db-01").click(); });
    await flush();
    expect(host.textContent).toContain("ResourceMgmtPage.selectedCount:2");

    const clear = host.querySelector('button[aria-label="DataToolbar.clearSelection"]');
    await act(async () => { clear.click(); });
    await flush();
    expect(buttonByText("ResourceMgmtPage.delete")).toBeUndefined();
    expect(host.querySelector('input[aria-label="ResourcesPage.searchPlaceholder"]')).toBeTruthy();
  });

  test("搜尋篩掉的機器自動取消勾選，全選只勾得到看得見的", async () => {
    await renderWith(machines);
    const search = host.querySelector('input[aria-label="ResourcesPage.searchPlaceholder"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      setter.call(search, "db");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await flush();
    expect(rowOf("web-01")).toBeUndefined();
    expect(rowOf("db-01")).toBeTruthy();

    const selectAll = host.querySelector('input[aria-label="ResourcesPage.selectAllAria"]');
    await act(async () => { selectAll.click(); });
    await flush();
    expect(host.textContent).toContain("ResourceMgmtPage.selectedCount:1");
  });
});
