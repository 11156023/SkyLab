// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import GatewayStep from "./GatewayStep";
import { setupKeys } from "../../queries/setup";
import { createTestQueryClient, withQueryClient } from "../../queries/testing";

const mocks = vi.hoisted(() => ({
  getGateway: vi.fn(),
  getGatewayInstallStatus: vi.fn(),
  saveGateway: vi.fn(),
  testGateway: vi.fn(),
  startGatewayInstall: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../services/setup", () => ({
  SetupService: {
    getGateway: mocks.getGateway,
    getGatewayInstallStatus: mocks.getGatewayInstallStatus,
    saveGateway: mocks.saveGateway,
    testGateway: mocks.testGateway,
    startGatewayInstall: mocks.startGatewayInstall,
  },
}));
vi.mock("../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../components/LoadingState/LoadingState", () => ({
  LoadingSpinner: () => <div data-testid="spinner" />,
}));

const CONFIG = {
  host: "10.0.0.2",
  ssh_port: 22,
  ssh_user: "root",
  public_key: "ssh-ed25519 AAAA skylab",
  is_configured: true,
};

const INSTALL_IDLE = {
  state: "idle",
  exit_code: null,
  components: { nginx: false, wireguard: false, ufw: false },
  root_access: true,
  interfaces: [
    { name: "eth0", addresses: ["192.168.100.5"] },
    { name: "eth1", addresses: ["10.0.0.2"] },
  ],
  defaults: {
    ingress_interface: "eth0",
    vm_interface: "eth1",
    snat_address: "10.0.0.2",
    listen_port: 51821,
    forward_port_start: 30000,
    forward_port_end: 39999,
    monitoring_allow_from: [],
  },
  log: "",
};

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
}

/* 設定讀到之後才讀安裝狀態（兩段請求、兩次通知），所以多等幾輪 */
async function flush() {
  for (let round = 0; round < 3; round += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      for (let i = 0; i < 5; i += 1) await Promise.resolve();
    });
  }
}

let host;
let root;
let client;
let onConfigured;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = createTestQueryClient();
  onConfigured = vi.fn();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
});

async function renderStep() {
  await act(async () => {
    root.render(withQueryClient(
      <GatewayStep onConfigured={onConfigured} onSkip={vi.fn()} onBack={vi.fn()} onNext={vi.fn()} />,
      client,
    ));
  });
  await flush();
}

const spinner = () => host.querySelector("[data-testid='spinner']");
const hostInput = () => host.querySelector("input[placeholder='SetupPage.gatewayHostPlaceholder']");
const installSection = () => host.textContent.includes("SetupPage.gatewayInstallTitle");
const button = (text) => [...host.querySelectorAll("button")].find((b) => b.textContent.includes(text));

