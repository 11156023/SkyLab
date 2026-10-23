// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AdminPage from "./AdminPage";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  deleteUser: vi.fn(),
  getRegistrationPolicy: vi.fn(),
  listPendingRegistrations: vi.fn(),
  confirm: vi.fn(),
  autoRefreshes: [],
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/users", () => ({
  UsersService: {
    listAll: mocks.listAll,
    delete: mocks.deleteUser,
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
/* 頁面掛了不只一個自動更新（使用者清單、註冊設定）：依呼叫位置各記一格，每次 render 換成最新的 callback */
vi.mock("../../../hooks/useAutoRefresh", async () => {
  const { useRef } = await import("react");
  return {
    default: (refresh) => {
      const slot = useRef(null);
      if (slot.current === null) slot.current = mocks.autoRefreshes.length;
      mocks.autoRefreshes[slot.current] = refresh;
    },
  };
});
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

/* 自動更新的一次 tick：頁面上所有的自動更新一起觸發 */
function autoRefresh() {
  return Promise.all(mocks.autoRefreshes.map((refresh) => refresh()));
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

beforeEach(() => {
  vi.resetAllMocks();
  mocks.autoRefreshes = [];
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

describe("AdminPage user list refresh", () => {
  test("an older list response arriving after a newer one is ignored", async () => {
    const older = deferred();
    const newer = deferred();
    mocks.listAll
      .mockResolvedValueOnce([user("a"), user("b")])
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);

    await act(async () => { root.render(<AdminPage />); });
    await flush();
    expect(host.textContent).toContain("user-b");

    let first;
    let second;
    act(() => { first = autoRefresh(); });
    act(() => { second = autoRefresh(); });
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

    await act(async () => { root.render(<AdminPage />); });
    await flush();

    let refresh;
    act(() => { refresh = autoRefresh(); });

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

  test("a first-load failure still reports the error when a background refresh overtook it", async () => {
    const firstLoad = deferred();
    const background = deferred();
    mocks.listAll
      .mockReturnValueOnce(firstLoad.promise)
      .mockReturnValueOnce(background.promise);

    await act(async () => { root.render(<AdminPage />); });
    expect(host.querySelector('[data-testid="loading"]')).toBeTruthy();

    let refresh;
    act(() => { refresh = autoRefresh(); });
    await act(async () => { background.reject(new Error("list failed")); await refresh; });
    await flush();

    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(mocks.toast.error).toHaveBeenCalledWith("list failed");
  });
});
