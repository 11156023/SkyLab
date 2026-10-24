// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
import AiApiKeysPage from "./AiApiKeysPage";

const mocks = vi.hoisted(() => ({
  listAllCredentials: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/aiApi", () => ({
  AiApiService: { listAllCredentials: mocks.listAllCredentials, deleteCredential: vi.fn() },
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/SegmentedControl/SegmentedControl", () => ({
  default: ({ options = [], onChange, ariaLabel }) => (
    <div data-group={ariaLabel}>
      {options.map((option) => (
        <button key={option.value} type="button" data-value={option.value} onClick={() => onChange(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  ),
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

function page(name, status) {
  return { data: [{ id: name, api_key_name: name, status }], count: 1 };
}

let host;
let root;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
});

/* setTimeout 是假的（搜尋框 debounce）：TanStack Query 的通知排在 setTimeout(0)，要推進假時鐘 0ms 才會送出 */
async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

async function pickStatus(status) {
  await act(async () => host.querySelector(`[data-group="AiApiKeysPage.statusTabsLabel"] [data-value="${status}"]`).click());
}

test("快速切換狀態分頁時，較晚回來的舊分頁結果不會蓋掉目前分頁的清單", async () => {
  const older = deferred();
  const newer = deferred();
  mocks.listAllCredentials
    .mockResolvedValueOnce(page("key-active", "active"))
    .mockReturnValueOnce(older.promise)
    .mockReturnValueOnce(newer.promise);

  await act(async () => root.render(withQueryClient(<AiApiKeysPage />, createTestQueryClient())));
  await flush();
  expect(host.textContent).toContain("key-active");

  await pickStatus("inactive");
  await pickStatus("all");
  expect(mocks.listAllCredentials).toHaveBeenLastCalledWith(expect.objectContaining({ status: undefined }));

  await act(async () => newer.resolve(page("key-all", "active")));
  await flush();
  await act(async () => older.resolve(page("key-inactive", "inactive")));
  await flush();

  expect(host.textContent).toContain("key-all");
  expect(host.textContent).not.toContain("key-inactive");
  expect(host.querySelector('[data-testid="loading"]')).toBeNull();
});

test("換分頁後這組條件載入失敗，背景重抓期間維持錯誤畫面，不閃回載入中或上一組條件的清單", async () => {
  /* TanStack v5 沒有資料時重抓會把 status 改回 pending，還會把上一組條件的清單放回來當佔位資料；
     錯誤畫面要維持到這次重抓的結果，同一個錯誤也不重複提示 */
  mocks.listAllCredentials
    .mockResolvedValueOnce(page("key-active", "active"))
    .mockRejectedValueOnce(new Error("inactive down"));
  const client = createTestQueryClient();

  await act(async () => root.render(withQueryClient(<AiApiKeysPage />, client)));
  await flush();
  expect(host.textContent).toContain("key-active");

  await pickStatus("inactive");
  await flush();
  expect(host.textContent).toContain("Error.retry");
  expect(host.textContent).not.toContain("key-active");
  expect(mocks.toast.error).toHaveBeenCalledTimes(1);

  let reject;
  mocks.listAllCredentials.mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail; }));
  act(() => { client.refetchQueries({ queryKey: ["aiApi", "allCredentials"], type: "active" }); });
  await flush();
  /* 重抓還在途中：錯誤畫面照樣在 */
  expect(reject).toBeTypeOf("function");
  expect(host.querySelector('[data-testid="loading"]')).toBeNull();
  expect(host.textContent).not.toContain("key-active");
  expect(host.textContent).toContain("Error.retry");

  await act(async () => reject(new Error("inactive down")));
  await flush();
  expect(host.textContent).toContain("Error.retry");
  expect(mocks.toast.error).toHaveBeenCalledTimes(1);
});
