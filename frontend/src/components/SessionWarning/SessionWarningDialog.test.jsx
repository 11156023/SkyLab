// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import SessionWarningDialog from "./SessionWarningDialog";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));
vi.mock("../../services/resources", () => ({ ResourcesService: { extendSession: vi.fn() } }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

const autoStop = { vmid: 101, warn_reason: "auto_stop", minutes_until_stop: 10, can_extend: true };

function pressEscape() {
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
}

test("renders as a labelled alert dialog with only the available extension action", async () => {
  await act(async () => root.render(<SessionWarningDialog status={autoStop} onClose={() => {}} />));
  const dialog = document.querySelector("[aria-modal='true']");
  expect(dialog.getAttribute("role")).toBe("alertdialog");
  expect(document.getElementById(dialog.getAttribute("aria-labelledby")).textContent).toBe("SessionWarningDialog.autoStopTitle");
  expect(document.getElementById(dialog.getAttribute("aria-describedby")).textContent).toContain("SessionWarningDialog.autoStopMinutes");
  expect(dialog.textContent).toContain("SessionWarningDialog.extendUsageTime");
  expect(dialog.textContent).not.toContain("SessionWarningDialog.later");
  expect(dialog.querySelector("input[type='checkbox']")).toBeNull();
});

test("Escape closes the already-recorded warning", async () => {
  const onClose = vi.fn();
  await act(async () => root.render(<SessionWarningDialog status={autoStop} onClose={onClose} />));
  await act(async () => pressEscape());
  expect(onClose).toHaveBeenCalledOnce();
});

test("a warning without extension support has no footer action", async () => {
  const expiry = {
    vmid: 102,
    warn_reason: "expiry",
    hours_until_expiry: 12,
    can_extend: false,
  };
  await act(async () => root.render(<SessionWarningDialog status={expiry} onClose={() => {}} />));
  const dialog = document.querySelector("[aria-modal='true']");
  expect(dialog.textContent).not.toContain("SessionWarningDialog.gotIt");
  expect(dialog.textContent).not.toContain("SessionWarningDialog.extendUsageTime");
  expect(dialog.querySelectorAll("button")).toHaveLength(1);
});
