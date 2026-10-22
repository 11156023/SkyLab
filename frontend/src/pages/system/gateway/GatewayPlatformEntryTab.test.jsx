// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import GatewayPlatformEntryTab from "./GatewayPlatformEntryTab";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  getPlatformEntry: vi.fn(),
  getPlatformEntryStatus: vi.fn(),
  updatePlatformEntry: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/gateway", () => ({
  GatewayService: {
    getPlatformEntry: mocks.getPlatformEntry,
    getPlatformEntryStatus: mocks.getPlatformEntryStatus,
    updatePlatformEntry: mocks.updatePlatformEntry,
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));

let root;
let host;

/* TanStack Query 以 macrotask 批次通知，先等一個 timer tick 再看畫面 */
async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 8; i += 1) await Promise.resolve();
  });
}

function deferred() {
  let resolve;
  const promise = new Promise((res) => { resolve = res; });
  return { promise, resolve };
}

const config = {
  enabled: false,
  domain: "lab.example.edu",
  upstream_host: "10.0.0.5",
  upstream_port: 8082,
  enable_https: false,
  dns_proxied: false,
  dns_managed: false,
  gateway_ready: true,
  gateway_host: "203.0.113.10",
  certificate_configured: false,
};
const status = (applied) => ({
  applied,
  applied_domain: applied ? "lab.example.edu" : null,
  applied_upstream: applied ? "10.0.0.5:8082" : null,
  applied_https: false,
  upstream_reachable: true,
});

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("GatewayPlatformEntryTab status check", () => {
  test("a refresh requested while a check is running is queued, so the post-save status shows", async () => {
    const preSave = deferred();
    mocks.getPlatformEntry.mockResolvedValue(config);
    mocks.updatePlatformEntry.mockResolvedValue({ ...config, enabled: true });
    mocks.getPlatformEntryStatus
      .mockReturnValueOnce(preSave.promise)
      .mockResolvedValueOnce(status(true));

    await act(async () => {
      root.render(withQueryClient(<GatewayPlatformEntryTab gatewayReady onGoToConnection={() => {}} onGoToCertificate={() => {}} />));
    });
    await flush();
    expect(mocks.getPlatformEntryStatus).toHaveBeenCalledTimes(1);

    // 檢查還在跑時啟用並儲存：儲存後的刷新不能被丟掉
    const enabled = host.querySelector('input[type="checkbox"]');
    await act(async () => { enabled.click(); });
    await act(async () => {
      host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(mocks.updatePlatformEntry).toHaveBeenCalledTimes(1);

    // 儲存前的檢查結果（尚未套用）晚回來
    await act(async () => { preSave.resolve(status(false)); });
    await flush();

    expect(mocks.getPlatformEntryStatus).toHaveBeenCalledTimes(2);
    expect(host.textContent).not.toContain("GatewayPage.platformWarnDrift");
    expect(host.textContent).toContain("lab.example.edu");
  });

  test("a load failure shows the retry state, and retrying fills the form", async () => {
    mocks.getPlatformEntry
      .mockRejectedValueOnce(new Error("ssh down"))
      .mockResolvedValueOnce(config);
    mocks.getPlatformEntryStatus.mockResolvedValue(status(true));

    await act(async () => {
      root.render(withQueryClient(<GatewayPlatformEntryTab gatewayReady onGoToConnection={() => {}} onGoToCertificate={() => {}} />));
    });
    await flush();
    expect(mocks.toast.error).toHaveBeenCalledWith("ssh down");
    expect(mocks.getPlatformEntryStatus).not.toHaveBeenCalled();

    const retry = [...host.querySelectorAll("button")].find((b) => b.textContent.includes("Error.retry"));
    await act(async () => { retry.click(); });
    await flush();

    expect(host.querySelector("form")).toBeTruthy();
    expect(host.querySelector('input[placeholder="skylab.example.com"]').value).toBe("lab.example.edu");
    expect(mocks.getPlatformEntryStatus).toHaveBeenCalledTimes(1);
  });
});
