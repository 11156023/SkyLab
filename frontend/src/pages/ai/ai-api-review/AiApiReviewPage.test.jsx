// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import AiApiReviewPage from "./AiApiReviewPage";

const mocks = vi.hoisted(() => ({
  listAllRequests: vi.fn(),
  reviewRequest: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/aiApi", () => ({
  AiApiService: { listAllRequests: mocks.listAllRequests, reviewRequest: mocks.reviewRequest },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
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
});
