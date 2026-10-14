// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import GatewayInstallTab from "./GatewayInstallTab";

const mocks = vi.hoisted(() => ({
  getInstallStatus: vi.fn(),
  startInstall: vi.fn(),
  syncRules: vi.fn(),
  confirm: vi.fn(),
  autoRefresh: null,
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/gateway", () => ({
  GatewayService: { getInstallStatus: mocks.getInstallStatus, startInstall: mocks.startInstall },
}));
vi.mock("../../../services/reverseProxy", () => ({
  ReverseProxyService: { syncRules: mocks.syncRules },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../hooks/useAutoRefresh", () => ({ default: (refresh) => { mocks.autoRefresh = refresh; } }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));

let root;
let host;

async function flush() {
  await act(async () => {
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
}

const status = (state) => ({
  state,
  root_access: true,
  components: { nginx: true, wireguard: true, ufw: true },
  defaults: {
    ingress_interface: "eth0",
    vm_interface: "eth1",
    snat_address: "10.10.0.2",
    listen_port: 51821,
    forward_port_start: 30000,
    forward_port_end: 39999,
    monitoring_allow_from: [],
  },
  interfaces: [],
  log: "",
  exit_code: state === "failed" ? 1 : null,
  started_at: null,
  finished_at: null,
});

function buttonByText(text) {
  return [...host.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.autoRefresh = null;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.syncRules.mockResolvedValue({ message: "synced" });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("GatewayInstallTab", () => {
  test("a Refresh result that lands after Start install is ignored; sync runs once when it really finishes", async () => {
    const staleRefresh = deferred();
    mocks.getInstallStatus
      .mockResolvedValueOnce(status("succeeded"))
      .mockReturnValueOnce(staleRefresh.promise)
      .mockResolvedValueOnce(status("running"))
      .mockResolvedValueOnce(status("succeeded"));
    mocks.startInstall.mockResolvedValue(status("running"));
    mocks.confirm.mockResolvedValue(true);

    await act(async () => { root.render(<GatewayInstallTab gatewayReady onGoToConnection={() => {}} />); });
    await flush();
    expect(host.textContent).toContain("GatewayPage.installState_succeeded");

    // 手動重新整理還在路上時按下重新安裝
    await act(async () => { buttonByText("GatewayPage.installRefresh").click(); });
    await act(async () => { buttonByText("GatewayPage.installReinstall").click(); });
    await flush();
    expect(mocks.startInstall).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("GatewayPage.installState_running");

    // 安裝前的舊狀態（succeeded）晚回來：不能誤報成功、不能同步規則、不能停止輪詢
    await act(async () => { staleRefresh.resolve(status("succeeded")); });
    await flush();
    expect(mocks.toast.success).not.toHaveBeenCalledWith("GatewayPage.toastInstallSucceeded");
    expect(mocks.syncRules).not.toHaveBeenCalled();
    expect(host.textContent).toContain("GatewayPage.installState_running");

    await act(async () => { await mocks.autoRefresh(); });
    await flush();
    expect(mocks.getInstallStatus).toHaveBeenCalledTimes(3);
    expect(mocks.syncRules).not.toHaveBeenCalled();

    // 真的裝完：提示一次成功並同步規則一次
    await act(async () => { await mocks.autoRefresh(); });
    await flush();
    expect(mocks.getInstallStatus).toHaveBeenCalledTimes(4);
    expect(mocks.toast.success).toHaveBeenCalledWith("GatewayPage.toastInstallSucceeded");
    expect(mocks.syncRules).toHaveBeenCalledTimes(1);

    await act(async () => { await mocks.autoRefresh(); });
    await flush();
    expect(mocks.getInstallStatus).toHaveBeenCalledTimes(4);
    expect(mocks.syncRules).toHaveBeenCalledTimes(1);
  });
});
