// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const account = vi.hoisted(() => ({ setupTotp: vi.fn(), confirmTotp: vi.fn() }));

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));
vi.mock("../../services/account", () => ({ AccountService: account }));
vi.mock("qrcode", () => ({ default: { toDataURL: vi.fn(async () => "data:image/png;base64,AA==") } }));

import TotpEnrollment from "./TotpEnrollment";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

beforeEach(() => {
  account.setupTotp.mockReset();
  account.confirmTotp.mockReset();
  account.setupTotp.mockResolvedValue({
    secret: "ABCDEFGHIJKLMNOP",
    otpauth_uri: "otpauth://totp/x",
    account: "a@b.c",
    issuer: "SkyLab",
  });
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

function typeInto(input, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

test("after a rejected code the cleared input gets focus back", async () => {
  account.confirmTotp.mockRejectedValue(new Error("invalid"));
  await act(async () => root.render(<TotpEnrollment onConfirmed={() => {}} />));

  const input = document.querySelector("input[autocomplete='one-time-code']");
  await act(async () => typeInto(input, "123456"));
  input.blur();
  expect(document.activeElement).not.toBe(input);

  await act(async () => document.querySelector("form").requestSubmit());

  expect(account.confirmTotp).toHaveBeenCalledWith("123456");
  expect(input.disabled).toBe(false);
  expect(input.value).toBe("");
  expect(document.activeElement).toBe(input);
});
