// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import PlatformEntryStep from "./PlatformEntryStep";
import { setupKeys } from "../../queries/setup";
import { createTestQueryClient, withQueryClient } from "../../queries/testing";

const mocks = vi.hoisted(() => ({
  getPlatformEntry: vi.fn(),
  savePlatformEntry: vi.fn(),
  testPlatformEntryUpstream: vi.fn(),
  t: (key) => key,
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: mocks.t }),
}));
vi.mock("../../services/setup", () => ({
  SetupService: {
    getPlatformEntry: mocks.getPlatformEntry,
    savePlatformEntry: mocks.savePlatformEntry,
    testPlatformEntryUpstream: mocks.testPlatformEntryUpstream,
  },
}));
vi.mock("../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("../../components/LoadingState/LoadingState", () => ({
  LoadingSpinner: () => <div data-testid="spinner" />,
}));

const ENTRY = {
  enabled: false,
  domain: "",
  upstream_host: "",
  upstream_port: 8082,
  enable_https: false,
  dns_proxied: false,
  certificate_configured: false,
  gateway_host: "10.0.0.2",
};

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

let host;
let root;
let client;
let onSaved;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = createTestQueryClient();
  onSaved = vi.fn();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
});

async function renderStep(gatewayReady) {
  await act(async () => {
    root.render(withQueryClient(
      <PlatformEntryStep gatewayReady={gatewayReady} onSaved={onSaved} onSkip={vi.fn()} onBack={vi.fn()} onNext={vi.fn()} />,
      client,
    ));
  });
  await flush();
}

const input = (placeholder) => host.querySelector(`input[placeholder='${placeholder}']`);

function typeInto(el, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("PlatformEntryStep", () => {
  test("does not read the settings before the Gateway step is done", async () => {
    await renderStep(false);
    expect(mocks.getPlatformEntry).not.toHaveBeenCalled();
    expect(host.textContent).toContain("SetupPage.platformNeedsGateway");
  });

  test("a failed read shows its error with back / skip", async () => {
    mocks.getPlatformEntry.mockRejectedValue(new Error("gateway unreachable"));
    await renderStep(true);
    expect(host.querySelector("[data-testid='spinner']")).toBeNull();
    expect(host.textContent).toContain("gateway unreachable");
    expect(host.querySelector("form")).toBeNull();
  });

  test("saving keeps what was submitted and treats the step as done", async () => {
    mocks.getPlatformEntry.mockResolvedValue(ENTRY);
    const saved = { ...ENTRY, enabled: true, domain: "skylab.example.com", upstream_host: "192.168.100.20" };
    mocks.savePlatformEntry.mockResolvedValue(saved);
    await renderStep(true);

    typeInto(input("skylab.example.com"), "skylab.example.com");
    typeInto(input("192.168.100.20"), "192.168.100.20");
    await act(async () => {
      host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();

    expect(mocks.savePlatformEntry).toHaveBeenCalledWith({
      enabled: true,
      domain: "skylab.example.com",
      upstream_host: "192.168.100.20",
      upstream_port: 8082,
      enable_https: false,
      dns_proxied: false,
    });
    expect(onSaved).toHaveBeenCalledWith(saved);
    expect(client.getQueryData(setupKeys.platformEntry)).toEqual(saved);
    expect(input("skylab.example.com").value).toBe("skylab.example.com");
    expect(host.textContent).toContain("SetupPage.platformDoneNotice");
    expect(mocks.getPlatformEntry).toHaveBeenCalledTimes(1);
  });
});
