// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import ResourceMgmtPage from "./ResourceMgmtPage";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  batchActionInChunks: vi.fn(),
  listAllSessions: vi.fn(),
  buildEnvironmentGroups: vi.fn(),
  navigate: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/resources", () => ({
  ResourcesService: { listAll: mocks.listAll, batchActionInChunks: mocks.batchActionInChunks },
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
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/MachineKindBadge/MachineKindBadge", () => ({ default: () => null }));
vi.mock("../../../components/PowerMenu/PowerMenu", () => ({
  default: ({ items = [], onControl }) => (
    <div>
      {items.map((item) => (
        <button key={item.action} type="button" data-action={item.action} onClick={() => onControl(item.action)}>
          {item.label}
        </button>
      ))}
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

beforeEach(() => {
  vi.resetAllMocks();
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
    await act(async () => { root.render(<ResourceMgmtPage />); });
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
    expect(mocks.toast.success).toHaveBeenCalledWith("ResourceMgmtPage.batchSuccessSkippedToast");
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
    await act(async () => { root.render(<ResourceMgmtPage />); });
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
});
