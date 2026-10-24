// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import RequestReviewPage from "./RequestReviewPage";
import { vmRequestKeys } from "../../../queries/vmRequests";
import { specChangeRequestKeys } from "../../../queries/specChangeRequests";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  vmListAll: vi.fn(),
  getReviewContext: vi.fn(),
  vmReview: vi.fn(),
  specListAll: vi.fn(),
  specReview: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/vmRequests", () => ({
  VmRequestsService: {
    listAll: mocks.vmListAll,
    getReviewContext: mocks.getReviewContext,
    review: mocks.vmReview,
  },
}));
vi.mock("../../../services/specChangeRequests", () => ({
  SpecChangeRequestsService: { listAll: mocks.specListAll, review: mocks.specReview },
}));
vi.mock("../../../services/pendingResources", () => ({ CONSUMED_REQUEST_MARKERS: [] }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
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

function vmRequest(id, status, createdAt = "2026-09-01T00:00:00Z") {
  return {
    id,
    status,
    hostname: `host-${id}`,
    user_email: `${id}@example.edu`,
    resource_type: "lxc",
    cores: 1,
    memory: 1024,
    rootfs_size: 8,
    reason: `reason-${id}`,
    created_at: createdAt,
  };
}

let root;
let host;
/* 每個測試自己的 QueryClient；背景刷新在測試裡用 refreshLists() 觸發（原本是 useAutoRefresh 的回呼） */
let client;

function refreshLists() {
  return Promise.all([
    client.refetchQueries({ queryKey: vmRequestKeys.all }),
    client.refetchQueries({ queryKey: specChangeRequestKeys.all }),
  ]);
}

async function renderPage() {
  await act(async () => { root.render(withQueryClient(<RequestReviewPage />, client)); });
}

async function flush() {
  await act(async () => {
    /* TanStack Query 以 macrotask 批次通知，先等一個 timer tick */
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

function rowButtons() {
  return [...host.querySelectorAll("button")].filter((b) => b.textContent.includes("host-"));
}

function buttonByText(text) {
  return [...host.querySelectorAll("button")].find((b) => b.textContent === text);
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

beforeEach(() => {
  vi.resetAllMocks();
  client = createTestQueryClient();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.specListAll.mockResolvedValue({ data: [], count: 0 });
  mocks.getReviewContext.mockResolvedValue({ feasible: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("RequestReviewPage", () => {
  test("pending tab asks the server for pending requests instead of filtering the newest 100", async () => {
    const newest = Array.from({ length: 100 }, (_, i) => vmRequest(`a${i}`, "approved", "2026-09-20T00:00:00Z"));
    const oldPending = vmRequest("old", "pending", "2026-01-01T00:00:00Z");
    mocks.vmListAll.mockImplementation(async (status) => {
      if (status === "pending") return { data: [oldPending], count: 1 };
      if (status === undefined) return { data: newest, count: 101 };
      return { data: [], count: status === "approved" ? 100 : 0 };
    });

    await renderPage();
    await flush();

    /* 待審是審核工作佇列，用較大的上限；其他分頁只取筆數給角標 */
    expect(mocks.vmListAll).toHaveBeenCalledWith("pending", { limit: 1000 });
    expect(mocks.specListAll).toHaveBeenCalledWith({ status: "pending", limit: 1000 });
    expect(mocks.vmListAll).toHaveBeenCalledWith("approved", { limit: 1 });
    expect(mocks.vmListAll).toHaveBeenCalledWith(undefined, { limit: 1 });
    expect(mocks.specListAll).toHaveBeenCalledWith({ status: "approved", limit: 1 });
    /* spec-change 沒有 expired 狀態，不能把 expired 傳給後端 */
    expect(mocks.specListAll.mock.calls.some(([params]) => params?.status === "expired")).toBe(false);
    expect(rowButtons().map((b) => b.textContent)).toEqual([expect.stringContaining("host-old")]);
    expect(host.querySelector('[data-tab="pending"]').dataset.badge).toBe("1");
    expect(host.querySelector('[data-tab="all"]').dataset.badge).toBe("101");
  });

  test("history tabs keep the smaller list limit when active", async () => {
    mocks.vmListAll.mockResolvedValue({ data: [], count: 0 });

    await renderPage();
    await flush();
    mocks.vmListAll.mockClear();
    mocks.specListAll.mockClear();

    await act(async () => { host.querySelector('[data-tab="approved"]').click(); });
    await flush();

    expect(mocks.vmListAll).toHaveBeenCalledWith("approved", { limit: 100 });
    expect(mocks.specListAll).toHaveBeenCalledWith({ status: "approved", limit: 100 });
    expect(mocks.vmListAll).toHaveBeenCalledWith("pending", { limit: 1 });
    expect(mocks.vmListAll).not.toHaveBeenCalledWith("pending", { limit: 1000 });
  });

  test("background refresh does not silently switch the detail pane to another request", async () => {
    const a = vmRequest("A", "pending", "2026-09-02T00:00:00Z");
    const b = vmRequest("B", "pending", "2026-09-01T00:00:00Z");
    let pending = [a, b];
    mocks.vmListAll.mockImplementation(async (status) => (
      status === "pending" ? { data: pending, count: pending.length } : { data: [], count: 0 }
    ));

    await renderPage();
    await flush();
    expect(host.textContent).toContain("RequestReviewPage.selectARequest");

    await act(async () => { rowButtons()[0].click(); });
    await flush();
    expect(host.querySelector("h2").textContent).toBe("host-A");

    pending = [b];
    await act(async () => { await refreshLists(); });
    await flush();

    expect(host.querySelector("h2")).toBeNull();
    expect(host.textContent).toContain("RequestReviewPage.selectARequest");
  });

  test("a slow response for the previous tab does not overwrite the newly selected tab", async () => {
    let releasePending;
    const pendingGate = new Promise((resolve) => { releasePending = resolve; });
    let firstPendingCall = true;
    mocks.vmListAll.mockImplementation(async (status, { limit }) => {
      if (status === "pending" && limit === 1000 && firstPendingCall) {
        firstPendingCall = false;
        await pendingGate;
        return { data: [vmRequest("P", "pending")], count: 1 };
      }
      if (status === "approved" && limit === 100) return { data: [vmRequest("A", "approved")], count: 1 };
      return { data: [], count: 0 };
    });

    await renderPage();
    await act(async () => { host.querySelector('[data-tab="approved"]').click(); });
    await flush();
    expect(rowButtons().map((b) => b.textContent)).toEqual([expect.stringContaining("host-A")]);

    /* 待審分頁的請求較晚才回來：不能把核准分頁的清單換成待審 */
    await act(async () => { releasePending(); });
    await flush();
    expect(rowButtons().map((b) => b.textContent)).toEqual([expect.stringContaining("host-A")]);
    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
  });

  test("approve stays disabled while the VM review context is still loading", async () => {
    mocks.vmListAll.mockImplementation(async (status) => (
      status === "pending" ? { data: [vmRequest("A", "pending")], count: 1 } : { data: [], count: 0 }
    ));
    mocks.getReviewContext.mockReturnValue(new Promise(() => {}));

    await renderPage();
    await flush();
    await act(async () => { rowButtons()[0].click(); });
    await flush();

    expect(buttonByText("RequestReviewPage.approve").disabled).toBe(true);
  });

  test("approving refreshes the lists in place: no loading screen and buttons stay locked until the refresh lands", async () => {
    let pending = [vmRequest("A", "pending"), vmRequest("B", "pending", "2026-08-01T00:00:00Z")];
    const refreshGate = deferred();
    let gateNextPending = false;
    mocks.vmListAll.mockImplementation(async (status, { limit }) => {
      if (status === "pending" && limit === 1000) {
        if (gateNextPending) { gateNextPending = false; await refreshGate.promise; }
        return { data: pending, count: pending.length };
      }
      return { data: [], count: 0 };
    });
    mocks.vmReview.mockResolvedValue({});

    await renderPage();
    await flush();
    await act(async () => { rowButtons()[0].click(); });
    await flush();
    expect(buttonByText("RequestReviewPage.approve").disabled).toBe(false);

    pending = [pending[1]];
    gateNextPending = true;
    await act(async () => { buttonByText("RequestReviewPage.approve").click(); });
    await flush();

    expect(mocks.vmReview).toHaveBeenCalledWith("A", { status: "approved", review_comment: null });
    /* 重抓還沒回來：舊清單留在畫面上（不閃載入畫面），點舊列也不能再按核准 */
    expect(host.querySelector('[data-testid="loading"]')).toBeNull();
    expect(rowButtons()).toHaveLength(2);
    await act(async () => { rowButtons()[0].click(); });
    await flush();
    expect(buttonByText("RequestReviewPage.approve").disabled).toBe(true);

    await act(async () => { refreshGate.resolve(); });
    await flush();
    expect(rowButtons().map((b) => b.textContent)).toEqual([expect.stringContaining("host-B")]);
    expect(host.querySelector('[data-tab="pending"]').dataset.badge).toBe("1");
  });

  test("a failed background refresh keeps the current list instead of showing the error", async () => {
    mocks.vmListAll
      .mockImplementation(async (status) => (
        status === "pending" ? { data: [vmRequest("A", "pending")], count: 1 } : { data: [], count: 0 }
      ));

    await renderPage();
    await flush();
    expect(rowButtons()).toHaveLength(1);

    mocks.vmListAll.mockRejectedValue(new Error("pve down"));
    await act(async () => { await refreshLists(); });
    await flush();

    expect(rowButtons()).toHaveLength(1);
    expect(host.textContent).not.toContain("pve down");
  });

  test("a failed first load shows the error and Retry reloads the list", async () => {
    mocks.vmListAll.mockRejectedValue(new Error("boom"));

    await renderPage();
    await flush();
    expect(host.textContent).toContain("boom");

    mocks.vmListAll.mockImplementation(async (status) => (
      status === "pending" ? { data: [vmRequest("A", "pending")], count: 1 } : { data: [], count: 0 }
    ));
    await act(async () => { buttonByText("RequestReviewPage.retry").click(); });
    await flush();

    expect(host.textContent).not.toContain("boom");
    expect(rowButtons()).toHaveLength(1);
  });
});
