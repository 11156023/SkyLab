// @vitest-environment happy-dom
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

/* TanStack Query 以 macrotask 批次通知，等一個 timer tick 再看畫面 */
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

it("clears the error state when retry succeeds after a failed first load", async () => {
  listMock
    .mockRejectedValueOnce(new Error("500"))
    .mockResolvedValue({ data: [{
      id: "r1", hostname: "lab-web-01", resource_type: "lxc", status: "pending",
      created_at: "2026-09-20T08:00:00Z",
    }] });
  listMyMock.mockResolvedValue({ data: [] });

  await act(async () => root.render(withQueryClient(<MemoryRouter><RequestsPage /></MemoryRouter>)));
  await flush();
  expect(host.textContent).toContain("Error.retry");

  const retry = [...host.querySelectorAll("button")].find((button) => button.textContent.includes("Error.retry"));
  await act(async () => retry.click());
  await flush();

  expect(listMock).toHaveBeenCalledTimes(2);
  expect(host.textContent).not.toContain("Error.retry");
  expect(host.textContent).toContain("lab-web-01");
});
