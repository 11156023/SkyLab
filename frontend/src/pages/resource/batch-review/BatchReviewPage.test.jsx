// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import BatchReviewPage from "./BatchReviewPage";
import { batchProvisionKeys } from "../../../queries/batchProvision";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  review: vi.fn(),
  getRecurrencePreview: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/batchProvision", () => ({
  BatchProvisionService: {
    listAll: mocks.listAll,
    review: mocks.review,
    getRecurrencePreview: mocks.getRecurrencePreview,
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
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
/* 每個測試自己的 QueryClient；背景刷新在測試裡用 refreshList() 觸發（原本是 useAutoRefresh 的回呼） */
let client;

function refreshList() {
  return client.refetchQueries({ queryKey: batchProvisionKeys.all });
}

async function renderPage() {
  await act(async () => { root.render(withQueryClient(<BatchReviewPage />, client)); });
}

async function flush() {
  await act(async () => {
    /* TanStack Query 以 macrotask 批次通知，先等一個 timer tick */
    await new Promise((resolve) => setTimeout(resolve, 0));
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
  client = createTestQueryClient();
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

    await renderPage();
    await flush();

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

    await renderPage();
    await flush();
    expect(host.textContent).toContain("prefix-j1");

    let refresh;
    act(() => { refresh = refreshList(); });

    await act(async () => { buttonByText("prefix-j1").click(); });
    await act(async () => { buttonByText("BatchReviewPage.approve").click(); });
    await flush();
    expect(mocks.review).toHaveBeenCalledWith("j1", expect.objectContaining({ decision: "approved" }));
    expect(host.textContent).not.toContain("prefix-j1");

    await act(async () => { stale.resolve([pendingJob]); await refresh; });
    await flush();
    expect(host.textContent).not.toContain("prefix-j1");
  });

  test("a background refresh during the first load joins it instead of firing a second request", async () => {
    const firstLoad = deferred();
    mocks.listAll.mockReturnValue(firstLoad.promise);

    await renderPage();
    expect(host.querySelector('[data-testid="loading"]')).toBeTruthy();
    expect(mocks.listAll).toHaveBeenCalledTimes(2);

    let refresh;
    act(() => { refresh = refreshList(); });
    expect(mocks.listAll).toHaveBeenCalledTimes(2);

    await act(async () => { firstLoad.resolve([job("late", "pending_review", "2026-09-20T00:00:00Z")]); await refresh; });
    await flush();
    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(host.textContent).toContain("prefix-late");
  });

  test("a failed first load shows the error; a failed background refresh keeps the list", async () => {
    mocks.listAll.mockRejectedValue(new Error("boom"));

    await renderPage();
    await flush();
    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(host.textContent).toContain("boom");

    mocks.listAll.mockResolvedValue([job("j1", "pending_review", "2026-09-20T00:00:00Z")]);
    await act(async () => { buttonByText("BatchReviewPage.retry").click(); });
    await flush();
    expect(host.textContent).not.toContain("boom");
    expect(host.textContent).toContain("prefix-j1");

    mocks.listAll.mockRejectedValue(new Error("pve down"));
    await act(async () => { await refreshList(); });
    await flush();
    expect(host.textContent).not.toContain("pve down");
    expect(host.textContent).toContain("prefix-j1");
  });

  test("recurrence preview loads on demand once and re-expanding reuses it", async () => {
    mocks.listAll.mockResolvedValue([{
      ...job("r1", "pending_review", "2026-09-20T00:00:00Z"),
      recurrence_rule: "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=0",
    }]);
    const preview = deferred();
    mocks.getRecurrencePreview.mockReturnValue(preview.promise);

    await renderPage();
    await flush();
    await act(async () => { buttonByText("prefix-r1").click(); });
    await flush();
    expect(mocks.getRecurrencePreview).not.toHaveBeenCalled();

    await act(async () => { buttonByText("BatchReviewPage.recurPreviewToggle").click(); });
    await flush();
    expect(mocks.getRecurrencePreview).toHaveBeenCalledWith("r1", 5);
    expect(host.textContent).toContain("BatchReviewPage.previewLoading");

    await act(async () => {
      preview.resolve({ windows: [["2026-09-21T01:00:00Z", "2026-09-21T03:00:00Z"]] });
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    await flush();
    expect(host.textContent).not.toContain("BatchReviewPage.previewLoading");
    expect(host.querySelectorAll("ul li").length).toBeGreaterThan(0);

    // 收合再展開：沿用快取，不重抓
    await act(async () => { buttonByText("BatchReviewPage.recurPreviewToggle").click(); });
    await act(async () => { buttonByText("BatchReviewPage.recurPreviewToggle").click(); });
    await flush();
    expect(mocks.getRecurrencePreview).toHaveBeenCalledTimes(1);
  });

  test("a failed recurrence preview shows a toast and can be retried", async () => {
    mocks.listAll.mockResolvedValue([{
      ...job("r1", "pending_review", "2026-09-20T00:00:00Z"),
      recurrence_rule: "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=0",
    }]);
    mocks.getRecurrencePreview.mockRejectedValueOnce(new Error("preview down"));

    await renderPage();
    await flush();
    await act(async () => { buttonByText("prefix-r1").click(); });
    await act(async () => { buttonByText("BatchReviewPage.recurPreviewToggle").click(); });
    await flush();

    expect(mocks.toast.error).toHaveBeenCalledWith("preview down");
    expect(host.textContent).not.toContain("BatchReviewPage.previewLoading");

    mocks.getRecurrencePreview.mockResolvedValue({ windows: [] });
    await act(async () => { buttonByText("BatchReviewPage.recurPreviewToggle").click(); });
    await flush();
    expect(mocks.getRecurrencePreview).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("BatchReviewPage.noScheduledWindows");
  });
});
