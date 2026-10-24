// @vitest-environment happy-dom
/*
 * 回歸重現：離開 LDAP 頁後 5 分鐘內（gcTime）再進來，表單用快取裡的舊設定初始化，
 * 進頁時的重抓拿回伺服器上的新設定，表單卻不再更新（baseline 已設就略過）；之後存檔 toPayload(form)
 * 會把整份舊值（伺服器位址、群組 DN…）寫回去。
 * 舊版（ce7675b9^ LdapPage.jsx:71-91）每次進頁都 LdapConfigService.get() 後才填表。
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { createQueryClient } from "../../../queries/queryClient";
import { withQueryClient } from "../../../queries/testing";
import { ldapConfigKeys } from "../../../queries/ldapConfig";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/ldapConfig", () => ({
  LdapConfigService: { get: mocks.get, update: vi.fn(), test: vi.fn() },
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../contexts/UnsavedChangesContext", () => ({ useUnsavedChangesGuard: () => {} }));
vi.mock("../../../components/PageHeader/PageHeader", () => ({ default: () => null }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));

const { default: LdapPage } = await import("./LdapPage");

let host;
let root;
let client;

const CONFIG = {
  enabled: true,
  server_uri: "ldaps://old.example.edu",
  use_starttls: false,
  bind_dn: "cn=svc",
  has_bind_password: true,
  user_search_base: "ou=people",
  user_filter_template: "(uid={username})",
  email_attribute: "mail",
  name_attribute: "cn",
  teacher_group_dn: "",
  admin_group_dn: "",
  auto_create_users: true,
  connect_timeout_seconds: 5,
};

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

function serverUriInput() {
  const label = [...host.querySelectorAll("label")]
    .find((el) => el.textContent.includes("LdapTab.serverUri"));
  return label?.querySelector("input");
}

test("coming back to the page shows the freshly fetched LDAP settings, not the cached ones", async () => {
  client.setQueryData(ldapConfigKeys.config, CONFIG);
  mocks.get.mockResolvedValue({ ...CONFIG, server_uri: "ldaps://new.example.edu" });

  await act(async () => root.render(withQueryClient(<LdapPage />, client)));
  await flush();
  await flush();

  expect(mocks.get).toHaveBeenCalledTimes(1);
  expect(client.getQueryData(ldapConfigKeys.config).server_uri).toBe("ldaps://new.example.edu");
  /* 舊版：new；新版：表單停在快取的 old */
  expect(serverUriInput().value).toBe("ldaps://new.example.edu");
});
