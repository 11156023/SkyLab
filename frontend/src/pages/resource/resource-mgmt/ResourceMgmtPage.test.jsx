// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import ResourceMgmtPage from "./ResourceMgmtPage";
import { resourceKeys } from "../../../queries/resources";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  batchActionInChunks: vi.fn(),
  deleteResource: vi.fn(),
  listAllSessions: vi.fn(),
  buildEnvironmentGroups: vi.fn(),
  navigate: vi.fn(),
  confirm: vi.fn(),
  t: (key, opts) => (opts?.count !== undefined ? `${key}:${opts.count}` : key),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/resources", () => ({
  ResourcesService: {
    listAll: mocks.listAll,
    batchActionInChunks: mocks.batchActionInChunks,
    delete: mocks.deleteResource,
  },
}));
vi.mock("../../../services/quickPractice", () => ({
  QuickPracticeService: { listAllSessions: mocks.listAllSessions },
}));
vi.mock("../../../utils/environmentGroups", () => ({
  buildEnvironmentGroups: mocks.buildEnvironmentGroups,
  groupedResourceKeys: (groups = []) => ({
    requestIds: new Set(groups.flatMap((g) => g.machines.map((m) => String(m.requestId)))),
    vmids: new Set(groups.flatMap((g) => g.machines.map((m) => m.vmid))),
  }),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/MachineKindBadge/MachineKindBadge", () => ({ default: () => null }));
vi.mock("../../../components/PowerMenu/PowerMenu", () => ({
  default: ({ items = [], onControl, onDeleteClick }) => (
    <div>
      {items.map((item) => (
        <button key={item.action} type="button" data-action={item.action} onClick={() => onControl(item.action)}>
          {item.label}
        </button>
      ))}
      {onDeleteClick && <button type="button" data-action="delete-row" onClick={onDeleteClick}>delete-row</button>}
    </div>
  ),
}));

let root;
let host;
/* 每個測試自己的 QueryClient；背景刷新在測試裡用 refreshList() 觸發（原本是 useAutoRefresh 的回呼） */
let client;
const LIST_KEY = resourceKeys.everyone;

function refreshList() {
  return client.refetchQueries({ queryKey: LIST_KEY });
}

async function flush() {
  await act(async () => {
    /* TanStack Query 以 macrotask 批次通知，先等一個 timer tick */
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

function buttonByText(text) {
  return [...host.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const vm = (vmid, extra = {}) => ({
  vmid, name: `vm-${vmid}`, type: "qemu", status: "stopped", request_id: vmid, ...extra,
});

function rowOf(name) {
  return [...host.querySelectorAll("tr")].find((row) => row.textContent.includes(name));
}

async function toggleRow(name) {
  const box = rowOf(name).querySelector('input[type="checkbox"]');
  await act(async () => { box.click(); });
}

async function renderPage() {
  await act(async () => { root.render(withQueryClient(<ResourceMgmtPage />, client)); });
  await flush();
}

beforeEach(() => {
  vi.resetAllMocks();
  client = createTestQueryClient();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.listAllSessions.mockResolvedValue([]);
  mocks.buildEnvironmentGroups.mockReturnValue([]);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("ResourceMgmtPage", () => {
  test("Create resource opens the request form, not just the request list", async () => {
    mocks.listAll.mockResolvedValueOnce([]);
    await renderPage();
    await act(async () => { buttonByText("ResourceMgmtPage.createResource").click(); });
    expect(mocks.navigate).toHaveBeenCalledWith("/my-requests", { state: { create: true } });
  });

  test("Retry after a failed first load clears the error state", async () => {
    mocks.listAll
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce([]);

    await renderPage();
    const retry = buttonByText("Error.retry");
    expect(retry).toBeTruthy();

    await act(async () => { retry.click(); });
    await flush();

    expect(mocks.listAll).toHaveBeenCalledTimes(2);
    expect(buttonByText("Error.retry")).toBeUndefined();
  });

  test("group 'start all' reports partial failure instead of success", async () => {
    mocks.listAll.mockResolvedValue([]);
    mocks.buildEnvironmentGroups.mockReturnValue([{
      id: "course:1",
      kind: "course",
      title: "課程 A",
      timingLabel: "—",
      nodeLabel: "pve1",
      machines: [
        { id: "m1", vmid: 101, requestId: 1, status: "stopped", resource: { vmid: 101, status: "stopped" } },
        { id: "m2", vmid: 102, requestId: 2, status: "stopped", resource: { vmid: 102, status: "stopped" } },
      ],
    }]);
    mocks.batchActionInChunks.mockResolvedValue({ total: 2, succeeded: 1, failed: 1, results: [] });

    await act(async () => { root.render(withQueryClient(<ResourceMgmtPage />, client)); });
    await flush();

    const menuBtn = host.querySelector('button[aria-label="ResourceMgmtPage.groupPowerTitle"]');
    await act(async () => { menuBtn.click(); });
    const startAll = host.querySelector('button[data-action="start"]');
    await act(async () => { startAll.click(); });
    await flush();

    expect(mocks.batchActionInChunks).toHaveBeenCalledWith([101, 102], "start");
    expect(mocks.toast.success).not.toHaveBeenCalled();
    expect(mocks.toast.error).toHaveBeenCalledWith("ResourceMgmtPage.batchPartialFailToast");
  });

  test("refresh deselects machines that disappeared or moved into an environment group", async () => {
    mocks.listAll
      .mockResolvedValueOnce([vm(101), vm(102), vm(103)])
      .mockResolvedValueOnce([vm(101), vm(103)]);
    mocks.buildEnvironmentGroups
      .mockReturnValueOnce([])
      .mockImplementation((resources) => (resources.length === 2 ? [{
        id: "course:1",
        kind: "course",
        title: "課程 A",
        timingLabel: "—",
        nodeLabel: "pve1",
        machines: [{ id: "m3", vmid: 103, requestId: 103, status: "stopped", resource: { vmid: 103 } }],
      }] : []));
    mocks.batchActionInChunks.mockResolvedValue({ total: 1, succeeded: 1, failed: 0, results: [] });

    await renderPage();
    await toggleRow("vm-101");
    await toggleRow("vm-102");
    await toggleRow("vm-103");
    expect(host.textContent).toContain("ResourceMgmtPage.selectedCount:3");

    await act(async () => { await refreshList(); });
    await flush();

    // 102 消失、103 併進環境群組：只剩 101 還勾著
    expect(host.textContent).toContain("ResourceMgmtPage.selectedCount:1");
    await act(async () => { buttonByText("ResourceMgmtPage.actionStart").click(); });
    await flush();
    expect(mocks.batchActionInChunks).toHaveBeenCalledWith([101], "start", expect.any(Object));
  });

  test("a batch confirmed after a refresh only acts on rows still visible and checked", async () => {
    const confirmed = deferred();
    mocks.confirm.mockReturnValue(confirmed.promise);
    const running = (vmid) => vm(vmid, { status: "running" });
    mocks.listAll
      .mockResolvedValueOnce([running(101), running(102)])
      .mockResolvedValueOnce([running(101)]);
    mocks.batchActionInChunks.mockResolvedValue({ total: 1, succeeded: 1, failed: 0, results: [] });

    await renderPage();
    await toggleRow("vm-101");
    await toggleRow("vm-102");
    // 強制停止收在批量列的「更多」選單裡
    await act(async () => { buttonByText("ResourceMgmtPage.batchMore").click(); });
    await act(async () => { host.querySelector('button[data-action="stop"]').click(); });

    // 確認框還開著時清單刷新，102 不見了
    await act(async () => { await refreshList(); });
    await flush();
    await act(async () => { confirmed.resolve(true); });
    await flush();

    expect(mocks.batchActionInChunks).toHaveBeenCalledTimes(1);
    expect(mocks.batchActionInChunks).toHaveBeenCalledWith([101], "stop", expect.any(Object));
  });

  test("an older list response arriving after a newer one is ignored", async () => {
    const older = deferred();
    const newer = deferred();
    mocks.listAll
      .mockResolvedValueOnce([vm(101), vm(102)])
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);

    await renderPage();
    /* 第二次刷新會把還在路上的第一次取消掉（TanStack Query 同一份快取只留最新的請求） */
    let first;
    let second;
    act(() => { first = refreshList(); });
    act(() => { second = refreshList(); });

    await act(async () => { newer.resolve([vm(101)]); await second; });
    await act(async () => { older.resolve([vm(101), vm(102)]); await first; });
    await flush();

    expect(rowOf("vm-101")).toBeTruthy();
    expect(rowOf("vm-102")).toBeUndefined();
  });

  test("a background refresh during the first load joins it instead of firing a second request", async () => {
    const firstLoad = deferred();
    mocks.listAll.mockReturnValueOnce(firstLoad.promise);

    await act(async () => { root.render(withQueryClient(<ResourceMgmtPage />, client)); });
    expect(host.querySelector('[data-testid="loading"]')).toBeTruthy();

    let refresh;
    act(() => { refresh = refreshList(); });
    expect(mocks.listAll).toHaveBeenCalledTimes(1);

    await act(async () => { firstLoad.resolve([vm(101)]); await refresh; });
    await flush();
    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(rowOf("vm-101")).toBeTruthy();
    expect(mocks.listAll).toHaveBeenCalledTimes(1);
  });

  test("a background refresh sent before a row delete does not bring the row back", async () => {
    const stale = deferred();
    mocks.listAll
      .mockResolvedValueOnce([vm(101), vm(102)])
      .mockReturnValueOnce(stale.promise);
    mocks.confirm.mockResolvedValue(true);
    mocks.deleteResource.mockResolvedValue({});

    await renderPage();
    let refresh;
    act(() => { refresh = refreshList(); });

    const menuBtn = rowOf("vm-102").querySelector('button[aria-label="ResourceMgmtPage.powerControlTitle"]');
    await act(async () => { menuBtn.click(); });
    await act(async () => { host.querySelector('button[data-action="delete-row"]').click(); });
    await flush();
    expect(mocks.deleteResource).toHaveBeenCalledWith(102);
    expect(rowOf("vm-102")).toBeUndefined();

    await act(async () => { stale.resolve([vm(101), vm(102)]); await refresh; });
    await flush();
    expect(rowOf("vm-102")).toBeUndefined();
  });

  test("a date-only expiry shows that calendar day plus the local cutoff time", async () => {
    const previousTz = process.env.TZ;
    process.env.TZ = "America/New_York";
    try {
      mocks.listAll.mockResolvedValueOnce([vm(101, { expiry_date: "2026-10-15" })]);
      await renderPage();
      const text = rowOf("vm-101").textContent;
      expect(text).toContain("2026/10/15");
      // 到期界線是 10/15 00:00 UTC，紐約是 10/14 20:00
      /* 日期與時間間的空白依 ICU 版本可能是 U+202F，比對前統一成一般空白 */
      expect(text.replace(/\s/g, " ")).toContain("10/14 20:00");
      expect(text).not.toContain("2026/10/14");
    } finally {
      if (previousTz === undefined) delete process.env.TZ;
      else process.env.TZ = previousTz;
    }
  });
});

describe("ResourceMgmtPage 批量操作", () => {
  const machines = [
    { vmid: 101, name: "web", status: "running", type: "qemu", can_control: true },
    { vmid: 102, name: "db", status: "stopped", type: "qemu", can_control: true },
    { vmid: 103, name: "cache", status: "stopped", type: "lxc", can_control: true },
  ];
  const rowBox = (name) => host.querySelector(`input[aria-label="ResourceMgmtPage.selectRowAria"][data-name="${name}"]`)
    ?? [...host.querySelectorAll('tbody input[type="checkbox"]')][machines.findIndex((m) => m.name === name)];

  async function renderWith(list) {
    mocks.listAll.mockResolvedValue(list);
    await act(async () => { root.render(withQueryClient(<ResourceMgmtPage />, client)); });
    await flush();
  }

  async function click(el, init = {}) {
    await act(async () => {
      el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, ...init }));
    });
    await flush();
  }

  test("沒勾選時不顯示操作列；勾選後只對適用的機器送出，略過的台數寫在結果裡", async () => {
    await renderWith(machines);
    expect(buttonByText("ResourceMgmtPage.actionStart")).toBeUndefined();

    await click(rowBox("web"));
    await click(rowBox("db"));
    const start = buttonByText("ResourceMgmtPage.actionStart");
    expect(start.disabled).toBe(false);

    mocks.batchActionInChunks.mockResolvedValue({ total: 1, succeeded: 1, failed: 0, results: [{ vmid: 102, success: true }] });
    await click(start);

    expect(mocks.batchActionInChunks).toHaveBeenCalledWith([102], "start", expect.any(Object));
    expect(mocks.toast.success).toHaveBeenCalledWith("ResourceMgmtPage.batchSuccessSkippedToast:1");
  });

  test("一台都不適用的動作停用", async () => {
    await renderWith(machines);
    await click(rowBox("db"));
    expect(buttonByText("ResourceMgmtPage.actionShutdown").disabled).toBe(true);
    expect(buttonByText("ResourceMgmtPage.actionStart").disabled).toBe(false);
  });

  test("部分失敗：失敗的保留勾選，靜默刷新不閃載入畫面", async () => {
    await renderWith(machines);
    await click(rowBox("db"));
    await click(rowBox("cache"));
    mocks.batchActionInChunks.mockResolvedValue({
      total: 2, succeeded: 1, failed: 1,
      results: [{ vmid: 102, success: false }, { vmid: 103, success: true }],
    });
    await click(buttonByText("ResourceMgmtPage.actionStart"));

    expect(mocks.toast.error).toHaveBeenCalledWith("ResourceMgmtPage.batchFailedKeptToast");
    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(mocks.listAll).toHaveBeenCalledTimes(2);
    expect(rowBox("db").checked).toBe(true);
    expect(rowBox("cache").checked).toBe(false);
  });

  test("Shift＋點選一次勾起一段", async () => {
    await renderWith(machines);
    await click(rowBox("web"));
    await click(rowBox("cache"), { shiftKey: true });
    expect(machines.map((m) => rowBox(m.name).checked)).toEqual([true, true, true]);
  });
});

describe("ResourceMgmtPage 搜尋與狀態篩選", () => {
  const machines = [
    { vmid: 101, name: "web", status: "running", type: "qemu", can_control: true, owner_name: "王小明" },
    { vmid: 102, name: "db", status: "stopped", type: "qemu", can_control: true, owner_name: "李小華" },
    { vmid: 103, name: "cache", status: "stopped", type: "lxc", can_control: true, owner_name: "王小明" },
  ];
  const rowNames = () => [...host.querySelectorAll("tbody tr")].map((tr) => tr.querySelector("td:nth-child(2)").textContent);

  async function renderWith(list) {
    mocks.listAll.mockResolvedValue(list);
    await act(async () => { root.render(withQueryClient(<ResourceMgmtPage />, client)); });
    await flush();
  }

  async function search(value) {
    const input = host.querySelector('input[aria-label="ResourceMgmtPage.searchPlaceholder"]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    await act(async () => {
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await flush();
  }

  test("搜尋擁有者只留符合的列", async () => {
    await renderWith(machines);
    await search("王小明");
    expect(rowNames().map((name) => name.startsWith("web") || name.startsWith("cache"))).toEqual([true, true]);
    expect(rowNames()).toHaveLength(2);
  });

  test("狀態切換後全選只勾得到看得見的機器", async () => {
    await renderWith(machines);
    await act(async () => { buttonByText("ResourceMgmtPage.statusStopped").click(); });
    await flush();
    expect(rowNames()).toHaveLength(2);

    const selectAll = host.querySelector('input[aria-label="ResourceMgmtPage.selectAllAria"]');
    await act(async () => { selectAll.click(); });
    await flush();

    mocks.confirm.mockResolvedValue(true);
    mocks.batchActionInChunks.mockResolvedValue({ total: 2, succeeded: 2, failed: 0, results: [] });
    await act(async () => { buttonByText("ResourceMgmtPage.delete").click(); });
    await flush();
    /* 執行中的 web 被篩掉了，刪除只送已關機的兩台 */
    expect(mocks.batchActionInChunks).toHaveBeenCalledWith([102, 103], "delete", expect.any(Object));
  });

  test("篩不到東西時顯示清除鈕，按了回到全部", async () => {
    await renderWith(machines);
    await search("不存在的機器");
    expect(host.querySelector("tbody")).toBeNull();

    await act(async () => { buttonByText("EmptyState.clearSearch").click(); });
    await flush();
    expect(rowNames()).toHaveLength(3);
  });

  test("超過 60 台時散機列只渲染視窗附近的列，其餘用空白列撐高度", async () => {
    /* happy-dom 沒有版面：列高 48、視窗高 480；virtualizer 讀 window.ResizeObserver，換成不回呼的替身 */
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get: () => 48 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 480 });
    Object.defineProperty(window, "scrollY", { configurable: true, value: 0 });
    class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
    const originalResizeObserver = window.ResizeObserver;
    Object.defineProperty(window, "ResizeObserver", { configurable: true, value: ResizeObserverStub });
    try {
      mocks.buildEnvironmentGroups.mockReturnValue([]);
      mocks.listAll.mockResolvedValue(Array.from({ length: 300 }, (_, i) => vm(1000 + i)));
      await renderPage();

      const rows = host.querySelectorAll("tr[data-guide='resource-mgmt-row']");
      expect(rows.length).toBeGreaterThan(0);
      expect(rows.length).toBeLessThan(300);
      expect(rows[0].textContent).toContain("vm-1000");
      expect(host.querySelector("tbody[data-virtualized]")).not.toBeNull();
      const spacer = [...host.querySelectorAll("tr[aria-hidden='true']")].pop();
      expect(parseInt(spacer.style.height, 10)).toBeGreaterThan(0);
    } finally {
      delete HTMLElement.prototype.offsetHeight;
      Object.defineProperty(window, "ResizeObserver", { configurable: true, value: originalResizeObserver });
    }
  });
});
