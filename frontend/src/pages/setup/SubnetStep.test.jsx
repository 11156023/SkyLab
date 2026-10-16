// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  configureSubnet: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: (key) => key }),
}));
vi.mock("../../services/setup", () => ({
  SetupService: { configureSubnet: mocks.configureSubnet },
}));
vi.mock("../../hooks/useToast", () => ({ useToast: () => mocks.toast }));

import { SubnetStep } from "./SetupPage";

let container;
let root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.configureSubnet.mockReset();
  mocks.configureSubnet.mockResolvedValue({ total_ips: 253 });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function inputByPlaceholder(key) {
  return container.querySelector(`input[placeholder="${key}"]`);
}

function typeInto(input, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  act(() => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function fillAndSubmit(platformIp) {
  typeInto(inputByPlaceholder("SetupPage.cidrPlaceholder"), "10.10.0.0/24");
  typeInto(inputByPlaceholder("SetupPage.gatewayPlaceholder"), "10.10.0.1");
  typeInto(inputByPlaceholder("SetupPage.gatewayVmIpPlaceholder"), "10.10.0.2");
  typeInto(inputByPlaceholder("SetupPage.platformIpPlaceholder"), platformIp);
  await act(async () => {
    container.querySelector("form").dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
  });
}

describe("SubnetStep 主系統 IP", () => {
  test("欄位為選填，填了就隨 payload 送出", async () => {
    const onSaved = vi.fn();
    act(() => root.render(<SubnetStep alreadyDone={false} onSaved={onSaved} />));

    const input = inputByPlaceholder("SetupPage.platformIpPlaceholder");
    expect(input).not.toBeNull();
    expect(input.required).toBe(false);
    expect(container.textContent).toContain("SetupPage.platformIpLabel");

    await fillAndSubmit(" 10.10.0.3 ");

    expect(mocks.configureSubnet).toHaveBeenCalledWith(
      expect.objectContaining({ gateway_vm_ip: "10.10.0.2", platform_ip: "10.10.0.3" }),
    );
    expect(onSaved).toHaveBeenCalledWith({ total_ips: 253 });
  });

  test("留空時送 null", async () => {
    act(() => root.render(<SubnetStep alreadyDone={false} onSaved={vi.fn()} />));

    await fillAndSubmit("");

    expect(mocks.configureSubnet).toHaveBeenCalledWith(
      expect.objectContaining({ platform_ip: null }),
    );
  });
});
