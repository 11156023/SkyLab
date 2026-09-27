// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test } from "vitest";
import useBodyScrollLock, { useModalScrollLock } from "./useBodyScrollLock";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;
const html = document.documentElement;

function Watcher() {
  useModalScrollLock();
  return null;
}

function Drawer({ open }) {
  useBodyScrollLock(open);
  return null;
}

function setScrollbarWidth(width) {
  Object.defineProperty(html, "clientWidth", { configurable: true, get: () => window.innerWidth - width });
}

async function render(element) {
  await act(async () => root.render(element));
}

/* MutationObserver 的回呼在微任務裡跑，等一輪再檢查 */
async function addModal() {
  const dialog = document.createElement("div");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  document.body.appendChild(dialog);
  await act(async () => {});
  return dialog;
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
  delete html.clientWidth;
  html.style.overflow = "";
  html.style.scrollbarGutter = "";
});

test("locks page scroll while a modal dialog is on screen and keeps the scrollbar gutter", async () => {
  setScrollbarWidth(15);
  await render(<Watcher />);
  expect(html.style.overflow).toBe("");

  const dialog = await addModal();
  expect(html.style.overflow).toBe("hidden");
  expect(html.style.scrollbarGutter).toBe("stable");

  dialog.remove();
  await act(async () => {});
  expect(html.style.overflow).toBe("");
  expect(html.style.scrollbarGutter).toBe("");
});

test("does not reserve a gutter when the page had no scrollbar", async () => {
  setScrollbarWidth(0);
  await render(<Watcher />);
  await addModal();
  expect(html.style.overflow).toBe("hidden");
  expect(html.style.scrollbarGutter).toBe("");
});

test("stays locked until both the drawer and the modal are gone", async () => {
  setScrollbarWidth(0);
  await render(<><Watcher /><Drawer open /></>);
  const dialog = await addModal();

  dialog.remove();
  await act(async () => {});
  expect(html.style.overflow).toBe("hidden");

  await render(<><Watcher /><Drawer open={false} /></>);
  expect(html.style.overflow).toBe("");
});
