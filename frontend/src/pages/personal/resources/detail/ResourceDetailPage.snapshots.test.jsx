// @vitest-environment happy-dom
/**
 * 快照分頁的顯示條件：機器當下不能用快照時，整個快照分頁（含一鍵重置、初始快照）要隱藏。
 * 備份分頁的顯示條件：快照不能用、而且所屬叢集有開放備份時，才以備份分頁取代。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import ResourceDetailPage from "./ResourceDetailPage";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  getSnapshotCapability: vi.fn(),
  getBackupCapability: vi.fn(),
  listForResource: vi.fn(),
  t: (key) => key,
}));
vi.mock("../../../../services/resources", () => ({
  ResourcesService: {
    get: mocks.get,
    getSnapshotCapability: mocks.getSnapshotCapability,
    getBackupCapability: mocks.getBackupCapability,
  },
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
vi.mock("./ResourceDetailGuideDemo", () => ({ default: ({ tab }) => <p data-testid={`guide-demo-${tab}`} /> }));
vi.mock("./SnapshotsTab", () => ({
  default: ({ onOperationFailed }) => (
    <button type="button" data-testid="snapshots-tab" onClick={onOperationFailed}>fail</button>
  ),
}));
vi.mock("./BackupsTab", () => ({
  default: ({ capability, onOperationFailed }) => (
    <button
      type="button"
      data-testid="backups-tab"
      data-max-count={String(capability?.max_count)}
      data-requires-shutdown={String(capability?.requires_shutdown)}
      onClick={onOperationFailed}
    >
      fail
    </button>
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
  mocks.getBackupCapability.mockResolvedValue({ available: false, reason: "not_configured" });
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
const backupTabButton = () => host.querySelector('[data-guide-tab="resource-backups"]');
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
    expect(mocks.getBackupCapability).not.toHaveBeenCalled();
    expect(snapshotTabButton()).not.toBeNull();
    expect(backupTabButton()).toBeNull();
  });
});

describe("ResourceDetailPage 備份分頁", () => {
  const backupReady = { available: true, reason: null, requires_shutdown: true, max_count: 2 };

  test("快照不能用、叢集有開放備份時，以備份分頁取代快照分頁", async () => {
    mocks.getSnapshotCapability.mockResolvedValue({ available: false, reason: "unsupported" });
    mocks.getBackupCapability.mockResolvedValue(backupReady);

    await renderPage();

    expect(mocks.getBackupCapability).toHaveBeenCalledWith(105);
    expect(tabKeys()).toEqual([
      "resource-overview",
      "resource-monitoring",
      "resource-specifications",
      "resource-backups",
      "resource-auditLogs",
      "resource-advanced",
    ]);

    await act(async () => backupTabButton().click());
    const tab = host.querySelector('[data-testid="backups-tab"]');
    expect(tab).not.toBeNull();
    /* 後端回的上限與「備份時要關機」要原樣交給分頁 */
    expect(tab.dataset.maxCount).toBe("2");
    expect(tab.dataset.requiresShutdown).toBe("true");
  });

  test("快照可用時不查備份、也不顯示備份分頁", async () => {
    mocks.getSnapshotCapability.mockResolvedValue({ available: true, reason: null });
    mocks.getBackupCapability.mockResolvedValue(backupReady);

    await renderPage();

    expect(mocks.getBackupCapability).not.toHaveBeenCalled();
    expect(snapshotTabButton()).not.toBeNull();
    expect(backupTabButton()).toBeNull();
  });

  test("快照不能用但叢集沒開放備份時，兩個分頁都不顯示", async () => {
    mocks.getSnapshotCapability.mockResolvedValue({ available: false, reason: "unsupported" });

    await renderPage();

    expect(snapshotTabButton()).toBeNull();
    expect(backupTabButton()).toBeNull();
  });

  test("備份可用性查詢失敗時不顯示備份分頁", async () => {
    mocks.getSnapshotCapability.mockResolvedValue({ available: false, reason: "unsupported" });
    mocks.getBackupCapability.mockRejectedValue(new Error("boom"));

    await renderPage();

    expect(backupTabButton()).toBeNull();
  });

  test("停在備份分頁時操作失敗會重查；備份變成不可用就收起分頁並退回總覽", async () => {
    mocks.getSnapshotCapability.mockResolvedValue({ available: false, reason: "unsupported" });
    mocks.getBackupCapability.mockResolvedValueOnce(backupReady);
    await renderPage();
    await act(async () => backupTabButton().click());

    mocks.getBackupCapability.mockResolvedValueOnce({ available: false, reason: "storage_unavailable" });
    await act(async () => host.querySelector('[data-testid="backups-tab"]').click());

    expect(mocks.getBackupCapability).toHaveBeenCalledTimes(2);
    expect(backupTabButton()).toBeNull();
    expect(host.querySelector('[data-testid="overview-tab"]')).not.toBeNull();
  });
});
