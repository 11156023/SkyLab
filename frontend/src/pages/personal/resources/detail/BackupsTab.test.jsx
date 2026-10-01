// @vitest-environment happy-dom
/**
 * 備份分頁：清單、上限、關機提示，以及「背景任務進行中鎖住操作、跑完自動重抓」。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import BackupsTab from "./BackupsTab";

const mocks = vi.hoisted(() => ({
  listBackups: vi.fn(),
  createBackup: vi.fn(),
  restoreBackup: vi.fn(),
  deleteBackup: vi.fn(),
  confirm: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
  jobs: { items: [] },
  t: (key) => key,
}));
vi.mock("../../../../services/resources", () => ({
  ResourcesService: {
    listBackups: mocks.listBackups,
    createBackup: mocks.createBackup,
    restoreBackup: mocks.restoreBackup,
    deleteBackup: mocks.deleteBackup,
  },
}));
vi.mock("../../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../../components/Jobs/JobsProvider", () => ({ useJobs: () => mocks.jobs }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

const OLD = { volid: "pbs:backup/ct/105/2026-09-10T00:00:00Z", created_at: 1789000000, size: 5832173897, description: null };
const NEW = { volid: "pbs:backup/ct/105/2026-09-20T00:00:00Z", created_at: 1790000000, size: 300 * 1024 ** 2, description: "升級前" };

let host;
let toolbar;
let root;
let onOperationFailed;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.jobs = { items: [] };
  mocks.listBackups.mockResolvedValue([NEW, OLD]);
  mocks.confirm.mockResolvedValue(true);
  mocks.createBackup.mockResolvedValue({ message: "ok", task_id: "t1" });
  mocks.restoreBackup.mockResolvedValue({ message: "ok", task_id: "t2" });
  mocks.deleteBackup.mockResolvedValue({ message: "ok" });
  onOperationFailed = vi.fn();
  host = document.createElement("div");
  toolbar = document.createElement("div");
  document.body.append(host, toolbar);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  toolbar.remove();
  vi.useRealTimers();
});

async function renderTab(capability = { available: true, requires_shutdown: false, max_count: 3 }) {
  await act(async () => {
    root.render(
      <BackupsTab vmid={105} toolbar={toolbar} capability={capability} onOperationFailed={onOperationFailed} />,
    );
  });
  return capability;
}

const buttonsByText = (scope, text) =>
  [...scope.querySelectorAll("button")].filter((b) => b.textContent.includes(text));
const createButton = () => buttonsByText(toolbar, "BackupsTab.createBackup")[0];
const rows = () => [...host.querySelectorAll("tbody tr")];

describe("BackupsTab", () => {
  test("列出備份：時間、描述、大小", async () => {
    await renderTab();

    expect(mocks.listBackups).toHaveBeenCalledWith(105);
    expect(rows()).toHaveLength(2);
    expect(rows()[0].textContent).toContain("升級前");
    expect(rows()[0].textContent).toContain("300 MB");
    expect(rows()[1].textContent).toContain("5.4 GB");
    expect(host.textContent).toContain("BackupsTab.notice");
  });

  test("備份時要關機的機器才顯示關機提示", async () => {
    await renderTab({ available: true, requires_shutdown: true, max_count: 3 });
    expect(host.textContent).toContain("BackupsTab.shutdownNotice");
  });

  test("線上備份的機器不顯示關機提示", async () => {
    await renderTab();
    expect(host.textContent).not.toContain("BackupsTab.shutdownNotice");
  });

  test("已達上限時不能再建立備份", async () => {
    await renderTab({ available: true, requires_shutdown: false, max_count: 2 });
    expect(createButton().disabled).toBe(true);
    expect(createButton().title).toBe("BackupsTab.limitReachedHint");
  });

  test("沒有上限（管理員）時可以建立備份", async () => {
    await renderTab({ available: true, requires_shutdown: false, max_count: null });
    expect(createButton().disabled).toBe(false);
  });

  test("建立備份：送出描述，入列後鎖住操作並提示任務進行中", async () => {
    await renderTab();
    await act(async () => createButton().click());

    const textarea = document.querySelector("#backup-desc");
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
    await act(async () => {
      setter.call(textarea, "  升級前  ");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      textarea.closest("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });

    expect(mocks.createBackup).toHaveBeenCalledWith(105, { description: "升級前" });
    expect(mocks.toast.success).toHaveBeenCalledWith("BackupsTab.backupQueued");
    expect(host.textContent).toContain("BackupsTab.inProgress");
    expect(createButton().disabled).toBe(true);
    /* 對話框有關閉動畫，元素可能還在；但表單必須已清空，下次開啟不殘留內容 */
    expect(document.querySelector("#backup-desc")?.value ?? "").toBe("");
  });

  test("還原：確認後以該列的 volid 送出", async () => {
    await renderTab();
    await act(async () => buttonsByText(rows()[1], "BackupsTab.restore")[0].click());

    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    expect(mocks.restoreBackup).toHaveBeenCalledWith(105, OLD.volid);
    expect(mocks.toast.success).toHaveBeenCalledWith("BackupsTab.restoreQueued");
  });

  test("還原：取消確認就不送出", async () => {
    mocks.confirm.mockResolvedValue(false);
    await renderTab();
    await act(async () => buttonsByText(rows()[0], "BackupsTab.restore")[0].click());

    expect(mocks.restoreBackup).not.toHaveBeenCalled();
  });

  test("刪除：確認後刪除並重抓清單", async () => {
    await renderTab();
    mocks.listBackups.mockResolvedValue([OLD]);
    await act(async () => buttonsByText(rows()[0], "BackupsTab.delete")[0].click());

    expect(mocks.deleteBackup).toHaveBeenCalledWith(105, NEW.volid);
    expect(mocks.listBackups).toHaveBeenCalledTimes(2);
    expect(rows()).toHaveLength(1);
  });

  test("這台機器有備份任務在跑時鎖住操作；任務結束後自動重抓清單", async () => {
    mocks.jobs = { items: [{ id: "resource_backup:t1", kind: "resource_backup", meta: { vmid: 105 } }] };
    mocks.listBackups.mockResolvedValue([OLD]);
    const capability = await renderTab();

    expect(host.textContent).toContain("BackupsTab.inProgress");
    expect(createButton().disabled).toBe(true);
    expect(buttonsByText(rows()[0], "BackupsTab.restore")[0].disabled).toBe(true);
    expect(mocks.listBackups).toHaveBeenCalledTimes(1);

    mocks.jobs = { items: [] };
    mocks.listBackups.mockResolvedValue([NEW, OLD]);
    await renderTab(capability);

    expect(mocks.listBackups).toHaveBeenCalledTimes(2);
    expect(rows()).toHaveLength(2);
    expect(host.textContent).not.toContain("BackupsTab.inProgress");
    expect(createButton().disabled).toBe(false);
  });

  test("別台機器的備份任務不影響這一頁", async () => {
    mocks.jobs = { items: [{ id: "resource_backup:t9", kind: "resource_backup", meta: { vmid: 999 } }] };
    await renderTab();

    expect(host.textContent).not.toContain("BackupsTab.inProgress");
    expect(createButton().disabled).toBe(false);
  });

  test("清單載入失敗時通知上層重查可用性", async () => {
    mocks.listBackups.mockRejectedValue(new Error("boom"));
    await renderTab();

    expect(mocks.toast.error).toHaveBeenCalledWith("boom");
    expect(onOperationFailed).toHaveBeenCalledTimes(1);
  });

  test("操作失敗時顯示錯誤並通知上層重查可用性", async () => {
    mocks.deleteBackup.mockRejectedValue(new Error("storage offline"));
    await renderTab();
    await act(async () => buttonsByText(rows()[0], "BackupsTab.delete")[0].click());

    expect(mocks.toast.error).toHaveBeenCalledWith("storage offline");
    expect(onOperationFailed).toHaveBeenCalledTimes(1);
  });
});
