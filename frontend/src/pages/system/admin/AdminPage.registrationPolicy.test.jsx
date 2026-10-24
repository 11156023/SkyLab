// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AdminPage from "./AdminPage";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  getRegistrationPolicy: vi.fn(),
  saveRegistrationPolicy: vi.fn(),
  listPendingRegistrations: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/users", () => ({
  UsersService: {
    listAll: mocks.listAll,
    getRegistrationPolicy: mocks.getRegistrationPolicy,
    saveRegistrationPolicy: mocks.saveRegistrationPolicy,
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
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => null }));

let root;
let host;

/* TanStack Query 以 macrotask 批次通知，先等一個 timer tick 再看畫面 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

/* 註冊設定的勾選框沒有 name，用標籤文字找（測試裡 t 直接回 key） */
function policyCheckbox(key) {
  const label = [...host.querySelectorAll("label")].find((el) => el.textContent.includes(key));
  return label?.querySelector("input[type='checkbox']") ?? null;
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.listAll.mockResolvedValue([]);
  mocks.listPendingRegistrations.mockResolvedValue({ data: [] });
  mocks.saveRegistrationPolicy.mockImplementation(async (payload) => payload);
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("AdminPage registration settings", () => {
  test("the Google first-login switch is saved on its own, leaving email signup untouched", async () => {
    mocks.getRegistrationPolicy.mockResolvedValue({
      signup_enabled: false, approval_required: true, google_signup_enabled: false,
    });

    await act(async () => { root.render(withQueryClient(<AdminPage />)); });
    await flush();

    const box = policyCheckbox("AdminPage.googleSignupEnabled");
    expect(box).toBeTruthy();
    expect(box.checked).toBe(false);

    await act(async () => { box.click(); });
    await flush();

    expect(mocks.saveRegistrationPolicy).toHaveBeenCalledWith({
      signup_enabled: false, approval_required: true, google_signup_enabled: true,
    });
    expect(policyCheckbox("AdminPage.googleSignupEnabled").checked).toBe(true);
    expect(policyCheckbox("AdminPage.signupEnabled").checked).toBe(false);
  });
});
