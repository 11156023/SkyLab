// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const disableTotp = vi.fn();
const deleteAccount = vi.fn();
const updateUser = vi.fn();
const logout = vi.fn();

/* t 必須是穩定參考 */
const translation = { t: (key) => key, i18n: { language: "zh-TW" } };
vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => translation }));
vi.mock("../../../contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { email: "amy@x.edu", full_name: "Amy", totp_enabled: true, totp_required: false },
    updateUser,
    logout,
  }),
}));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));
vi.mock("../../../services/account", () => ({
  AccountService: {
    disableTotp: (...args) => disableTotp(...args),
    delete: (...args) => deleteAccount(...args),
  },
}));
vi.mock("../../../components/TotpEnrollment/TotpEnrollment", () => ({ default: () => null }));
vi.mock("../../../components/FileDropzone/FileDropzone", () => ({ default: () => null }));
vi.mock("./AppearanceTab", () => ({ default: () => null }));

const { default: AccountSettingsPage } = await import("./AccountSettingsPage");

let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

const buttonByText = (scope, text) => [...scope.querySelectorAll("button")].find((b) => b.textContent.includes(text));
const dialog = () => document.body.querySelector("[role='dialog'], [role='alertdialog']");
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function typeInto(input, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

it("opens the delete-account dialog in the shared Modal, gates the button on the confirm word and closes on Escape", async () => {
  await act(async () => root.render(<AccountSettingsPage />));
  await act(async () => buttonByText(host, "DangerZoneTab.deleteAccount").click());

  const box = dialog();
  expect(box).toBeTruthy();
  expect(box.getAttribute("role")).toBe("alertdialog");
  expect(box.getAttribute("aria-modal")).toBe("true");
  expect(document.getElementById(box.getAttribute("aria-labelledby")).textContent).toContain("DangerZoneTab.confirmTitle");
  const confirm = buttonByText(box, "DangerZoneTab.confirmDelete");
  expect(confirm.disabled).toBe(true);

  await act(async () => typeInto(box.querySelector("input"), "DangerZoneTab.confirmWord"));
  expect(buttonByText(dialog(), "DangerZoneTab.confirmDelete").disabled).toBe(false);

  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
  await act(async () => wait(200));
  expect(dialog()).toBeNull();

  /* 取消後重開：確認字已清空，按鈕回到停用 */
  await act(async () => buttonByText(host, "DangerZoneTab.deleteAccount").click());
  expect(dialog().querySelector("input").value).toBe("");
  expect(buttonByText(dialog(), "DangerZoneTab.confirmDelete").disabled).toBe(true);
});

it("submits the TOTP-disable form with the six digits and closes the dialog", async () => {
  disableTotp.mockResolvedValue({});
  await act(async () => root.render(<AccountSettingsPage />));
  await act(async () => buttonByText(host, "TwoFactorSection.disable").click());

  const form = dialog();
  expect(form.tagName).toBe("FORM");
  await act(async () => typeInto(form.querySelector("input"), "123 456"));
  await act(async () => buttonByText(form, "TwoFactorSection.confirmDisable").click());

  expect(disableTotp).toHaveBeenCalledWith("123456");
  expect(updateUser).toHaveBeenCalledWith({ totp_enabled: false });
  await act(async () => wait(200));
  expect(dialog()).toBeNull();
});

it("keeps the TOTP-disable dialog open on Escape while the request is in flight", async () => {
  let resolve;
  disableTotp.mockReturnValue(new Promise((r) => { resolve = r; }));
  await act(async () => root.render(<AccountSettingsPage />));
  await act(async () => buttonByText(host, "TwoFactorSection.disable").click());
  await act(async () => typeInto(dialog().querySelector("input"), "123456"));
  await act(async () => buttonByText(dialog(), "TwoFactorSection.confirmDisable").click());

  await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));
  await act(async () => wait(200));
  expect(dialog()).toBeTruthy();

  await act(async () => resolve({}));
  await act(async () => wait(200));
  expect(dialog()).toBeNull();
});
