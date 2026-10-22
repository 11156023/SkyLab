// @vitest-environment happy-dom
/*
 * 回歸重現：離開資源排程頁後 5 分鐘內（gcTime）再進來，表單用快取裡的舊設定初始化（loaded 設為 true），
 * 進頁時的重抓拿回伺服器上的新設定，表單卻不再更新；之後存檔 buildPayload(form) 把整份舊值寫回去。
 * 舊版（ce7675b9^ SchedulerPage.jsx:171-180）每次進頁都 getConfig() 後才填表。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createQueryClient } from "../../../queries/queryClient";
import { withQueryClient } from "../../../queries/testing";
import { proxmoxConfigKeys } from "../../../queries/proxmoxConfig";

const mocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
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

const { default: SchedulerPage } = await import("./SchedulerPage");

let host;
let root;
let client;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  /* 與正式環境相同：離開頁面後快取留著（gcTime 預設 5 分鐘），回來時已過 staleTime 要重抓 */
  client = createQueryClient({ queries: { retry: false, staleTime: 0, refetchInterval: false } });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  client.clear();
});

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  });
}

function warnInput() {
  const label = [...host.querySelectorAll("label")]
    .find((el) => el.textContent.includes("SettingsPage.placementLoadavgWarnPerCore"));
  return label?.querySelector("input");
}

test("coming back to the page shows the freshly fetched scheduler settings, not the cached ones", async () => {
  client.setQueryData(proxmoxConfigKeys.config, { placement_loadavg_warn_per_core: 0.8 });
  mocks.getConfig.mockResolvedValue({ placement_loadavg_warn_per_core: 1.2 });

  await act(async () => root.render(withQueryClient(<SchedulerPage />, client)));
  await flush();
  await flush();

  expect(mocks.getConfig).toHaveBeenCalledTimes(1);
  expect(client.getQueryData(proxmoxConfigKeys.config).placement_loadavg_warn_per_core).toBe(1.2);
  /* 舊版：1.2；新版：表單停在快取的 0.8 */
  expect(warnInput().value).toBe("1.2");
});
