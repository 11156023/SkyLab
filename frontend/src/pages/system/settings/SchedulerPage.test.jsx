// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import SchedulerPage from "./SchedulerPage";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
  /* t 要是穩定的參照：載入效果依賴 t，每次 render 換新會無限重抓 */
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/proxmoxConfig", () => ({
  ProxmoxConfigService: { getConfig: mocks.getConfig, updateConfig: vi.fn() },
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../contexts/UnsavedChangesContext", () => ({ useUnsavedChangesGuard: () => {} }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

let host;
let root;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.getConfig.mockResolvedValue({
    placement_loadavg_warn_per_core: 0.8,
    placement_loadavg_max_per_core: 1.5,
    placement_loadavg_penalty_weight: 0.9,
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function flush() {
  await act(async () => {
    /* TanStack Query 以 macrotask 批次通知，先等一個 timer tick */
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  });
}

test("三個 LoadAvg 欄位都註明以實體核計", async () => {
  await act(async () => root.render(withQueryClient(<SchedulerPage />)));
  await flush();

  const fields = [...host.querySelectorAll("label")].filter((label) =>
    label.textContent.includes("SettingsPage.placementLoadavg"),
  );
  expect(fields).toHaveLength(3);
  for (const field of fields) {
    expect(field.querySelector("em")?.textContent).toBe(
      "SettingsPage.placementLoadavgPerPhysicalCoreHint",
    );
  }
});
