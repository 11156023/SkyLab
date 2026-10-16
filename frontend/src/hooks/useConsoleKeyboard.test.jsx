// @vitest-environment happy-dom
import { act, useRef } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import useConsoleKeyboard, { shouldBlockBrowserShortcut } from "./useConsoleKeyboard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/* happy-dom 的 getModifierState("AltGraph") 直接回 altKey；Chrome 只有 AltGr 才是 true，這裡照 Chrome */
function key({ altGraph = false, ...init }, type = "keydown") {
  const event = new KeyboardEvent(type, { bubbles: true, cancelable: true, ...init });
  Object.defineProperty(event, "getModifierState", { value: (name) => name === "AltGraph" && altGraph });
  return event;
}

describe("shouldBlockBrowserShortcut", () => {
  it.each([
    ["Ctrl+F find", { key: "f", ctrlKey: true }],
    ["Ctrl+L address bar", { key: "l", ctrlKey: true }],
    ["Ctrl+R reload", { key: "r", ctrlKey: true }],
    ["Ctrl+Shift+J devtools", { key: "J", ctrlKey: true, shiftKey: true }],
    ["Ctrl+1 first tab", { key: "1", ctrlKey: true }],
    ["Ctrl+- zoom", { key: "-", ctrlKey: true }],
    ["F5 reload", { key: "F5" }],
    ["F12 devtools", { key: "F12" }],
    ["Alt+D address bar", { key: "d", altKey: true }],
    ["Alt+Left back", { key: "ArrowLeft", altKey: true }],
  ])("blocks %s", (_label, init) => {
    expect(shouldBlockBrowserShortcut(key(init), false)).toBe(true);
  });

  it("blocks releasing Alt alone, which would focus the Chrome menu", () => {
    expect(shouldBlockBrowserShortcut(key({ key: "Alt" }, "keyup"), false)).toBe(true);
  });

  it.each([
    ["plain letters", { key: "a" }],
    ["Enter", { key: "Enter" }],
    ["Cmd/Win combos", { key: "c", metaKey: true }],
    ["AltGr (Ctrl+Alt) characters", { key: "@", ctrlKey: true, altKey: true }],
    ["AltGraph characters", { key: "€", altKey: true, altGraph: true }],
    ["IME composition", { key: "Process", ctrlKey: true, isComposing: true }],
  ])("leaves %s alone", (_label, init) => {
    expect(shouldBlockBrowserShortcut(key(init), false)).toBe(false);
  });

  it("leaves Option combos alone on macOS so accented characters still compose", () => {
    expect(shouldBlockBrowserShortcut(key({ key: "e", altKey: true }), true)).toBe(false);
    expect(shouldBlockBrowserShortcut(key({ key: "r", ctrlKey: true }), true)).toBe(true);
  });
});

describe("useConsoleKeyboard", () => {
  let host;
  let root;
  let lock;
  let unlock;

  function Console({ active, allowDefault }) {
    const surfaceRef = useRef(null);
    useConsoleKeyboard(surfaceRef, { active, allowDefault });
    return (
      <div data-testid="dialog">
        <div ref={surfaceRef}><textarea data-testid="surface-input" /></div>
        <input data-testid="outside" />
      </div>
    );
  }

  async function render(props) {
    await act(async () => root.render(<Console {...props} />));
  }

  function dispatch(testId, init) {
    const event = key(init);
    host.querySelector(`[data-testid="${testId}"]`).dispatchEvent(event);
    return event;
  }

  beforeEach(() => {
    lock = vi.fn().mockResolvedValue(undefined);
    unlock = vi.fn();
    Object.defineProperty(navigator, "keyboard", { configurable: true, value: { lock, unlock } });
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
    delete navigator.keyboard;
    Object.defineProperty(document, "fullscreenElement", { configurable: true, value: null });
  });

  it("blocks browser shortcuts only while focus is inside the console surface", async () => {
    await render({ active: true });
    expect(dispatch("surface-input", { key: "w", ctrlKey: true }).defaultPrevented).toBe(true);
    expect(dispatch("outside", { key: "w", ctrlKey: true }).defaultPrevented).toBe(false);
  });

  it("does nothing until the console is connected", async () => {
    await render({ active: false });
    expect(dispatch("surface-input", { key: "f", ctrlKey: true }).defaultPrevented).toBe(false);
  });

  it("keeps the browser action for keys the console asks to pass through", async () => {
    const allowDefault = (event) => event.ctrlKey && event.shiftKey && event.code === "KeyV";
    await render({ active: true, allowDefault });
    expect(dispatch("surface-input", { key: "V", code: "KeyV", ctrlKey: true, shiftKey: true }).defaultPrevented).toBe(false);
    expect(dispatch("surface-input", { key: "C", code: "KeyC", ctrlKey: true, shiftKey: true }).defaultPrevented).toBe(true);
  });

  it("locks the keyboard while the dialog holding the console is fullscreen", async () => {
    await render({ active: true });
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      value: host.querySelector('[data-testid="dialog"]'),
    });
    await act(async () => document.dispatchEvent(new Event("fullscreenchange")));
    expect(lock).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "fullscreenElement", { configurable: true, value: null });
    await act(async () => document.dispatchEvent(new Event("fullscreenchange")));
    expect(unlock).toHaveBeenCalled();
  });

  it("asks before leaving the page while connected", async () => {
    await render({ active: true });
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);

    await render({ active: false });
    const after = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(false);
  });
});
