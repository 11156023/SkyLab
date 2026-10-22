// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
import AiApiReviewPage from "./AiApiReviewPage";

const mocks = vi.hoisted(() => ({
  listAllRequests: vi.fn(),
  reviewRequest: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/aiApi", () => ({
  AiApiService: {
    listAllRequests: mocks.listAllRequests,
    reviewRequest: mocks.reviewRequest,
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/useDialogPresence", () => ({
  default: (open) => ({ open, closing: false }),
}));
vi.mock("../../../components/Modal/Modal", () => ({
  default: ({ title, description, children, actions }) => (
    <div role="dialog">
      <h2>{title}</h2>
      {description && <p>{description}</p>}
      {children}
      <div>{actions}</div>
    </div>
  ),
}));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/SegmentedControl/SegmentedControl", () => ({
  default: ({ options = [], value, onChange }) => (
    <div>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          data-tab={option.value}
          data-badge={option.badge}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  ),
}));

function request(id, status, createdAt) {
  return {
    id,
    status,
    user_email: `${id}@example.edu`,
    api_key_name: `key-${id}`,
    purpose: "課堂作業",
    created_at: createdAt,
    reviewed_at: null,
  };
}

let root;
let host;

/* TanStack Query 的通知排在 setTimeout(0)：先讓回應的 microtask 跑完（通知的 timer 才會先排進去），
   再等 timer tick 與 microtask；跑兩輪，涵蓋「回應 → 通知 → 重繪 → 下一個查詢」 */
