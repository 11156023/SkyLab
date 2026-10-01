// @vitest-environment happy-dom
/**
 * 快照分頁的顯示條件：機器當下不能用快照時，整個快照分頁（含一鍵重置、初始快照）要隱藏。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import ResourceDetailPage from "./ResourceDetailPage";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  getSnapshotCapability: vi.fn(),
  listForResource: vi.fn(),
  t: (key) => key,
}));
vi.mock("../../../../services/resources", () => ({
  ResourcesService: { get: mocks.get, getSnapshotCapability: mocks.getSnapshotCapability },
}));
vi.mock("../../../../services/auditLogs", () => ({
  AuditLogsService: { listForResource: mocks.listForResource },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
/* 各分頁內容與本測試無關，換成只留識別字的替身；快照分頁多一顆鈕模擬「操作失敗」 */
vi.mock("./OverviewTab", () => ({ default: () => <p data-testid="overview-tab" /> }));
vi.mock("./MonitoringTab", () => ({ default: () => <p data-testid="monitoring-tab" /> }));
vi.mock("./SpecificationsTab", () => ({ default: () => <p data-testid="specifications-tab" /> }));
vi.mock("./AuditLogsTab", () => ({ default: () => <p data-testid="audit-tab" /> }));
vi.mock("./AdvancedSettingsTab", () => ({ default: () => <p data-testid="advanced-tab" /> }));
vi.mock("./SnapshotsTab", () => ({
  default: ({ onOperationFailed }) => (
    <button type="button" data-testid="snapshots-tab" onClick={onOperationFailed}>fail</button>
  ),
}));

let host;
let root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  mocks.get.mockResolvedValue({ access_role: "owner", can_manage: true });
  mocks.listForResource.mockResolvedValue({ count: 0 });
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function renderPage(path = "/my-resources/105") {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/my-resources/:vmid" element={<ResourceDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );
  });
}

const snapshotTabButton = () => host.querySelector('[data-guide-tab="resource-snapshots"]');
const tabKeys = () =>
  [...host.querySelectorAll("[data-guide-tab]")].map((el) => el.dataset.guideTab);

describe("ResourceDetailPage 快照分頁", () => {
  test("機器支援快照時顯示快照分頁", async () => {
    mocks.getSnapshotCapability.mockResolvedValue({ available: true, reason: null });

    await renderPage();

    expect(mocks.getSnapshotCapability).toHaveBeenCalledWith(105);
    expect(tabKeys()).toEqual([
      "resource-overview",
      "resource-monitoring",
      "resource-specifications",
      "resource-snapshots",
      "resource-auditLogs",
      "resource-advanced",
    ]);
  });

  test("機器當下不支援快照時隱藏快照分頁，其他分頁不受影響", async () => {
    mocks.getSnapshotCapability.mockResolvedValue({ available: false, reason: "unsupported" });

    await renderPage();

    expect(snapshotTabButton()).toBeNull();
    expect(tabKeys()).toEqual([
      "resource-overview",
      "resource-monitoring",
      "resource-specifications",
      "resource-auditLogs",
      "resource-advanced",
    ]);
  });

  test("查不到可用性（請求失敗）時一樣隱藏", async () => {
    mocks.getSnapshotCapability.mockRejectedValue(new Error("boom"));

    await renderPage();

    expect(snapshotTabButton()).toBeNull();
  });

  test("可用性還沒查到之前不顯示快照分頁", async () => {
    let resolve;
    mocks.getSnapshotCapability.mockReturnValue(new Promise((r) => { resolve = r; }));

    await renderPage();
    expect(snapshotTabButton()).toBeNull();

    await act(async () => resolve({ available: true, reason: null }));
    expect(snapshotTabButton()).not.toBeNull();
  });

  test("停在快照分頁時操作失敗會重查；變成不可用就收起分頁並退回總覽", async () => {
    mocks.getSnapshotCapability.mockResolvedValueOnce({ available: true, reason: null });
    await renderPage();

    await act(async () => snapshotTabButton().click());
    expect(host.querySelector('[data-testid="snapshots-tab"]')).not.toBeNull();

    mocks.getSnapshotCapability.mockResolvedValueOnce({ available: false, reason: "unsupported" });
    await act(async () => host.querySelector('[data-testid="snapshots-tab"]').click());

    expect(mocks.getSnapshotCapability).toHaveBeenCalledTimes(2);
    expect(snapshotTabButton()).toBeNull();
    expect(host.querySelector('[data-testid="snapshots-tab"]')).toBeNull();
    expect(host.querySelector('[data-testid="overview-tab"]')).not.toBeNull();
  });

  test("導覽示範頁不查後端，快照分頁照常顯示", async () => {
    await renderPage("/my-resources/demo");

    expect(mocks.getSnapshotCapability).not.toHaveBeenCalled();
    expect(snapshotTabButton()).not.toBeNull();
  });
});
