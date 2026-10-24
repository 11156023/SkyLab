// @vitest-environment happy-dom
/*
 * 回歸重現：使用者清單（或註冊設定）第一次就抓不到時，之後每次背景重試（30 秒輪詢／切回視窗）
 * 失敗都會再跳一次錯誤 toast。
 * 舊版（ce7675b9^ AdminPage.jsx:360-376、390、392-406）背景刷新是 silent，只有進頁那次失敗提示。
 * 新版用 `data === undefined && isError` 當觸發條件；沒有資料時重抓會把 status 改回 pending，
 * 布林先變 false 再變回 true，effect 每輪都再跳一次（既有的
 * "a failed first load is reported once even if background retries keep failing" 用立即 reject 的 mock，
 * pending 狀態來不及 render，所以沒測出來）。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import AdminPage from "./AdminPage";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
import { userKeys } from "../../../queries/users";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  getRegistrationPolicy: vi.fn(),
  listPendingRegistrations: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/users", () => ({
  UsersService: {
    listAll: mocks.listAll,
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
/* 群組清單與這裡要測的行為無關：一律回空清單 */
vi.mock("../../../services/userGroups", () => ({
  UserGroupsService: { list: () => Promise.resolve({ data: [], count: 0 }), members: () => Promise.resolve({ data: [], count: 0 }) },
}));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));

let root;
let host;
let client;

/* TanStack Query 以 macrotask 批次通知：多等幾個 timer tick，確保重試中的 pending 狀態已經 render */
async function flush() {
  for (let round = 0; round < 3; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      for (let i = 0; i < 5; i += 1) await Promise.resolve();
    });
  }
}

function slowFailure() {
  let reject;
  const promise = new Promise((_, rej) => { reject = rej; });
  return { promise, reject };
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

test("a slow background retry of a never-loaded user list does not toast the same failure again", async () => {
  const retry = slowFailure();
  mocks.listAll
    .mockRejectedValueOnce(new Error("list failed"))
    .mockReturnValueOnce(retry.promise);

  await act(async () => { root.render(withQueryClient(<AdminPage />, client)); });
  await flush();
  expect(mocks.toast.error).toHaveBeenCalledTimes(1);

  /* 一次背景刷新（原本 useAutoRefresh 的 tick，舊版是 silent） */
  let refresh;
  act(() => { refresh = client.refetchQueries({ queryKey: userKeys.list }); });
  await flush();
  await act(async () => { retry.reject(new Error("list failed")); await refresh; });
  await flush();

  expect(mocks.listAll).toHaveBeenCalledTimes(2);
  expect(mocks.toast.error).toHaveBeenCalledTimes(1);
});

test("a slow background retry of never-loaded registration settings does not toast the same failure again", async () => {
  const retry = slowFailure();
  mocks.listAll.mockResolvedValue([]);
  mocks.listPendingRegistrations.mockReset();
  mocks.listPendingRegistrations
    .mockRejectedValueOnce(new Error("pending failed"))
    .mockReturnValueOnce(retry.promise);

  await act(async () => { root.render(withQueryClient(<AdminPage />, client)); });
  await flush();
  expect(mocks.toast.error).toHaveBeenCalledTimes(1);

  let refresh;
  act(() => { refresh = client.refetchQueries({ queryKey: userKeys.pendingRegistrations }); });
  await flush();
  await act(async () => { retry.reject(new Error("pending failed")); await refresh; });
  await flush();

  expect(mocks.listPendingRegistrations).toHaveBeenCalledTimes(2);
  expect(mocks.toast.error).toHaveBeenCalledTimes(1);
});