async function settle() {
  for (let round = 0; round < 2; round += 1) {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
}

const renderPage = () => root.render(withQueryClient(<AiApiReviewPage />));

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("AiApiReviewPage", () => {
  test("pending tab loads pending requests with a server-side status filter", async () => {
    const newest = Array.from({ length: 100 }, (_, i) => request(`a${i}`, "approved", "2026-09-20T00:00:00Z"));
    const oldPending = request("old", "pending", "2026-01-01T00:00:00Z");
    mocks.listAllRequests.mockImplementation(async ({ status } = {}) => {
      if (status === "pending") return { data: [oldPending], count: 1 };
      if (status === "approved") return { data: newest.slice(0, 1), count: 100 };
      if (status === "rejected") return { data: [], count: 0 };
      return { data: newest, count: 101 };
    });

    await act(async () => { renderPage(); });
    await act(settle);

    expect(mocks.listAllRequests).toHaveBeenCalledWith({ status: "pending", limit: 100 });
    expect(host.textContent).toContain("key-old");
    expect(host.querySelector('[data-tab="pending"]').dataset.badge).toBe("1");
    expect(host.querySelector('[data-tab="approved"]').dataset.badge).toBe("100");
    expect(host.querySelector('[data-tab="all"]').dataset.badge).toBe("101");
  });

  test("a late response from the previous tab does not overwrite the current tab", async () => {
    const pendingRow = request("p1", "pending", "2026-09-01T00:00:00Z");
    const approvedRow = request("ok1", "approved", "2026-09-02T00:00:00Z");
    const calls = [];
    mocks.listAllRequests.mockImplementation((params) => new Promise((resolve) => {
      calls.push({ params, resolve });
    }));
    const respond = (batch) => batch.forEach(({ params, resolve }) => {
      if (params?.status === "pending") resolve({ data: [pendingRow], count: 1 });
      else if (params?.status === "approved") resolve({ data: [approvedRow], count: 1 });
      else if (params?.status === "rejected") resolve({ data: [], count: 0 });
      else resolve({ data: [approvedRow, pendingRow], count: 2 });
    });

    await act(async () => { renderPage(); });
    const pendingBatch = calls.splice(0);
    expect(pendingBatch).toHaveLength(4);

    await act(async () => { host.querySelector('[data-tab="approved"]').click(); });
    const approvedBatch = calls.splice(0);
    /* 只有筆數上限變了的兩個分頁換 key 重抓（待審 100→1、已核准 1→100）；其餘兩個沿用原查詢 */
    expect(approvedBatch).toHaveLength(2);
    expect(approvedBatch.find(({ params }) => params?.status === "approved").params.limit).toBe(100);

    await act(async () => { respond(approvedBatch); });
    await act(settle);
    expect(host.textContent).toContain("key-ok1");

    await act(async () => { respond(pendingBatch); });
    await act(settle);

    expect(host.textContent).toContain("key-ok1");
    expect(host.textContent).not.toContain("key-p1");
    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
  });

  test("a stale response from the previous tab does not clear the loading state of the current tab", async () => {
    const approvedRow = request("ok1", "approved", "2026-09-02T00:00:00Z");
    const calls = [];
    mocks.listAllRequests.mockImplementation((params) => new Promise((resolve) => {
      calls.push({ params, resolve });
    }));
    const respond = (batch) => batch.forEach(({ params, resolve }) => {
      if (params?.status === "approved") resolve({ data: [approvedRow], count: 1 });
      else resolve({ data: [], count: 0 });
    });

    await act(async () => { renderPage(); });
    const pendingBatch = calls.splice(0);

    await act(async () => { host.querySelector('[data-tab="approved"]').click(); });
    const approvedBatch = calls.splice(0);

    await act(async () => { respond(pendingBatch); });
    await act(settle);
    expect(host.querySelector('[data-testid="loading"]')).not.toBeNull();

    await act(async () => { respond(approvedBatch); });
    await act(settle);
    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(host.textContent).toContain("key-ok1");
  });

  test("does not render bulk rejection controls", async () => {
    const pendingRows = [request("p1", "pending", "2026-09-01T00:00:00Z")];
    mocks.listAllRequests.mockImplementation(async ({ status } = {}) => {
      if (status === "pending") return { data: pendingRows, count: pendingRows.length };
      return { data: [], count: 0 };
    });

    await act(async () => { renderPage(); });
    await act(settle);

    expect(host.textContent).toContain("key-p1");
    expect(host.querySelectorAll('input[type="checkbox"]')).toHaveLength(0);
    expect(host.textContent).not.toContain("AiApiReviewPage.bulkReject");
  });

  test("a failed first load shows a retry instead of an empty list and toasts once", async () => {
    const pendingRow = request("p1", "pending", "2026-09-01T00:00:00Z");
    let failing = true;
    mocks.listAllRequests.mockImplementation(async ({ status } = {}) => {
      if (failing) throw new Error("gateway down");
      return status === "pending" ? { data: [pendingRow], count: 1 } : { data: [], count: 0 };
    });

    await act(async () => { renderPage(); });
    await act(settle);
    expect(host.textContent).toContain("Error.retry");
    expect(host.textContent).not.toContain("AiApiReviewPage.emptyTitle");
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
    expect(mocks.toast.error).toHaveBeenCalledWith("gateway down");

    failing = false;
    const retry = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("Error.retry"));
    await act(async () => { retry.click(); });
    await act(settle);
    expect(host.textContent).toContain("key-p1");
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
  });

  test("after a tab switch fails, a background retry keeps the error instead of flashing back to loading", async () => {
    /* 換分頁後新 key 失敗：TanStack v5 沒資料時重抓會把 status 改回 pending，還會把上一個 key 的
       佔位資料放回來；錯誤畫面要維持到這次的結果，不能閃成載入中，也不重複提醒 */
    const pendingRow = request("p1", "pending", "2026-09-01T00:00:00Z");
    const okResponse = (status) => (status === "pending" ? { data: [pendingRow], count: 1 } : { data: [], count: 0 });
    mocks.listAllRequests.mockImplementation(async ({ status, limit } = {}) => {
      if (status === "approved" && limit === 100) throw new Error("approved down");
      return okResponse(status);
    });
    const client = createTestQueryClient();

    await act(async () => { root.render(withQueryClient(<AiApiReviewPage />, client)); });
    await act(settle);
    expect(host.textContent).toContain("key-p1");

    await act(async () => { host.querySelector('[data-tab="approved"]').click(); });
    await act(settle);
    expect(host.textContent).toContain("Error.retry");
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);

    let reject;
    mocks.listAllRequests.mockImplementation(({ status, limit } = {}) => {
      if (status === "approved" && limit === 100) return new Promise((_resolve, fail) => { reject = fail; });
      return Promise.resolve(okResponse(status));
    });
    act(() => { client.refetchQueries({ queryKey: ["aiApi", "allRequests"] }); });
    await act(settle);
    /* 重抓還在途中：錯誤畫面照樣在 */
    expect(reject).toBeTypeOf("function");
    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(host.textContent).toContain("Error.retry");

    await act(async () => reject(new Error("approved down")));
    await act(settle);
    expect(host.textContent).toContain("Error.retry");
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
  });
});
