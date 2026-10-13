// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
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
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
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

async function flush() {
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
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

  await act(async () => root.render(<AiApiKeysPage />));
  await flush();

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
