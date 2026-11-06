// @vitest-environment happy-dom

/*
 * 2026-11-06 ntub 實測：新增使用者對話框填了一半按「取消」，馬上再按「新增使用者」，
 * 上一次填的 Email／姓名還在表單裡。原因是 useDialogPresence 的 150ms 離場動畫還沒播完，
 * 同一個 UserModal 實例被沿用；修法是每次開啟換 key，讓表單重掛載。
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AdminPage from "./AdminPage";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  getRegistrationPolicy: vi.fn(),
  listPendingRegistrations: vi.fn(),
  confirm: vi.fn(),
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
/* 「新增使用者」按鈕在 PageHeader 的 actions 裡，這裡要用真的 PageHeader */
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));

let root;
let host;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.getRegistrationPolicy.mockResolvedValue({ signup_enabled: true, approval_required: false, google_signup_enabled: true });
  mocks.listPendingRegistrations.mockResolvedValue({ data: [] });
  mocks.listAll.mockResolvedValue([
    { id: "me", email: "me@example.edu", full_name: "user-me", role: "admin", is_active: true, created_at: "2026-09-01T00:00:00Z" },
  ]);
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const buttonByText = (text) => [...host.querySelectorAll("button")].find((button) => button.textContent.includes(text));
const dialog = () => document.body.querySelector("[role='dialog']");
const emailInput = () => dialog().querySelector('input[type="email"]');

function typeInto(input, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("AdminPage：新增使用者對話框取消後立刻重開", () => {
  test("離場動畫還沒結束就再按「新增使用者」，表單仍是空的", async () => {
    await act(async () => { root.render(withQueryClient(<AdminPage />)); });
    await flush();

    await act(async () => { buttonByText("AdminPage.addUser").click(); });
    expect(dialog()).toBeTruthy();
    await act(async () => typeInto(emailInput(), "half-typed@example.edu"));
    expect(emailInput().value).toBe("half-typed@example.edu");

    /* Esc 關閉（共用 Modal 的關閉路徑），不等 150ms 離場動畫就重開 */
    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
    await act(async () => { buttonByText("AdminPage.addUser").click(); });

    expect(dialog()).toBeTruthy();
    expect(emailInput().value).toBe("");
  });

  test("等離場動畫結束再重開，表單一樣是空的（原本就正確，確認沒被改壞）", async () => {
    await act(async () => { root.render(withQueryClient(<AdminPage />)); });
    await flush();

    await act(async () => { buttonByText("AdminPage.addUser").click(); });
    await act(async () => typeInto(emailInput(), "half-typed@example.edu"));
    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 200)); });
    expect(dialog()).toBeNull();

    await act(async () => { buttonByText("AdminPage.addUser").click(); });
    expect(emailInput().value).toBe("");
  });
});
