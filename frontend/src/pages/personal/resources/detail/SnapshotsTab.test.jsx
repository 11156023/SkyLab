// @vitest-environment happy-dom
/**
 * 快照分頁的挖礦存證快照（mining-*）：標上「存證」，非管理員看不到刪除鈕；
 * 一般快照與管理員的操作維持原樣。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import SnapshotsTab from "./SnapshotsTab";
import { withQueryClient } from "../../../../queries/testing";

const mocks = vi.hoisted(() => ({
  listSnapshots: vi.fn(),
  deleteSnapshot: vi.fn(),
  confirm: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
  user: { id: "u1", role: "student" },
  t: (key) => key,
}));
vi.mock("../../../../services/resources", () => ({
  ResourcesService: {
    listSnapshots: mocks.listSnapshots,
    deleteSnapshot: mocks.deleteSnapshot,
  },
}));
vi.mock("../../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: mocks.user }) }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

const EVIDENCE = { name: "mining-202610120900", description: "Mining evidence (auto)", snaptime: 1791000000 };
const OWN = { name: "before-upgrade", description: "升級前", snaptime: 1790000000 };
const INIT = { name: "skylab-init", description: null, snaptime: 1789000000 };

let host;
let toolbar;
let root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.user = { id: "u1", role: "student" };
  mocks.listSnapshots.mockResolvedValue([EVIDENCE, OWN, INIT]);
  mocks.confirm.mockResolvedValue(true);
  mocks.deleteSnapshot.mockResolvedValue({ message: "ok", task_id: "t1" });
  host = document.createElement("div");
  toolbar = document.createElement("div");
  document.body.append(host, toolbar);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  toolbar.remove();
});

/* TanStack Query 的通知排在 setTimeout(0)：先等一個 timer tick，再把 microtask 清掉 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

async function renderTab(onOperationFailed = vi.fn()) {
  await act(async () => {
    root.render(withQueryClient(<SnapshotsTab vmid={105} toolbar={toolbar} onOperationFailed={onOperationFailed} />));
  });
  await flush();
}

const rowFor = (name) =>
  [...host.querySelectorAll("tbody tr")].find((tr) => tr.textContent.includes(name));
const buttonsByText = (scope, text) =>
  [...scope.querySelectorAll("button")].filter((b) => b.textContent.includes(text));

describe("SnapshotsTab 挖礦存證快照", () => {
  test("存證快照標上「存證」，其他快照沒有", async () => {
    await renderTab();

    expect(rowFor(EVIDENCE.name).textContent).toContain("SnapshotsTab.evidence");
    expect(rowFor(OWN.name).textContent).not.toContain("SnapshotsTab.evidence");
    expect(rowFor(INIT.name).textContent).not.toContain("SnapshotsTab.evidence");
  });

  test.each([
    ["學生", { id: "u1", role: "student" }],
    ["老師", { id: "u2", role: "teacher" }],
  ])("%s看不到存證快照的刪除鈕，自己的快照照常可刪", async (_label, user) => {
    mocks.user = user;
    await renderTab();

    expect(buttonsByText(rowFor(EVIDENCE.name), "SnapshotsTab.delete")).toHaveLength(0);
    expect(buttonsByText(rowFor(EVIDENCE.name), "SnapshotsTab.restore")).toHaveLength(1);
    expect(buttonsByText(rowFor(OWN.name), "SnapshotsTab.delete")).toHaveLength(1);
  });

  test("管理員可以刪除存證快照", async () => {
    mocks.user = { id: "a1", role: "admin" };
    await renderTab();

    const [deleteButton] = buttonsByText(rowFor(EVIDENCE.name), "SnapshotsTab.delete");
    expect(deleteButton).toBeDefined();
    await act(async () => deleteButton.click());

    expect(mocks.deleteSnapshot).toHaveBeenCalledWith(105, EVIDENCE.name);
  });

  test("刪除後重抓清單，畫面換成新的清單", async () => {
    await renderTab();
    mocks.listSnapshots.mockResolvedValue([EVIDENCE, INIT]);
    const [deleteButton] = buttonsByText(rowFor(OWN.name), "SnapshotsTab.delete");
    await act(async () => deleteButton.click());
    await flush();

    expect(mocks.deleteSnapshot).toHaveBeenCalledWith(105, OWN.name);
    expect(mocks.listSnapshots).toHaveBeenCalledTimes(2);
    expect(rowFor(OWN.name)).toBeUndefined();
  });

  test("清單載入失敗時提示、顯示空清單，並通知上層重查可用性", async () => {
    mocks.listSnapshots.mockRejectedValue(new Error("boom"));
    const onOperationFailed = vi.fn();
    await renderTab(onOperationFailed);

    expect(mocks.toast.error).toHaveBeenCalledWith("boom");
    expect(onOperationFailed).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("SnapshotsTab.emptyTitle");
  });

  test("名稱中間帶 mining 的一般快照不算存證", async () => {
    mocks.listSnapshots.mockResolvedValue([{ name: "pre-mining-check", description: null, snaptime: 1 }]);
    await renderTab();

    const row = rowFor("pre-mining-check");
    expect(row.textContent).not.toContain("SnapshotsTab.evidence");
    expect(buttonsByText(row, "SnapshotsTab.delete")).toHaveLength(1);
  });
});
