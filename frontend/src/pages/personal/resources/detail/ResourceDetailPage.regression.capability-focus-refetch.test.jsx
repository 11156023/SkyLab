// @vitest-environment happy-dom
/**
 * 回歸：停在快照（或備份）分頁時切回視窗，可用性的背景重抓剛好失敗一次，分頁就被收起並踢回總覽。
 *
 * 改版前快照／備份可用性只在進頁與「操作失敗後重查」時抓（ResourceDetailPage effect 依賴
 * [isGuideDemo, vmid, capabilityCheckSeq]），切回視窗不會重問 PVE。
 * 改版後改用 useSnapshotCapabilityQuery／useBackupCapabilityQuery：沿用全站預設 refetchOnWindowFocus＋staleTime 5 秒，
 * 每次切回視窗都在背景重查；而頁面用 `snapshotCapabilityQuery.isError` 判定「不可用」——v5 背景重抓失敗時
 * data 還在但 isError＝true → 快照分頁消失、activeTab 退回總覽、SnapshotsTab 整個卸載（開到一半的建立對話框也沒了），
 * 直到下一次切回視窗重抓成功才回來。PVE 短暫不通（502／逾時）就會發生。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { focusManager } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import ResourceDetailPage from "./ResourceDetailPage";
import { createQueryClient } from "../../../../queries/queryClient";
import { withQueryClient } from "../../../../queries/testing";

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
vi.mock("./OverviewTab", () => ({ default: () => <p data-testid="overview-tab" /> }));
vi.mock("./MonitoringTab", () => ({ default: () => <p data-testid="monitoring-tab" /> }));
vi.mock("./SpecificationsTab", () => ({ default: () => <p data-testid="specifications-tab" /> }));
vi.mock("./AuditLogsTab", () => ({ default: () => <p data-testid="audit-tab" /> }));
vi.mock("./AdvancedSettingsTab", () => ({ default: () => <p data-testid="advanced-tab" /> }));
vi.mock("./ResourceDetailGuideDemo", () => ({ default: () => null }));
vi.mock("./SnapshotsTab", () => ({ default: () => <p data-testid="snapshots-tab" /> }));
vi.mock("./BackupsTab", () => ({ default: () => <p data-testid="backups-tab" /> }));

let host;
let root;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

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
  focusManager.setFocused(undefined);
  await act(async () => root.unmount());
  host.remove();
});

const snapshotTabButton = () => host.querySelector('[data-guide-tab="resource-snapshots"]');

test("a failed background re-check on window focus does not hide the snapshot tab the user is on", async () => {
  /* 正式環境的 QueryClient 設定（切回視窗會重抓），staleTime 0 代表「離開超過 5 秒」 */
  const client = createQueryClient({ queries: { staleTime: 0, gcTime: 0 } });
  mocks.getSnapshotCapability
    .mockResolvedValueOnce({ available: true, reason: null })
    /* 切回視窗那一次 PVE 剛好沒回應 */
    .mockRejectedValueOnce(new Error("502 Bad Gateway"));

  await act(async () => {
    root.render(withQueryClient(
      <MemoryRouter initialEntries={["/my-resources/105"]}>
        <Routes>
          <Route path="/my-resources/:vmid" element={<ResourceDetailPage />} />
        </Routes>
      </MemoryRouter>,
      client,
    ));
  });
  await flush();
  await flush();
  await flush();

  await act(async () => snapshotTabButton().click());
  expect(host.querySelector('[data-testid="snapshots-tab"]')).not.toBeNull();

  /* 使用者切到別的視窗再切回來 */
  act(() => { focusManager.setFocused(false); });
  act(() => { focusManager.setFocused(true); });
  await flush();
  await flush();

  /* 修正後與改版前相同：切回視窗不重查可用性 */
  expect(mocks.getSnapshotCapability).toHaveBeenCalledTimes(1);
  /* 改版前：切回視窗不重查，快照分頁與正在看的內容都還在 */
  expect(snapshotTabButton()).not.toBeNull();
  expect(host.querySelector('[data-testid="snapshots-tab"]')).not.toBeNull();
});
