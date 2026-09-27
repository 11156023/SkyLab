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

test("renders as a labelled alert dialog with the remaining time in the description", async () => {
  await act(async () => root.render(<SessionWarningDialog status={autoStop} onClose={() => {}} onDismissPermanent={() => {}} />));
  const dialog = document.querySelector("[aria-modal='true']");
  expect(dialog.getAttribute("role")).toBe("alertdialog");
  expect(document.getElementById(dialog.getAttribute("aria-labelledby")).textContent).toBe("SessionWarningDialog.autoStopTitle");
  expect(document.getElementById(dialog.getAttribute("aria-describedby")).textContent).toContain("SessionWarningDialog.autoStopMinutes");
});

test("Escape counts as 'later', and remembers the choice when the checkbox is ticked", async () => {
  const onClose = vi.fn();
  const onDismissPermanent = vi.fn();
  await act(async () => root.render(<SessionWarningDialog status={autoStop} onClose={onClose} onDismissPermanent={onDismissPermanent} />));
  await act(async () => pressEscape());
  expect(onClose).toHaveBeenCalledOnce();

  await act(async () => document.querySelector("input[type='checkbox']").click());
  await act(async () => pressEscape());
  expect(onDismissPermanent).toHaveBeenCalledOnce();
});
