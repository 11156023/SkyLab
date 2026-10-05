// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AiApiReviewPage from "./AiApiReviewPage";

const mocks = vi.hoisted(() => ({
  listAllRequests: vi.fn(),
  reviewRequest: vi.fn(),
  bulkRejectRequests: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/aiApi", () => ({
  AiApiService: {
    listAllRequests: mocks.listAllRequests,
    reviewRequest: mocks.reviewRequest,
    bulkRejectRequests: mocks.bulkRejectRequests,
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
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

    await act(async () => { root.render(<AiApiReviewPage />); });
    await act(async () => {
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });

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
    const flush = async () => {
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    };

    await act(async () => { root.render(<AiApiReviewPage />); });
    const pendingBatch = calls.splice(0);
    expect(pendingBatch).toHaveLength(4);

    await act(async () => { host.querySelector('[data-tab="approved"]').click(); });
    const approvedBatch = calls.splice(0);
    expect(approvedBatch.find(({ params }) => params?.status === "approved").params.limit).toBe(100);

    await act(async () => { respond(approvedBatch); await flush(); });
    expect(host.textContent).toContain("key-ok1");

    await act(async () => { respond(pendingBatch); await flush(); });

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
    const flush = async () => {
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    };

    await act(async () => { root.render(<AiApiReviewPage />); });
    const pendingBatch = calls.splice(0);

    await act(async () => { host.querySelector('[data-tab="approved"]').click(); });
    const approvedBatch = calls.splice(0);

    await act(async () => { respond(pendingBatch); await flush(); });
    expect(host.querySelector('[data-testid="loading"]')).not.toBeNull();

    await act(async () => { respond(approvedBatch); await flush(); });
    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(host.textContent).toContain("key-ok1");
  });

  test("selects pending requests and submits one shared reason for bulk rejection", async () => {
    const pendingRows = [
      request("p1", "pending", "2026-09-01T00:00:00Z"),
      request("p2", "pending", "2026-09-02T00:00:00Z"),
    ];
    mocks.listAllRequests.mockImplementation(async ({ status } = {}) => {
      if (status === "pending") return { data: pendingRows, count: pendingRows.length };
      return { data: [], count: 0 };
    });
    mocks.bulkRejectRequests.mockResolvedValue({ count: 2 });

    const flush = async () => {
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    };

    await act(async () => { root.render(<AiApiReviewPage />); await flush(); });
    const selectAll = host.querySelector('input[aria-label="AiApiReviewPage.selectAll"]');
    expect(selectAll).not.toBeNull();

    await act(async () => { selectAll.click(); });
    const bulkButton = [...host.querySelectorAll("button")]
      .find((button) => button.textContent.includes("AiApiReviewPage.bulkReject"));
    expect(bulkButton).not.toBeUndefined();

    await act(async () => { bulkButton.click(); });
    const textarea = host.querySelector("textarea");
    expect(textarea).not.toBeNull();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLTextAreaElement.prototype,
        "value",
      ).set;
      setter.call(textarea, "用途與申請內容不符");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    const confirmButton = [...host.querySelectorAll("button")]
      .find((button) => button.textContent.includes("AiApiReviewPage.bulkRejectConfirm"));
    expect(confirmButton).not.toBeUndefined();

    await act(async () => { confirmButton.click(); await flush(); });
    expect(mocks.bulkRejectRequests).toHaveBeenCalledWith(["p1", "p2"], "用途與申請內容不符");
  });
});
