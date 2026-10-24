// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import AuditPage from "./AuditPage";
import { withQueryClient } from "../../../queries/testing";

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

/* TanStack Query 以 macrotask 批次通知，先等一個 timer tick 再看畫面 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
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

  await act(async () => root.render(withQueryClient(<AuditPage />)));
  await flush();
  expect(shownTotal()).toBe("1");

  const startInput = host.querySelector('input[type="date"]');
  await setDate(startInput, "2025-01-01");
  await setDate(startInput, "2025-02-01");
  expect(mocks.stats).toHaveBeenCalledTimes(3);
  /* 新區間還沒回來前先留著上一個區間的數字，不閃掉 */
  await flush();
  expect(shownTotal()).toBe("1");

  await act(async () => newer.resolve(statsWith(30)));
  await flush();
  await act(async () => older.resolve(statsWith(999)));
  await flush();

  expect(shownTotal()).toBe("30");
});

test("摘要抓不到就把卡片收掉", async () => {
  mocks.stats
    .mockResolvedValueOnce(statsWith(1))
    .mockRejectedValueOnce(new Error("503"));

  await act(async () => root.render(withQueryClient(<AuditPage />)));
  await flush();
  expect(shownTotal()).toBe("1");

  await setDate(host.querySelector('input[type="date"]'), "2025-01-01");
  await flush();
  expect(shownTotal()).toBeUndefined();
});

test("換篩選時保留上一組清單（半透明）直到新資料回來，較晚回來的舊條件結果不會蓋掉", async () => {
  const older = deferred();
  const newer = deferred();
  const row = (id) => ({ id, created_at: "2026-09-01T00:00:00Z", action: `act-${id}`, details: `detail-${id}` });
  mocks.stats.mockResolvedValue(statsWith(0));
  mocks.actions.mockResolvedValue([{ value: "a1" }, { value: "a2" }]);
  mocks.list
    .mockResolvedValueOnce({ data: [row("first")], count: 1 })
    .mockReturnValueOnce(older.promise)
    .mockReturnValueOnce(newer.promise);

  await act(async () => root.render(withQueryClient(<AuditPage />)));
  await flush();
  expect(host.textContent).toContain("detail-first");

  const actionSelect = host.querySelector("select");
  await act(async () => {
    actionSelect.value = "a1";
    actionSelect.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
  expect(host.textContent).toContain("detail-first");
  expect(host.querySelector('[aria-busy="true"]')).toBeTruthy();

  await act(async () => {
    actionSelect.value = "a2";
    actionSelect.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => newer.resolve({ data: [row("newer")], count: 1 }));
  await flush();
  await act(async () => older.resolve({ data: [row("older")], count: 1 }));
  await flush();

  expect(host.textContent).toContain("detail-newer");
  expect(host.textContent).not.toContain("detail-older");
  expect(host.querySelector('[aria-busy="true"]')).toBeNull();
});
