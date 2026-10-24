// @vitest-environment happy-dom
/**
 * 回歸：只有規格調整／延期申請、沒有 VM 申請單的人（班級機器、快速練習機器的擁有者）
 * 進「我的申請」時，先看到「還沒有任何申請」空狀態，幾秒後規格調整區塊才冒出來。
 *
 * 改版前 fetchRequests 用 Promise.all 同時等 VmRequestsService.list 與 SpecChangeRequestsService.listMy，
 * 兩份都回來才離開載入中（RequestsPage.jsx@ce7675b9^ 第 534 行），空狀態只會在兩份都真的是空的時候出現。
 * 改版後兩份拆成兩支 query，loading 只看 VM 申請那支（useQueryView(requestsQuery)），
 * isEmpty 卻同時看兩份：VM 申請（純 DB）先回來、規格調整（後端要問 PVE 拿機器名稱，較慢）還在路上時，
 * specRequests 還是空陣列 → isEmpty＝true → 顯示空狀態與「立即申請」鈕。
 * 從「我的資源」切過來時 VM 申請單已在快取裡，空狀態會從第一個畫面就出現。
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const listMock = vi.fn();
const listMyMock = vi.fn();

vi.mock("react-i18next", async (original) => ({ ...await original(),
  useTranslation: () => ({ t: (key) => key, i18n: { language: "zh-TW" } }),
}));
vi.mock("../../../services/vmRequests", () => ({
  VmRequestsService: { list: (...args) => listMock(...args) },
}));
vi.mock("../../../services/specChangeRequests", () => ({
  SpecChangeRequestsService: { listMy: (...args) => listMyMock(...args) },
  canApplySpecRequest: () => false,
  canCancelSpecRequest: () => false,
  specRequestChangeLabel: () => "",
  specRequestDisplayStatus: () => "pending",
}));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { role: "student" } }) }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => vi.fn() }));
vi.mock("./RequestFormPage", () => ({ default: () => null }));

const { default: RequestsPage } = await import("./RequestsPage");
const { withQueryClient } = await import("../../../queries/testing");

let host, root;

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
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it("does not claim there are no requests while the spec-change list is still loading", async () => {
  /* 沒有 VM 申請單（機器來自班級），只有一張延期申請 */
  listMock.mockResolvedValue({ data: [] });
  let resolveSpecs;
  listMyMock.mockReturnValue(new Promise((resolve) => { resolveSpecs = resolve; }));

  await act(async () => root.render(withQueryClient(<MemoryRouter><RequestsPage /></MemoryRouter>)));
  await flush();
  expect(listMock).toHaveBeenCalledTimes(1);
  expect(listMyMock).toHaveBeenCalledTimes(1);

  /* 改版前：兩份都回來之前一直是載入中，不會先說「沒有申請」 */
  expect(host.textContent).not.toContain("RequestsPage.emptyTitle");

  await act(async () => resolveSpecs({ data: [{
    id: "s1", vmid: 301, change_type: "expiry", status: "pending", resource_name: "class-lab-01",
    created_at: "2026-10-20T08:00:00Z",
  }] }));
  await flush();
  expect(host.textContent).toContain("RequestsPage.specSectionTitle");
  expect(host.textContent).not.toContain("RequestsPage.emptyTitle");
});
