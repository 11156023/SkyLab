// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AdminPage from "./AdminPage";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  deleteUser: vi.fn(),
  update: vi.fn(),
  getRegistrationPolicy: vi.fn(),
  listPendingRegistrations: vi.fn(),
  confirm: vi.fn(),
  t: (key, opts) => (opts && typeof opts.count === "number" ? `${key}:${opts.count}` : key),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/users", () => ({
  UsersService: {
    listAll: mocks.listAll,
    delete: mocks.deleteUser,
    update: mocks.update,
    getRegistrationPolicy: mocks.getRegistrationPolicy,
    listPendingRegistrations: mocks.listPendingRegistrations,
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "me" } }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));

let root;
let host;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

const user = (id) => ({
  id, email: `${id}@example.edu`, full_name: `user-${id}`, role: "student", is_active: true, created_at: "2026-09-01T00:00:00Z",
});

/* 該使用者那一列的勾選框（列由 data-guide 標記） */
function checkboxOf(name) {
  const row = [...host.querySelectorAll('[data-guide="admin-user-row"]')].find((el) => el.textContent.includes(name));
  return row?.querySelector('input[type="checkbox"]');
}

function buttonByText(text) {
  return [...host.querySelectorAll("button")].find((button) => button.textContent.includes(text));
}

async function renderWith(list) {
  mocks.listAll.mockResolvedValue(list);
  await act(async () => { root.render(<AdminPage />); });
  await flush();
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.getRegistrationPolicy.mockResolvedValue({ signup_enabled: true, approval_required: false, google_signup_enabled: true });
  mocks.listPendingRegistrations.mockResolvedValue({ data: [] });
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("AdminPage batch delete", () => {
  test("勾選後批量列有刪除鈕；確認後逐筆刪除並從清單移除", async () => {
    await renderWith([user("me"), user("a"), user("b")]);
    expect(buttonByText("AdminPage.delete")).toBeUndefined();

    await act(async () => { checkboxOf("user-a").click(); });
    await act(async () => { checkboxOf("user-b").click(); });
    await flush();
    expect(host.textContent).toContain("AdminPage.selectedCount:2");

    mocks.confirm.mockResolvedValue(true);
    mocks.deleteUser.mockResolvedValue({});
    await act(async () => { buttonByText("AdminPage.delete").click(); });
    await flush();

    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    const [{ title, message, danger }] = mocks.confirm.mock.calls[0];
    expect(title).toBe("AdminPage.batchDeleteTitle:2");
    expect(message).toContain("user-a");
    expect(message).toContain("user-b");
    expect(danger).toBe(true);
    expect(mocks.deleteUser.mock.calls.map(([id]) => id).sort()).toEqual(["a", "b"]);
    expect(host.textContent).not.toContain("user-a");
    expect(host.textContent).not.toContain("user-b");
    expect(mocks.toast.success).toHaveBeenCalledWith("AdminPage.batchDoneToast:2");
    expect(buttonByText("AdminPage.delete")).toBeUndefined();
  });

  test("取消確認就不刪", async () => {
    await renderWith([user("me"), user("a")]);
    await act(async () => { checkboxOf("user-a").click(); });
    await flush();
    mocks.confirm.mockResolvedValue(false);
    await act(async () => { buttonByText("AdminPage.delete").click(); });
    await flush();
    expect(mocks.deleteUser).not.toHaveBeenCalled();
    expect(host.textContent).toContain("user-a");
  });

  test("部分失敗：成功的消失、失敗的留在清單並保留勾選，錯誤原因進 toast", async () => {
    await renderWith([user("me"), user("a"), user("b")]);
    await act(async () => { checkboxOf("user-a").click(); });
    await act(async () => { checkboxOf("user-b").click(); });
    await flush();

    mocks.confirm.mockResolvedValue(true);
    mocks.deleteUser.mockImplementation((id) => (
      id === "b" ? Promise.reject(new Error("still owns resources")) : Promise.resolve({})
    ));
    await act(async () => { buttonByText("AdminPage.delete").click(); });
    await flush();

    expect(host.textContent).not.toContain("user-a");
    expect(host.textContent).toContain("user-b");
    expect(host.textContent).toContain("AdminPage.selectedCount:1");
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
    expect(mocks.toast.error.mock.calls[0][0]).toContain("still owns resources");
  });
});
