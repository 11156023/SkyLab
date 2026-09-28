// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { service, toastApi, i18n } = vi.hoisted(() => ({
  /* t 要是穩定的參考：卡片的 load 以 t 為依賴，每次換新函式會一直重抓 */
  i18n: { t: (key) => key, i18n: { language: "zh-TW" } },
  service: {
    get: vi.fn(),
    getSshKey: vi.fn(),
    getTemplateManual: vi.fn(),
    getCurrentStats: vi.fn(),
    getStats: vi.fn(),
    getBootOptions: vi.fn(),
    listIsoImages: vi.fn(),
    getCredentials: vi.fn(),
  },
  toastApi: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock("react-i18next", async (original) => ({ ...await original(),
  useTranslation: () => i18n,
}));
vi.mock("../../../../services/resources", () => ({ ResourcesService: service }));
vi.mock("../../../../hooks/useToast", () => ({ useToast: () => toastApi }));
vi.mock("../../../../hooks/useAutoRefresh", () => ({ default: () => {} }));
vi.mock("../../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u1", role: "student" } }) }));
vi.mock("../../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => async () => false }));
vi.mock("../../../../components/RrdChart/RrdChart", () => ({ default: () => null }));
/* 進階設定分頁只驗證要掛哪些卡片，卡片本身換成標記 */
vi.mock("./advanced/LifecycleCard", () => ({ default: () => <div data-card="lifecycle" /> }));
vi.mock("./advanced/FirewallCard", () => ({ default: () => <div data-card="firewall" /> }));
vi.mock("./advanced/SharingCard", () => ({ default: () => <div data-card="sharing" /> }));

import AdvancedSettingsTab from "./AdvancedSettingsTab";
import OverviewTab from "./OverviewTab";
import MonitoringTab from "./MonitoringTab";
import BootOptionsCard from "./advanced/BootOptionsCard";
import CredentialsCard from "./advanced/CredentialsCard";

let host, root;
async function render(element) {
  await act(async () => root.render(element));
  await act(async () => {});
}
const retryButton = () => [...host.querySelectorAll("button")].find((b) => b.textContent.includes("Error.retry"));

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  service.getTemplateManual.mockResolvedValue({ count: 0, items: [] });
  service.getStats.mockResolvedValue({ data: [] });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

describe("shared viewers", () => {
  const shared = {
    vmid: 120, name: "shared-vm", type: "qemu", status: "running", node: "pve1",
    access_role: "shared", can_manage: false, has_login_password: true,
    ssh_public_key: "ssh-ed25519 AAAA owner", public_urls: [],
  };

  it("does not mount the owner-only firewall card on the advanced tab", async () => {
    service.get.mockResolvedValue(shared);
    await render(<AdvancedSettingsTab vmid={120} />);
    expect(host.querySelector('[data-card="lifecycle"]')).not.toBeNull();
    expect(host.querySelector('[data-card="firewall"]')).toBeNull();
    expect(toastApi.error).not.toHaveBeenCalled();
  });

  it("still shows the firewall card to the owner", async () => {
    service.get.mockResolvedValue({ ...shared, access_role: "owner", can_manage: true });
    service.getBootOptions.mockResolvedValue({ boot_order: [], boot_devices: [] });
    service.getCredentials.mockResolvedValue({ authorized_keys: [] });
    await render(<AdvancedSettingsTab vmid={120} />);
    expect(host.querySelector('[data-card="firewall"]')).not.toBeNull();
  });

  it("does not request the owner's credentials on the overview tab", async () => {
    service.get.mockResolvedValue(shared);
    service.getCurrentStats.mockResolvedValue({});
    await render(<OverviewTab vmid={120} />);
    expect(host.textContent).toContain("shared-vm");
    expect(service.getSshKey).not.toHaveBeenCalled();
    expect(host.textContent).not.toContain("Error.generic");
    expect(host.textContent).not.toContain("OverviewTab.noCredentials");
  });

  it("keeps fetching credentials for class members who own the machine", async () => {
    service.get.mockResolvedValue({ ...shared, access_role: "class_member", can_manage: true });
    service.getSshKey.mockResolvedValue({ login_password: "pw" });
    service.getCurrentStats.mockResolvedValue({});
    await render(<OverviewTab vmid={120} />);
    expect(service.getSshKey).toHaveBeenCalledWith(120);
  });
});

describe("first load failures show a retryable error, not an endless spinner", () => {
  it("BootOptionsCard", async () => {
    service.getBootOptions.mockRejectedValueOnce(new Error("pve down"));
    await render(<BootOptionsCard vmid={1} canManage />);
    expect(host.textContent).not.toContain("BootOptionsCard.loading");
    expect(retryButton()).toBeTruthy();
    service.getBootOptions.mockResolvedValueOnce({ boot_order: [], boot_devices: [], supports_boot_order: true });
    await act(async () => retryButton().click());
    expect(retryButton()).toBeFalsy();
    expect(host.textContent).toContain("BootOptionsCard.bootOrderDefault");
  });

  it("CredentialsCard", async () => {
    service.getCredentials.mockRejectedValueOnce(new Error("pve down"));
    await render(<CredentialsCard vmid={1} />);
    expect(host.textContent).not.toContain("CredentialsCard.loading");
    expect(retryButton()).toBeTruthy();
    service.getCredentials.mockResolvedValueOnce({ authorized_keys: [], username: "student" });
    await act(async () => retryButton().click());
    expect(host.textContent).toContain("student");
  });

  it("MonitoringTab", async () => {
    service.getCurrentStats.mockRejectedValueOnce(new Error("pve down"));
    await render(<MonitoringTab vmid={1} />);
    expect(host.textContent).not.toContain("MonitoringTab.loadingData");
    expect(retryButton()).toBeTruthy();
    service.getCurrentStats.mockResolvedValueOnce({ cpu: 0.5, maxcpu: 2 });
    await act(async () => retryButton().click());
    await act(async () => {});
    expect(retryButton()).toBeFalsy();
    expect(host.textContent).toContain("MonitoringTab.cpuUsage");
  });
});