async function click(el) {
  await act(async () => { el.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
  await flush();
}

function typeInto(input, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("GatewayStep", () => {
  test("a configured gateway keeps the spinner until the install probe answers, then counts as connected", async () => {
    const probe = deferred();
    mocks.getGateway.mockResolvedValue(CONFIG);
    mocks.getGatewayInstallStatus.mockReturnValue(probe.promise);

    await renderStep();
    expect(spinner()).not.toBeNull();
    expect(hostInput()).toBeNull();

    await act(async () => { probe.resolve(INSTALL_IDLE); });
    await flush();
    expect(spinner()).toBeNull();
    expect(hostInput().value).toBe("10.0.0.2");
    expect(installSection()).toBe(true);
    expect(mocks.testGateway).not.toHaveBeenCalled();
  });

  test("a failed probe is silent; a successful connection test then loads the install status", async () => {
    mocks.getGateway.mockResolvedValue(CONFIG);
    mocks.getGatewayInstallStatus
      .mockRejectedValueOnce(new Error("probe failed"))
      .mockResolvedValue(INSTALL_IDLE);
    mocks.testGateway.mockResolvedValue({ success: true, message: "SSH OK" });

    await renderStep();
    expect(spinner()).toBeNull();
    expect(installSection()).toBe(false);
    expect(host.textContent).not.toContain("probe failed");

    await click(button("SetupPage.testConnection"));
    expect(mocks.getGatewayInstallStatus).toHaveBeenCalledTimes(2);
    expect(installSection()).toBe(true);
    expect(host.textContent).toContain("SSH OK");
  });

  test("the install read right after a successful test reports its failure", async () => {
    mocks.getGateway.mockResolvedValue(CONFIG);
    mocks.getGatewayInstallStatus.mockRejectedValue(new Error("ssh refused"));
    mocks.testGateway.mockResolvedValue({ success: true, message: "" });

    await renderStep();
    expect(host.textContent).not.toContain("ssh refused");

    await click(button("SetupPage.testConnection"));
    expect(host.textContent).toContain("ssh refused");
    expect(installSection()).toBe(false);
  });

  test("saving a new connection drops the install status and does not read it until tested", async () => {
    mocks.getGateway.mockResolvedValue(CONFIG);
    mocks.getGatewayInstallStatus.mockResolvedValue(INSTALL_IDLE);
    const saved = { ...CONFIG, host: "10.0.0.9" };
    mocks.saveGateway.mockResolvedValue(saved);

    await renderStep();
    expect(installSection()).toBe(true);

    typeInto(hostInput(), "10.0.0.9");
    await act(async () => {
      host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();

    expect(mocks.saveGateway).toHaveBeenCalledWith({ host: "10.0.0.9", ssh_port: 22, ssh_user: "root" });
    expect(onConfigured).toHaveBeenCalledWith(saved);
    expect(hostInput().value).toBe("10.0.0.9");
    expect(installSection()).toBe(false);
    expect(client.getQueryData(setupKeys.gatewayInstall)).toBeUndefined();
    expect(mocks.getGatewayInstallStatus).toHaveBeenCalledTimes(1);
    /* 存好的連線可以直接測試（沒有被當成未存的修改） */
    expect(button("SetupPage.testConnection").disabled).toBe(false);
  });

  test("a running install toasts once when a later read sees it finish", async () => {
    mocks.getGateway.mockResolvedValue(CONFIG);
    mocks.getGatewayInstallStatus
      .mockResolvedValueOnce({ ...INSTALL_IDLE, state: "running", log: "step 1" })
      .mockResolvedValue({ ...INSTALL_IDLE, state: "succeeded", log: "done" });

    await renderStep();
    expect(host.textContent).toContain("GatewayPage.installState_running");
    expect(mocks.toast.success).not.toHaveBeenCalled();

    await act(async () => { await client.refetchQueries({ queryKey: setupKeys.gatewayInstall }); });
    await flush();
    expect(host.textContent).toContain("GatewayPage.installState_succeeded");
    expect(mocks.toast.success).toHaveBeenCalledTimes(1);
    expect(mocks.toast.success).toHaveBeenCalledWith("GatewayPage.toastInstallSucceeded");
  });

  test("starting the install shows the returned running state", async () => {
    mocks.getGateway.mockResolvedValue(CONFIG);
    mocks.getGatewayInstallStatus.mockResolvedValue(INSTALL_IDLE);
    mocks.startGatewayInstall.mockResolvedValue({ ...INSTALL_IDLE, state: "running", log: "starting" });

    await renderStep();
    await click(button("GatewayPage.installStart"));

    expect(mocks.startGatewayInstall).toHaveBeenCalledWith(expect.objectContaining({
      ingress_interface: "eth0",
      vm_interface: "eth1",
    }));
    expect(host.textContent).toContain("GatewayPage.installState_running");
    expect(host.textContent).toContain("starting");
    expect(mocks.toast.success).toHaveBeenCalledWith("GatewayPage.toastInstallStarted");
  });

  test("a failed settings read shows its error under the empty form", async () => {
    mocks.getGateway.mockRejectedValue(new Error("boom"));

    await renderStep();
    expect(spinner()).toBeNull();
    expect(hostInput().value).toBe("");
    expect(host.textContent).toContain("boom");
    expect(mocks.getGatewayInstallStatus).not.toHaveBeenCalled();
  });
});
