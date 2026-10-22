// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import QuotasSection from "./QuotasSection";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  getGlobal: vi.fn(),
  remove: vi.fn(),
  listAll: vi.fn(),
  confirm: vi.fn(),
  /* t 要是穩定的參照：載入函式依賴 t，每次 render 換新會無限重抓 */
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/quotas", () => ({
  QuotasService: { list: mocks.list, getGlobal: mocks.getGlobal, remove: mocks.remove },
}));
vi.mock("../../../services/users", () => ({ UsersService: { listAll: mocks.listAll } }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

const GLOBAL = { max_cpu_cores: 8, max_memory_mb: 16384, max_disk_gb: 100, max_instances: 5 };

let host;
let root;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.list.mockResolvedValue([
    { id: "q1", user_id: "u1", user_email: "a@example.edu", ...GLOBAL },
  ]);
  /* 每次重抓都回新物件，跟真實 API 一樣 */
  mocks.getGlobal.mockImplementation(async () => ({ ...GLOBAL }));
  mocks.remove.mockResolvedValue(undefined);
  mocks.listAll.mockResolvedValue([]);
  mocks.confirm.mockResolvedValue(true);
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
    /* TanStack Query 以 macrotask 批次通知，先等一個 timer tick */
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  });
}

async function typeInto(input, value) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

test("刪除個人覆寫後重抓全域設定，全域卡片尚未儲存的修改不會被洗掉", async () => {
  await act(async () => root.render(withQueryClient(<QuotasSection />)));
  await flush();

  const cpu = host.querySelector("#global-max_cpu_cores");
  await typeInto(cpu, "16");
  expect(cpu.value).toBe("16");

  const deleteBtn = host.querySelector('button[aria-label="QuotasTab.deleteQuotaTitle"]');
  await act(async () => deleteBtn.click());
  await flush();

  expect(mocks.remove).toHaveBeenCalledWith("q1");
  expect(mocks.getGlobal).toHaveBeenCalledTimes(2);
  expect(host.querySelector("#global-max_cpu_cores").value).toBe("16");
});

test("新增個人配額時挑不到管理員（管理員不受配額限制）", async () => {
  mocks.listAll.mockResolvedValue([
    { id: "s1", email: "student@example.edu", role: "student" },
    { id: "t1", email: "teacher@example.edu", role: "teacher" },
    { id: "a1", email: "admin@example.edu", role: "admin", is_superuser: true },
  ]);
  await act(async () => root.render(withQueryClient(<QuotasSection />)));
  await flush();

  const addBtn = [...host.querySelectorAll("button")].find((b) => b.textContent.includes("QuotasTab.addQuota"));
  await act(async () => addBtn.click());
  await flush();
  const picker = document.querySelector("#quota-user");
  await act(async () => picker.focus());
  await flush();

  const emails = [...document.querySelectorAll("li button")].map((b) => b.textContent);
  expect(emails).toEqual(["student@example.edu", "teacher@example.edu"]);
});

test("全域設定的數值真的變了才重設表單", async () => {
  await act(async () => root.render(withQueryClient(<QuotasSection />)));
  await flush();

  mocks.getGlobal.mockImplementation(async () => ({ ...GLOBAL, max_cpu_cores: 12 }));
  const deleteBtn = host.querySelector('button[aria-label="QuotasTab.deleteQuotaTitle"]');
  await act(async () => deleteBtn.click());
  await flush();

  expect(host.querySelector("#global-max_cpu_cores").value).toBe("12");
});
