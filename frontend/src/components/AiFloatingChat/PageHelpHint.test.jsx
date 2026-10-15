// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, expect, test, vi } from "vitest";
import PageHelpHint from "./PageHelpHint";
import translations from "../../locales/zh-TW/components.json";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => translations[key] ?? key }) }));
let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers(); localStorage.clear();
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove(); document.querySelectorAll("[data-test-modal], [data-test-focus]").forEach((el) => el.remove());
  vi.restoreAllMocks(); vi.useRealTimers();
});
async function render(props = {}) {
  await act(async () => root.render(<PageHelpHint enabled pageKey="my-resources" userId="student-1" {...props} />));
}
async function advance(ms) { await act(async () => vi.advanceTimersByTimeAsync(ms)); }

test("shows a passive hint without a dialog or focus change, then automatically hides", async () => {
  const input = document.createElement("input"); input.dataset.testFocus = "";
  document.body.append(input); input.focus();
  await render();
  expect(host.textContent).toBe("");
  await advance(1500);
  expect(host.querySelector('[role="status"]').textContent).toBe(translations["AiFloatingChat.pageHelpHint"]);
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(document.activeElement).toBe(input);
  await advance(8000);
  expect(host.textContent).toBe("");
});

test("dismisses and does not repeat the same page after reopening or remounting", async () => {
  await render(); await advance(1500);
  await act(async () => host.querySelector('button[aria-label="關閉提示"]').click());
  expect(host.textContent).toBe("");
  await render({ enabled: false }); await render(); await advance(1500);
  expect(host.textContent).toBe("");
  await act(async () => root.unmount()); root = createRoot(host);
  await render(); await advance(1500);
  expect(host.textContent).toBe("");
});

test("tracks page types and accounts independently", async () => {
  await render(); await advance(1500);
  await render({ pageKey: "firewall" }); await advance(1500);
  expect(host.querySelector('[role="status"]')).not.toBeNull();
  await render({ userId: "student-2" }); await advance(1500);
  expect(host.querySelector('[role="status"]')).not.toBeNull();
});

test("waits until a spotlight tour or modal closes", async () => {
  const modal = document.createElement("div"); modal.setAttribute("aria-modal", "true");
  modal.dataset.testModal = ""; document.body.append(modal);
  await render(); await advance(4000);
  expect(host.textContent).toBe("");
  await act(async () => modal.remove()); await advance(1500);
  expect(host.querySelector('[role="status"]')).not.toBeNull();
});

test("suppresses the hint while the assistant is open or replying and cancels old-page timers", async () => {
  await render({ enabled: false }); await advance(4000);
  expect(host.textContent).toBe("");
  await render(); await advance(1000);
  await render({ pageKey: "firewall" }); await advance(500);
  expect(host.textContent).toBe("");
  expect(localStorage.getItem("skylab:page-help-hint:student-1:my-resources")).toBeNull();
  await advance(1000);
  expect(host.querySelector('[role="status"]')).not.toBeNull();
});

test("storage failure still shows the hint once during this mounted session", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  await render(); await advance(1500);
  expect(host.querySelector('[role="status"]')).not.toBeNull();
  await render({ enabled: false }); await render(); await advance(1500);
  expect(host.textContent).toBe("");
});


test("only activating the hint invokes the assistant action", async () => {
  const onActivate = vi.fn();
  await render({ onActivate }); await advance(1500);
  expect(onActivate).not.toHaveBeenCalled();
  await act(async () => host.querySelector("button:not([aria-label])").click());
  expect(onActivate).toHaveBeenCalledTimes(1);
  expect(host.textContent).toBe("");
});
