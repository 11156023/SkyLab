// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import BatchReviewPage from "./BatchReviewPage";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  review: vi.fn(),
  confirm: vi.fn(),
  autoRefresh: null,
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/batchProvision", () => ({
  BatchProvisionService: { listAll: mocks.listAll, review: mocks.review },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: (refresh) => { mocks.autoRefresh = refresh; } }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("../../../components/LoadingState/LoadingState", () => ({ default: () => <div data-testid="loading" /> }));
vi.mock("../../../components/SegmentedControl/SegmentedControl", () => ({
  default: ({ options = [] }) => (
    <div>
      {options.map((option) => (
        <span key={option.value} data-tab={option.value} data-badge={option.badge} />
      ))}
    </div>
  ),
}));

function job(id, status, createdAt) {
  return {
    id,
    status,
    hostname_prefix: `prefix-${id}`,
    initiated_by_email: "teacher@example.edu",
    resource_type: "lxc",
    spec: { cores: 1, memory: 1024, rootfs_size: 8 },
    total: 1,
    done: 0,
    failed_count: 0,
    tasks: [],
    created_at: createdAt,
  };
}

let root;
let host;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function buttonByText(text) {
  return [...host.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

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

describe("BatchReviewPage", () => {
  test("pending batches older than the newest list window still show up", async () => {
    const recent = Array.from({ length: 200 }, (_, i) => job(`c${i}`, "completed", "2026-09-20T00:00:00Z"));
    const oldPending = job("old", "pending_review", "2026-01-01T00:00:00Z");
    mocks.listAll.mockImplementation(async (params = {}) => (
      params.status === "pending_review" ? [oldPending] : recent
    ));

    await act(async () => { root.render(<BatchReviewPage />); });
    await act(async () => {
      for (let i = 0; i < 8; i += 1) await Promise.resolve();
    });

    expect(mocks.listAll).toHaveBeenCalledWith(expect.objectContaining({ status: "pending_review" }));
    expect(host.textContent).toContain("prefix-old");
    expect(host.querySelector('[data-tab="pending"]').dataset.badge).toBe("1");
  });

  test("a background refresh sent before a review does not bring back the reviewed batch", async () => {
    const pendingJob = job("j1", "pending_review", "2026-09-20T00:00:00Z");
    const approvedJob = { ...pendingJob, status: "queued" };
    const stale = deferred();
    mocks.listAll
      .mockResolvedValueOnce([pendingJob])
      .mockResolvedValueOnce([pendingJob])
      // 審核前送出的背景刷新（兩個請求共用同一份慢回應）
      .mockReturnValueOnce(stale.promise)
      .mockReturnValueOnce(stale.promise)
      .mockResolvedValue([approvedJob]);
    mocks.confirm.mockResolvedValue(true);
    mocks.review.mockResolvedValue({});

    await act(async () => { root.render(<BatchReviewPage />); });
    await flush();
    expect(host.textContent).toContain("prefix-j1");

    let refresh;
    act(() => { refresh = mocks.autoRefresh(); });

    await act(async () => { buttonByText("prefix-j1").click(); });
    await act(async () => { buttonByText("BatchReviewPage.approve").click(); });
    await flush();
    expect(mocks.review).toHaveBeenCalledWith("j1", expect.objectContaining({ decision: "approved" }));
    expect(host.textContent).not.toContain("prefix-j1");

    await act(async () => { stale.resolve([pendingJob]); await refresh; });
    await flush();
    expect(host.textContent).not.toContain("prefix-j1");
  });

  test("a first-load failure still shows the error when a background refresh overtook it", async () => {
    const firstLoad = deferred();
    const background = deferred();
    mocks.listAll
      .mockReturnValueOnce(firstLoad.promise)
      .mockReturnValueOnce(firstLoad.promise)
      .mockReturnValueOnce(background.promise)
      .mockReturnValueOnce(background.promise);

    await act(async () => { root.render(<BatchReviewPage />); });
    expect(host.querySelector('[data-testid="loading"]')).toBeTruthy();

    let refresh;
    act(() => { refresh = mocks.autoRefresh(); });
    await act(async () => { background.reject(new Error("boom")); await refresh; });
    await flush();

    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(host.textContent).toContain("boom");

    await act(async () => { firstLoad.resolve([job("late", "pending_review", "2026-09-20T00:00:00Z")]); });
    await flush();
    expect(host.textContent).not.toContain("prefix-late");
  });
});
