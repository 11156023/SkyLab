// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import AuditPage from "./AuditPage";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  stats: vi.fn(),
  actions: vi.fn(),
  users: vi.fn(),
  exportCsv: vi.fn(),
  confirm: vi.fn(),
  downloadBlob: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/auditLogs", () => ({
  AuditLogsService: {
    list: mocks.list,
    stats: mocks.stats,
    actions: mocks.actions,
    users: mocks.users,
    exportCsv: mocks.exportCsv,
  },
}));
vi.mock("../../../services/api", () => ({ downloadBlob: mocks.downloadBlob }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function statsWith(total) {
  return { total, danger: 0, login_failed: 0, active_users: 0 };
}

let host;
let root;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.list.mockResolvedValue({ data: [], count: 0 });
  mocks.actions.mockResolvedValue([]);
  mocks.users.mockResolvedValue([]);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function flush() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  });
}

async function setDate(input, value) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function shownTotal() {
  const label = [...host.querySelectorAll("span")].find((el) => el.textContent === "AuditPage.statTotal");
  return label?.nextElementSibling?.textContent;
}

test("連續改日期時，較晚回來的舊區間摘要不會蓋掉新區間的", async () => {
  const older = deferred();
  const newer = deferred();
  mocks.stats
    .mockResolvedValueOnce(statsWith(1))
    .mockReturnValueOnce(older.promise)
    .mockReturnValueOnce(newer.promise);

  await act(async () => root.render(<AuditPage />));
  await flush();
  expect(shownTotal()).toBe("1");

  const startInput = host.querySelector('input[type="date"]');
  await setDate(startInput, "2025-01-01");
  await setDate(startInput, "2025-02-01");
  expect(mocks.stats).toHaveBeenCalledTimes(3);

  await act(async () => newer.resolve(statsWith(30)));
  await flush();
  await act(async () => older.resolve(statsWith(999)));
  await flush();

  expect(shownTotal()).toBe("30");
});
