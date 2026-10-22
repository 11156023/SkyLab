// @vitest-environment happy-dom

/*
 * 回歸：範本建立中／更新循環中（4 秒輪詢）時清單 API 開始失敗，新版每 4 秒跳一次錯誤 toast，
 * 舊版只有每 30 秒一次。
 *
 * 舊版（ce7675b9^ pages/resource/templates/TemplatesPage.jsx:297-330）：load() 失敗時 toast，並回傳 []，
 * tick 依回傳的清單判斷 active → 失敗後一律 30 秒才再試。
 * 新版：輪詢間隔是 queries/templates.js 的 templatesPollInterval(query.state.data)，失敗時 data 保留上一份
 * （仍有 creating），所以照 4 秒重試；TemplatesPage.jsx:397-401 每個新的錯誤物件都 toast 一次。
 * 一段 25 秒的故障（例如後端重啟）在新版會跳 6～7 則錯誤 toast。
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { withQueryClient } from "../../../queries/testing";
import TemplatesPage from "./TemplatesPage";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/templates", () => ({
  TemplatesService: {
    list: mocks.list,
    listAttachments: vi.fn(),
    downloadAttachment: vi.fn(),
    startUpdateCycle: vi.fn(),
    finishUpdateCycle: vi.fn(),
    cancelUpdateCycle: vi.fn(),
    retry: vi.fn(),
    remove: vi.fn(),
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
vi.mock("./TemplateFormDialog", () => ({ default: () => null }));
vi.mock("./TemplateCloneDialog", () => ({ default: () => null }));

const CREATING_TEMPLATE = Object.freeze({
  id: 1,
  name: "ubuntu-base",
  status: "creating",
  resource_type: "qemu",
  visibility: "private",
  version: 1,
  vmid: 9001,
});

let root;
let host;

async function advance(ms) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  notifyManager.setScheduler((cb) => cb());
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  notifyManager.setScheduler((cb) => setTimeout(cb, 0));
  vi.useRealTimers();
});

describe("TemplatesPage：建立中的範本輪詢時清單 API 失敗", () => {
  test("故障期間錯誤提示的頻率不超過舊版（30 秒一次）", async () => {
    mocks.list.mockResolvedValueOnce({ data: [CREATING_TEMPLATE] });
    /* 之後每次都失敗（真實 API 每次都是新的錯誤物件） */
    mocks.list.mockImplementation(() => Promise.reject(new Error("後端暫時無法使用")));

    await act(async () => { root.render(withQueryClient(<TemplatesPage />)); });
    await advance(10);
    await advance(10);
    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(mocks.toast.error).not.toHaveBeenCalled();

    /* 4 秒後第一次輪詢失敗：新舊版都提示一次 */
    await advance(4_005);
    await advance(10);
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);

    /* 再過 25 秒：舊版失敗後改成 30 秒才重試，這段期間不會再提示 */
    for (let i = 0; i < 5; i += 1) {
      await advance(5_000);
      await advance(10);
    }
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);

    /* 30 秒後的重試又失敗：同一串失敗只提示一次（後端掛著時不要每 30 秒跳一則） */
    await advance(6_000);
    await advance(10);
    expect(mocks.list.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
  });
});
