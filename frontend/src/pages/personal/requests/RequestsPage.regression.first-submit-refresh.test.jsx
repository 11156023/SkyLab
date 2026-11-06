// @vitest-environment happy-dom
/* 回歸：還沒有任何申請時從空狀態進表單、送出後回列表，剛送出的那一張要出現在清單裡
   （2026-11-06 ntub 實測：第一次送出後停在「尚無申請紀錄」，重整才看得到）。 */
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
/* 表單替身：只留「送出後返回」那一顆按鈕，模擬真表單送出成功後呼叫 onBack */
vi.mock("./RequestFormPage", () => ({
  default: ({ onBack }) => (
    <button type="button" data-testid="fake-submit" onClick={() => setTimeout(onBack, 0)}>
      submit-and-back
    </button>
  ),
}));

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

it("shows the request submitted from the empty state without a reload", async () => {
  const created = {
    id: "r-new", hostname: "qa-student-lxc", resource_type: "lxc", status: "pending",
    created_at: "2026-11-06T01:52:00Z",
  };
  /* 伺服器：送出前空清單，送出後有一張 */
  let serverRequests = [];
  listMock.mockImplementation(async () => ({ data: [...serverRequests] }));
  listMyMock.mockResolvedValue({ data: [] });

  await act(async () => root.render(withQueryClient(<MemoryRouter><RequestsPage /></MemoryRouter>)));
  await flush();
  expect(host.textContent).toContain("RequestsPage.emptyTitle");

  const createNow = [...host.querySelectorAll("button")].find((b) => b.textContent.includes("RequestsPage.createNow"));
  await act(async () => createNow.click());
  await flush();
  const submit = host.querySelector('[data-testid="fake-submit"]');
  expect(submit).toBeTruthy();

  serverRequests = [created];
  await act(async () => submit.click());
  await flush();
  await flush();

  expect(host.textContent).not.toContain("RequestsPage.emptyTitle");
  expect(host.textContent).toContain("qa-student-lxc");
});
