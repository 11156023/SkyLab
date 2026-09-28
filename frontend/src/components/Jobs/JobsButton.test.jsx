// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import JobsButton from "./JobsButton";

const confirmLeave = vi.fn();
const markReminderRead = vi.fn();

vi.mock("../../contexts/UnsavedChangesContext", () => ({ useUnsavedChanges: () => ({ confirmLeave }) }));
vi.mock("./JobsProvider", () => ({
  useJobs: () => ({
    items: [],
    isAdmin: false,
    notifyOnlyMine: false,
    setNotifyOnlyMine: vi.fn(),
    openJob: vi.fn(),
    reminders: [{ id: "r1", title: "申請已核准", target: "/my-requests", tone: "info" }],
    readReminderIds: [],
    refreshReminders: vi.fn(),
    markReminderRead,
    markAllRemindersRead: vi.fn(),
    desktopNotifications: { supported: false, sync: vi.fn(), permission: "default" },
  }),
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal()),
  useTranslation: () => ({ t: (key) => key }),
}));

let host;
let root;

function Path() {
  return <span data-testid="path">{useLocation().pathname}</span>;
}

beforeEach(async () => {
  vi.clearAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => {
    root.render(<MemoryRouter initialEntries={["/admin/ldap"]}><Path /><JobsButton /></MemoryRouter>);
  });
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

async function clickReminder() {
  await act(async () => { document.querySelector('button[aria-label="JobsButton.backgroundJobs"]').click(); });
  const row = [...document.querySelectorAll("button")].find((b) => b.textContent.includes("申請已核准"));
  await act(async () => { row.click(); });
}

const path = () => document.querySelector('[data-testid="path"]').textContent;

test("a reminder click stays on the page when leaving is declined", async () => {
  confirmLeave.mockResolvedValue(false);
  await clickReminder();
  expect(confirmLeave).toHaveBeenCalledTimes(1);
  expect(markReminderRead).toHaveBeenCalledWith("r1");
  expect(path()).toBe("/admin/ldap");
});

test("a reminder click navigates once leaving is confirmed", async () => {
  confirmLeave.mockResolvedValue(true);
  await clickReminder();
  expect(path()).toBe("/my-requests");
});
