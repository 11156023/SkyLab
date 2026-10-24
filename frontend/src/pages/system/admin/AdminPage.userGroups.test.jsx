// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AdminPage from "./AdminPage";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  getRegistrationPolicy: vi.fn(),
  listPendingRegistrations: vi.fn(),
  groupList: vi.fn(),
  groupMembers: vi.fn(),
  groupDelete: vi.fn(),
  addMembers: vi.fn(),
  confirm: vi.fn(),
  t: (key, opts) => (opts && typeof opts.count === "number" ? `${key}:${opts.count}` : key),
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock("../../../services/users", () => ({
  UsersService: {
    listAll: mocks.listAll,
    getRegistrationPolicy: mocks.getRegistrationPolicy,
    listPendingRegistrations: mocks.listPendingRegistrations,
  },
}));
vi.mock("../../../services/userGroups", () => ({
  UserGroupsService: {
    list: mocks.groupList,
    members: mocks.groupMembers,
    delete: mocks.groupDelete,
    addMembers: mocks.addMembers,
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "me" } }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));

let root;
let host;

/* TanStack Query 以 macrotask 批次通知，先等一個 timer tick 再看畫面 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

const user = (id) => ({
  id, email: `${id}@example.edu`, full_name: `user-${id}`, role: "student", is_active: true, created_at: "2026-09-01T00:00:00Z",
});
const group = (id, memberCount) => ({ id, name: `group-${id}`, member_count: memberCount, student_count: memberCount, created_at: "2026-09-01T00:00:00Z" });

function listedNames() {
  /* 名字與 Email 在 textContent 裡相連（user-aa@…），逐一比對完整的 Email 認列 */
  return [...host.querySelectorAll('[data-guide="admin-user-row"]')]
    .map((row) => ["a", "b", "c"].find((id) => row.textContent.includes(`user-${id}${id}@example.edu`)))
    .map((id) => `user-${id}`);
}

function filterSelect() {
  return host.querySelector('select[aria-label="UserGroups.filterAria"]');
}

async function choose(select, value) {
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
}

function button(text, scope = document) {
  return [...scope.querySelectorAll("button")].find((el) => el.textContent.includes(text) || el.getAttribute("aria-label") === text);
}

async function click(el) {
  await act(async () => { el.click(); });
  await flush();
}

async function render() {
  await act(async () => { root.render(withQueryClient(<AdminPage />)); });
  await flush();
}

beforeEach(() => {
  vi.clearAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  mocks.listAll.mockResolvedValue([user("a"), user("b"), user("c")]);
  mocks.getRegistrationPolicy.mockResolvedValue({ signup_enabled: false, approval_required: false, google_signup_enabled: false });
  mocks.listPendingRegistrations.mockResolvedValue({ data: [], count: 0 });
  mocks.groupList.mockResolvedValue({ data: [group("g1", 2)], count: 1 });
  mocks.groupMembers.mockResolvedValue({ data: [{ id: "a" }, { id: "c" }], count: 2 });
  mocks.confirm.mockResolvedValue(true);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  document.body.innerHTML = "";
});

describe("AdminPage user groups", () => {
  test("choosing a group lists only its members, and All groups lists everyone again", async () => {
    await render();
    expect(listedNames()).toEqual(["user-a", "user-b", "user-c"]);

    await choose(filterSelect(), "g1");
    expect(mocks.groupMembers).toHaveBeenCalledWith("g1");
    expect(listedNames()).toEqual(["user-a", "user-c"]);

    await choose(filterSelect(), "");
    expect(listedNames()).toEqual(["user-a", "user-b", "user-c"]);
  });

  test("deleting the group being filtered goes back to every user", async () => {
    await render();
    await choose(filterSelect(), "g1");
    expect(listedNames()).toEqual(["user-a", "user-c"]);

    mocks.groupDelete.mockResolvedValue({ message: "ok" });
    mocks.groupList.mockResolvedValue({ data: [], count: 0 });
    await click(button("UserGroups.manage", host));
    await click(button("UserGroups.deleteAria"));

    expect(mocks.groupDelete).toHaveBeenCalledWith("g1");
    expect(listedNames()).toEqual(["user-a", "user-b", "user-c"]);
    expect(filterSelect()).toBeNull();
  });

  test("a members load failure is reported once and the filter is cleared", async () => {
    mocks.groupMembers.mockRejectedValue(new Error("members down"));
    await render();
    await choose(filterSelect(), "g1");

    expect(mocks.toast.error).toHaveBeenCalledWith("members down");
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
    expect(filterSelect().value).toBe("");
    expect(listedNames()).toEqual(["user-a", "user-b", "user-c"]);
  });

  test("adding the ticked users to a group sends their ids and refreshes the group counts", async () => {
    mocks.addMembers.mockResolvedValue({ added: 1, removed: 0, not_found: [], group: group("g1", 3) });
    await render();
    const listCalls = mocks.groupList.mock.calls.length;

    const rowB = [...host.querySelectorAll('[data-guide="admin-user-row"]')].find((row) => row.textContent.includes("user-b"));
    await click(rowB.querySelector('input[type="checkbox"]'));
    await click(button("UserGroups.batchAdd", host));
    const dialog = document.querySelector('[role="dialog"]');
    await click(button("UserGroups.addSubmit", dialog));

    expect(mocks.addMembers).toHaveBeenCalledWith("g1", { userIds: ["b"] });
    expect(mocks.toast.success).toHaveBeenCalledWith("UserGroups.addedToast:1");
    expect(mocks.groupList.mock.calls.length).toBeGreaterThan(listCalls);
  });
});
