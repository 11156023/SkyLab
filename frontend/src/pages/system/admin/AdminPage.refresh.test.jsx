// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AdminPage from "./AdminPage";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
import { userKeys } from "../../../queries/users";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  deleteUser: vi.fn(),
  reviewRegistration: vi.fn(),
  getRegistrationPolicy: vi.fn(),
  listPendingRegistrations: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/users", () => ({
  UsersService: {
    listAll: mocks.listAll,
    delete: mocks.deleteUser,
    reviewRegistration: mocks.reviewRegistration,
    getRegistrationPolicy: mocks.getRegistrationPolicy,
    listPendingRegistrations: mocks.listPendingRegistrations,
  },
}));
/* 群組清單與這裡要測的行為無關：一律回空清單 */
vi.mock("../../../services/userGroups", () => ({
  UserGroupsService: { list: () => Promise.resolve({ data: [], count: 0 }), members: () => Promise.resolve({ data: [], count: 0 }) },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "me" } }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));

let root;
let host;
let client;

/* TanStack Query 以 macrotask 批次通知，先等一個 timer tick 再看畫面 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

/* 背景刷新的一次 tick（原本是 useAutoRefresh 的計時器）：直接叫 QueryClient 重抓使用者清單 */
function backgroundRefresh() {
  return client.refetchQueries({ queryKey: userKeys.list });
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const user = (id) => ({
  id, email: `${id}@example.edu`, full_name: `user-${id}`, role: "student", is_active: true, created_at: "2026-09-01T00:00:00Z",
});

async function renderPage() {
  await act(async () => { root.render(withQueryClient(<AdminPage />, client)); });
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.getRegistrationPolicy.mockResolvedValue({ signup_enabled: true, approval_required: false, google_signup_enabled: true });
  mocks.listPendingRegistrations.mockResolvedValue({ data: [] });
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
});

describe("AdminPage user list refresh", () => {
  test("an older list response arriving after a newer one is ignored", async () => {
    const older = deferred();
    const newer = deferred();
    mocks.listAll
      .mockResolvedValueOnce([user("a"), user("b")])
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);

    await renderPage();
    await flush();
    expect(host.textContent).toContain("user-b");

    let first;
    let second;
    act(() => { first = backgroundRefresh(); });
    act(() => { second = backgroundRefresh(); });
    await act(async () => { newer.resolve([user("a")]); await second; });
    await act(async () => { older.resolve([user("a"), user("b")]); await first; });
    await flush();

    expect(host.textContent).toContain("user-a");
    expect(host.textContent).not.toContain("user-b");
  });

  test("a refresh sent before a delete does not bring the deleted user back", async () => {
    const stale = deferred();
    mocks.listAll
      .mockResolvedValueOnce([user("a"), user("b")])
      .mockReturnValueOnce(stale.promise);
    mocks.confirm.mockResolvedValue(true);
    mocks.deleteUser.mockResolvedValue({});

    await renderPage();
    await flush();

    let refresh;
    act(() => { refresh = backgroundRefresh(); });

    const rowB = [...host.querySelectorAll("button[title='AdminPage.deleteTitle']")]
      .find((button) => button.closest("div").parentElement.textContent.includes("user-b"));
    await act(async () => { rowB.click(); });
    await flush();
    expect(mocks.deleteUser).toHaveBeenCalledWith("b");
    expect(host.textContent).not.toContain("user-b");

    await act(async () => { stale.resolve([user("a"), user("b")]); await refresh; });
    await flush();
    expect(host.textContent).not.toContain("user-b");
  });

  test("a background refresh during the first load joins it, and the first-load failure is still reported", async () => {
    const firstLoad = deferred();
    mocks.listAll.mockReturnValueOnce(firstLoad.promise);

    await renderPage();
    expect(host.querySelector('[data-testid="loading"]')).toBeTruthy();

    /* 還沒有資料時，重抓會併進進行中的首次載入（不會另發一個請求把它作廢） */
    let refresh;
    act(() => { refresh = backgroundRefresh(); });
    expect(mocks.listAll).toHaveBeenCalledTimes(1);
    await act(async () => { firstLoad.reject(new Error("list failed")); await refresh; });
    await flush();

    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(mocks.toast.error).toHaveBeenCalledWith("list failed");
  });

  test("background refresh failures keep the list and stay silent", async () => {
    mocks.listAll
      .mockResolvedValueOnce([user("a")])
      .mockRejectedValueOnce(new Error("503"));

    await renderPage();
    await flush();
    await act(async () => { await backgroundRefresh(); });
    await flush();

    expect(host.textContent).toContain("user-a");
    expect(mocks.toast.error).not.toHaveBeenCalled();
  });

  test("a failed first load is reported once even if background retries keep failing", async () => {
    mocks.listAll.mockRejectedValue(new Error("list failed"));

    await renderPage();
    await flush();
    await act(async () => { await backgroundRefresh(); });
    await flush();

    expect(mocks.listAll).toHaveBeenCalledTimes(2);
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
  });
});

describe("AdminPage pending registrations", () => {
  test("reviewing a registration removes it from the pending list and updates the user row in place", async () => {
    const pendingUser = { ...user("p"), is_active: false };
    mocks.listAll.mockResolvedValue([user("a"), pendingUser]);
    mocks.listPendingRegistrations.mockResolvedValue({ data: [pendingUser] });
    mocks.reviewRegistration.mockResolvedValue({ ...pendingUser, is_active: true });

    await renderPage();
    await flush();
    expect(host.textContent).toContain("AdminPage.pendingRegistrationsTitle (1)");

    const approve = [...host.querySelectorAll("button")].find((b) => b.textContent === "AdminPage.approveRegistration");
    await act(async () => { approve.click(); });
    await flush();

    expect(mocks.reviewRegistration).toHaveBeenCalledWith("p", true);
    expect(host.textContent).toContain("AdminPage.pendingRegistrationsTitle (0)");
    expect(client.getQueryData(userKeys.list).find((u) => u.id === "p").is_active).toBe(true);
    /* 就地更新，不整份重抓 */
    expect(mocks.listAll).toHaveBeenCalledTimes(1);
    expect(mocks.listPendingRegistrations).toHaveBeenCalledTimes(1);
  });

  test("a registration settings load failure is reported", async () => {
    mocks.listAll.mockResolvedValue([]);
    mocks.listPendingRegistrations.mockRejectedValue(new Error("pending failed"));

    await renderPage();
    await flush();

    expect(mocks.toast.error).toHaveBeenCalledWith("pending failed");
  });
});
