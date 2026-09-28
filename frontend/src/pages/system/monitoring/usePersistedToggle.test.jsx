// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test } from "vitest";
import usePersistedToggle, { loadPersistedOpen } from "./usePersistedToggle";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const KEY = "skylab.test.toggle";
let host;
let root;
let latest;

function Probe({ storageKey }) {
  latest = usePersistedToggle(storageKey);
  return null;
}

beforeEach(() => {
  window.localStorage.clear();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
  window.localStorage.clear();
});

test("沒記過時預設收起", async () => {
  await act(async () => root.render(<Probe storageKey={KEY} />));
  expect(latest[0]).toBe(false);
});

test("讀取既有偏好，切換後以 1/0 寫回同一個 key", async () => {
  window.localStorage.setItem(KEY, "1");
  await act(async () => root.render(<Probe storageKey={KEY} />));
  expect(latest[0]).toBe(true);
  await act(async () => latest[1]());
  expect(latest[0]).toBe(false);
  expect(window.localStorage.getItem(KEY)).toBe("0");
  await act(async () => latest[1]());
  expect(window.localStorage.getItem(KEY)).toBe("1");
});

test("localStorage 丟例外時當作收起", () => {
  const original = Object.getOwnPropertyDescriptor(window, "localStorage");
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    get() {
      throw new Error("blocked");
    },
  });
  try {
    expect(loadPersistedOpen(KEY)).toBe(false);
  } finally {
    Object.defineProperty(window, "localStorage", original);
  }
});
