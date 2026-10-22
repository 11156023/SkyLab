// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import GatewayCertificateTab from "./GatewayCertificateTab";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  getCertificate: vi.fn(),
  getCertificateStatus: vi.fn(),
  updateCertificate: vi.fn(),
  confirm: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/gateway", () => ({
  GatewayService: {
    getCertificate: mocks.getCertificate,
    getCertificateStatus: mocks.getCertificateStatus,
    updateCertificate: mocks.updateCertificate,
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

function buttonByText(text) {
  return [...host.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

const config = {
  configured: true,
  ssl_certificate_path: "/etc/ssl/skylab/fullchain.pem",
  ssl_certificate_key_path: "/etc/ssl/skylab/privkey.pem",
};
const healthyStatus = {
  configured: true,
  cert_readable: true,
  key_readable: true,
  cert_valid: true,
  key_valid: true,
  key_matches: true,
  expires_at: "2099-01-01T00:00:00Z",
  dns_names: ["lab.example.edu"],
  covered_domains: ["lab.example.edu"],
  uncovered_domains: [],
};

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

describe("GatewayCertificateTab status check", () => {
  test("a refresh requested while a check is running is queued, so the post-save status shows", async () => {
    const preSave = deferred();
    mocks.getCertificate.mockResolvedValue(config);
    mocks.updateCertificate.mockResolvedValue(config);
    mocks.getCertificateStatus
      .mockReturnValueOnce(preSave.promise)
      .mockResolvedValueOnce(healthyStatus);

    await act(async () => { root.render(withQueryClient(<GatewayCertificateTab gatewayReady onGoToConnection={() => {}} />)); });
    await flush();
    expect(mocks.getCertificateStatus).toHaveBeenCalledTimes(1);

    // 檢查還在跑時按「重新套用」：儲存後的刷新不能被丟掉
    await act(async () => { buttonByText("GatewayPage.certReapply").click(); });
    await flush();
    expect(mocks.updateCertificate).toHaveBeenCalledTimes(1);

    await act(async () => { preSave.resolve({ configured: false }); });
    await flush();

    expect(mocks.getCertificateStatus).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("GatewayPage.certStatusAllGood");
    expect(host.textContent).not.toContain("GatewayPage.certWarnNotConfigured");
  });

  test("several refreshes during one check collapse into a single re-run", async () => {
    const first = deferred();
    mocks.getCertificate.mockResolvedValue(config);
    mocks.getCertificateStatus
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue(healthyStatus);

    await act(async () => { root.render(withQueryClient(<GatewayCertificateTab gatewayReady onGoToConnection={() => {}} />)); });
    await flush();

    const refreshButton = buttonByText("GatewayPage.installRefresh");
    expect(refreshButton.disabled).toBe(true);
    // 檢查中重新整理鈕會停用；改按兩次「重新套用」，在同一次檢查裡要求兩次刷新
    mocks.updateCertificate.mockResolvedValue(config);
    await act(async () => { buttonByText("GatewayPage.certReapply").click(); });
    await flush();
    await act(async () => { buttonByText("GatewayPage.certReapply").click(); });
    await flush();

    await act(async () => { first.resolve({ configured: false }); });
    await flush();

    expect(mocks.getCertificateStatus).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("GatewayPage.certStatusAllGood");
  });
});
