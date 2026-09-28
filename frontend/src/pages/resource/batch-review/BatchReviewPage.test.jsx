// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import BatchReviewPage from "./BatchReviewPage";

const mocks = vi.hoisted(() => ({
  listAll: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/batchProvision", () => ({
  BatchProvisionService: { listAll: mocks.listAll },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
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
});
