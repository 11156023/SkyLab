// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import ResourceMgmtPage from "./ResourceMgmtPage";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  batchActionInChunks: vi.fn(),
  deleteResource: vi.fn(),
  listAllSessions: vi.fn(),
  buildEnvironmentGroups: vi.fn(),
  navigate: vi.fn(),
  confirm: vi.fn(),
  autoRefresh: null,
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
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: (refresh) => { mocks.autoRefresh = refresh; } }));
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

async function flush() {
  await act(async () => {
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
  await act(async () => { root.render(<ResourceMgmtPage />); });
  await flush();
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.autoRefresh = null;
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
  test("Retry after a failed first load clears the error state", async () => {
    mocks.listAll
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce([]);

    await act(async () => { root.render(<ResourceMgmtPage />); });
    await flush();
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

    await act(async () => { root.render(<ResourceMgmtPage />); });
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

    await act(async () => { await mocks.autoRefresh(); });
    await flush();

    // 102 消失、103 併進環境群組：只剩 101 還勾著
    expect(host.textContent).toContain("ResourceMgmtPage.selectedCount:1");
    await act(async () => { buttonByText("ResourceMgmtPage.actionStart").click(); });
    await flush();
    expect(mocks.batchActionInChunks).toHaveBeenCalledWith([101], "start");
  });

  test("a batch confirmed after a refresh only acts on rows still visible and checked", async () => {
    const confirmed = deferred();
    mocks.confirm.mockReturnValue(confirmed.promise);
    mocks.listAll
      .mockResolvedValueOnce([vm(101), vm(102)])
      .mockResolvedValueOnce([vm(101)]);
    mocks.batchActionInChunks.mockResolvedValue({ total: 1, succeeded: 1, failed: 0, results: [] });

    await renderPage();
    await toggleRow("vm-101");
    await toggleRow("vm-102");
    await act(async () => { buttonByText("ResourceMgmtPage.actionStop").click(); });

    // 確認框還開著時清單刷新，102 不見了
    await act(async () => { await mocks.autoRefresh(); });
    await flush();
    await act(async () => { confirmed.resolve(true); });
    await flush();

    expect(mocks.batchActionInChunks).toHaveBeenCalledTimes(1);
    expect(mocks.batchActionInChunks).toHaveBeenCalledWith([101], "stop");
  });

  test("an older list response arriving after a newer one is ignored", async () => {
    const older = deferred();
    const newer = deferred();
    mocks.listAll
      .mockResolvedValueOnce([vm(101), vm(102)])
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);

    await renderPage();
    let first;
    let second;
    act(() => { first = mocks.autoRefresh(); });
    act(() => { second = mocks.autoRefresh(); });

    await act(async () => { newer.resolve([vm(101)]); await second; });
    await act(async () => { older.resolve([vm(101), vm(102)]); await first; });
    await flush();

    expect(rowOf("vm-101")).toBeTruthy();
    expect(rowOf("vm-102")).toBeUndefined();
  });

  test("a first-load failure still shows the error when a background refresh overtook it", async () => {
    const firstLoad = deferred();
    const background = deferred();
    mocks.listAll
      .mockReturnValueOnce(firstLoad.promise)
      .mockReturnValueOnce(background.promise);

    await act(async () => { root.render(<ResourceMgmtPage />); });
    expect(host.querySelector('[data-testid="loading"]')).toBeTruthy();

    let refresh;
    act(() => { refresh = mocks.autoRefresh(); });
    await act(async () => { background.reject(new Error("pve down")); await refresh; });
    await flush();

    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(buttonByText("Error.retry")).toBeTruthy();

    // 被超車的首次載入晚回來也不能蓋掉
    await act(async () => { firstLoad.resolve([vm(101)]); });
    await flush();
    expect(buttonByText("Error.retry")).toBeTruthy();
    expect(rowOf("vm-101")).toBeUndefined();
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
    act(() => { refresh = mocks.autoRefresh(); });

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
