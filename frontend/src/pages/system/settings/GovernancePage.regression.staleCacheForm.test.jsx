// @vitest-environment happy-dom
/*
 * 回歸重現：離開資源政策頁後 5 分鐘內（gcTime）再進來，表單用快取裡的舊設定初始化；
 * 進頁時的重抓雖然拿回了伺服器上的新設定，表單卻不再更新（baseline 已設就略過）。
 * 這時改任何一格按儲存，updateConfig(form) 會把整份舊值寫回去，蓋掉別人（另一位管理員）剛改的設定。
 * 舊版（ce7675b9^ GovernancePage.jsx:124-145）每次進頁都 getConfig() 後才填表，一定是最新值。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createQueryClient } from "../../../queries/queryClient";
import { withQueryClient } from "../../../queries/testing";
import { governanceKeys } from "../../../queries/governance";

const mocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/governance", () => ({
  GovernanceService: { getConfig: mocks.getConfig, updateConfig: vi.fn() },
}));
vi.mock("./QuotasSection", () => ({ default: () => null }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../contexts/UnsavedChangesContext", () => ({ useUnsavedChangesGuard: () => {} }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

const { default: GovernancePage } = await import("./GovernancePage");

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

function concurrencyInput() {
  const label = [...host.querySelectorAll("label")]
    .find((el) => el.textContent.includes("GovernanceTab.provisionMaxConcurrency"));
  return label?.querySelector("input");
}

test("coming back to the page shows the freshly fetched settings, not the cached ones", async () => {
  /* 上次離開頁面時留下的快取 */
  client.setQueryData(governanceKeys.config, { provision_max_concurrency: 2, alerts_enabled: true });
  /* 期間另一位管理員把併發數改成 6；進頁的重抓拿到的是新值 */
  mocks.getConfig.mockResolvedValue({ provision_max_concurrency: 6, alerts_enabled: true });

  await act(async () => root.render(withQueryClient(<GovernancePage />, client)));
  await flush();
  await flush();

  expect(mocks.getConfig).toHaveBeenCalledTimes(1);
  expect(client.getQueryData(governanceKeys.config).provision_max_concurrency).toBe(6);
  /* 舊版：6；新版：表單停在快取的 2 */
  expect(concurrencyInput().value).toBe("6");
});
