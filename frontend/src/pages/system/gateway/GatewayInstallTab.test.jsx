// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import GatewayInstallTab from "./GatewayInstallTab";
import { withQueryClient } from "../../../queries/testing";
import { INSTALL_POLL_INTERVAL } from "../../../queries/gateway";

const mocks = vi.hoisted(() => ({
  getInstallStatus: vi.fn(),
  startInstall: vi.fn(),
  syncRules: vi.fn(),
  confirm: vi.fn(),
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
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));

let root;
let host;

/* 輪詢間隔走真的 refetchInterval：用假計時器推進時間；TanStack Query 以 macrotask 批次通知，flush 推 0 毫秒 */
async function advance(ms) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

function flush() {
  return advance(0);
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
  vi.useFakeTimers();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.syncRules.mockResolvedValue({ message: "synced" });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
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

    await act(async () => { root.render(withQueryClient(<GatewayInstallTab gatewayReady onGoToConnection={() => {}} />)); });
    await flush();
    expect(host.textContent).toContain("GatewayPage.installState_succeeded");

    // 已經裝好、不在安裝中：不輪詢
    await advance(INSTALL_POLL_INTERVAL);
    expect(mocks.getInstallStatus).toHaveBeenCalledTimes(1);

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

    // 安裝中每 3 秒問一次
    await advance(INSTALL_POLL_INTERVAL);
    expect(mocks.getInstallStatus).toHaveBeenCalledTimes(3);
    expect(mocks.syncRules).not.toHaveBeenCalled();

    // 真的裝完：提示一次成功並同步規則一次
    await advance(INSTALL_POLL_INTERVAL);
    expect(mocks.getInstallStatus).toHaveBeenCalledTimes(4);
    expect(mocks.toast.success).toHaveBeenCalledWith("GatewayPage.toastInstallSucceeded");
    expect(mocks.syncRules).toHaveBeenCalledTimes(1);

    // 裝完就不再輪詢
    await advance(INSTALL_POLL_INTERVAL * 2);
    expect(mocks.getInstallStatus).toHaveBeenCalledTimes(4);
    expect(mocks.syncRules).toHaveBeenCalledTimes(1);
  });

  test("a background poll failure keeps the last status on screen", async () => {
    mocks.getInstallStatus
      .mockResolvedValueOnce(status("running"))
      .mockRejectedValueOnce(new Error("ssh timeout"));

    await act(async () => { root.render(withQueryClient(<GatewayInstallTab gatewayReady onGoToConnection={() => {}} />)); });
    await flush();
    await advance(INSTALL_POLL_INTERVAL);

    expect(mocks.getInstallStatus).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("GatewayPage.installState_running");
    expect(host.textContent).not.toContain("GatewayPage.installConnectErrorTitle");
  });

  test("a first-load failure shows the error with a retry button", async () => {
    mocks.getInstallStatus
      .mockRejectedValueOnce(new Error("ssh refused"))
      .mockResolvedValueOnce(status("idle"));

    await act(async () => { root.render(withQueryClient(<GatewayInstallTab gatewayReady onGoToConnection={() => {}} />)); });
    await flush();
    expect(host.textContent).toContain("ssh refused");

    await act(async () => { buttonByText("GatewayPage.installRetry").click(); });
    await flush();
    expect(host.textContent).toContain("GatewayPage.installState_idle");
  });
});
