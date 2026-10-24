// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import JobsPage from "./JobsPage";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
import { jobKeys } from "../../../queries/jobs";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/jobs", () => ({ JobsService: { list: mocks.list } }));
vi.mock("../../../components/Jobs/JobDetailDialog", () => ({ default: () => null }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/ErrorState/ErrorState", () => ({
  default: ({ onRetry }) => <button type="button" data-testid="error" onClick={onRetry}>retry</button>,
}));
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
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function job(id, kind) {
  return { id, kind, title: `job-${id}`, status: "completed", progress: 100 };
}

let host;
let root;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
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
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

async function pickKind(kind) {
  await act(async () => host.querySelector(`[data-group="JobsPage.filterKind"] [data-value="${kind}"]`).click());
}

test("快速切換篩選時，較晚回來的舊條件結果不會蓋掉目前篩選的清單", async () => {
  const older = deferred();
  const newer = deferred();
  mocks.list
    .mockResolvedValueOnce({ items: [job("all", "vm_request")] })
    .mockReturnValueOnce(older.promise)
    .mockReturnValueOnce(newer.promise);

  await act(async () => root.render(withQueryClient(<JobsPage />)));
  await flush();

  await pickKind("vm_request");
  await pickKind("deletion");
  expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ kinds: ["deletion"] }));

  await act(async () => newer.resolve({ items: [job("del", "deletion")] }));
  await flush();
  await act(async () => older.resolve({ items: [job("vm", "vm_request")] }));
  await flush();

  expect(host.textContent).toContain("job-del");
  expect(host.textContent).not.toContain("job-vm");
  expect(host.querySelector('[data-testid="loading"]')).toBeNull();
});

test("換篩選期間先留著上一份清單，不閃回載入畫面", async () => {
  const pending = deferred();
  mocks.list
    .mockResolvedValueOnce({ items: [job("all", "vm_request")] })
    .mockReturnValueOnce(pending.promise);

  await act(async () => root.render(withQueryClient(<JobsPage />)));
  await flush();
  await pickKind("deletion");
  await flush();

  expect(host.querySelector('[data-testid="loading"]')).toBeNull();
  expect(host.textContent).toContain("job-all");

  await act(async () => pending.resolve({ items: [job("del", "deletion")] }));
  await flush();
  expect(host.textContent).toContain("job-del");
  expect(host.textContent).not.toContain("job-all");
});

test("換篩選失敗時跳 toast 並顯示錯誤狀態；背景重試又失敗不重複跳", async () => {
  mocks.list
    .mockResolvedValueOnce({ items: [job("all", "vm_request")] })
    .mockRejectedValueOnce(new Error("boom"))
    .mockRejectedValueOnce(new Error("boom again"));
  const client = createTestQueryClient();

  await act(async () => root.render(withQueryClient(<JobsPage />, client)));
  await flush();
  await pickKind("deletion");
  await flush();

  expect(mocks.toast.error).toHaveBeenCalledTimes(1);
  expect(mocks.toast.error).toHaveBeenCalledWith("boom");
  expect(host.querySelector('[data-testid="error"]')).not.toBeNull();
  /* 錯誤時不能顯示成「沒有結果」 */
  expect(host.textContent).not.toContain("JobsPage.emptyNoResult");

  await act(async () => { await client.refetchQueries({ queryKey: jobKeys.list({ limit: 200, kinds: ["deletion"] }) }); });
  await flush();
  expect(mocks.list).toHaveBeenCalledTimes(3);
  expect(mocks.toast.error).toHaveBeenCalledTimes(1);
});

test("背景刷新失敗沿用現有清單、不跳 toast", async () => {
  mocks.list
    .mockResolvedValueOnce({ items: [job("all", "vm_request")] })
    .mockRejectedValueOnce(new Error("503"));
  const client = createTestQueryClient();

  await act(async () => root.render(withQueryClient(<JobsPage />, client)));
  await flush();
  await act(async () => { await client.refetchQueries({ queryKey: jobKeys.list({ limit: 200 }) }); });
  await flush();

  expect(mocks.list).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain("job-all");
  expect(mocks.toast.error).not.toHaveBeenCalled();
});
