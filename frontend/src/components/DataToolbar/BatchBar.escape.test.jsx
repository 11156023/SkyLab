// @vitest-environment happy-dom
/*
 * 批量列的 Esc 清除選取，與選單（useOutsideClick escape）的 Esc 關閉要分開：
 * 選單在 document 上關掉自己會同步重繪，批量列在同一個事件傳到 window 前就把監聽掛回去，
 * 結果按 Esc 只想關「更多」選單，整個選取也被清掉。選單處理過的 Esc 以 preventDefault 標記，批量列略過它。
 */
import { act, useRef } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

vi.mock("react-i18next", async (importOriginal) => ({ ...await importOriginal(), useTranslation: () => ({ t: (key) => key }) }));

import BatchBar from "./BatchBar";
import useOutsideClick from "../../hooks/useOutsideClick";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const pressEscape = (target = document.body) => {
  const event = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
  act(() => { target.dispatchEvent(event); });
  return event;
};

test("Escape clears the selection", () => {
  const onClear = vi.fn();
  act(() => { root.render(<BatchBar countLabel="3 selected" onClear={onClear} />); });
  pressEscape();
  expect(onClear).toHaveBeenCalledTimes(1);
});

test("an Escape a menu already handled (defaultPrevented) does not clear the selection", () => {
  const onClear = vi.fn();
  act(() => { root.render(<BatchBar countLabel="3 selected" onClear={onClear} />); });
  const closeMenu = (event) => { if (event.key === "Escape") event.preventDefault(); };
  document.addEventListener("keydown", closeMenu);
  try {
    pressEscape();
  } finally {
    document.removeEventListener("keydown", closeMenu);
  }
  expect(onClear).not.toHaveBeenCalled();
});

function Menu({ onClose }) {
  const ref = useRef(null);
  useOutsideClick(ref, null, onClose, { escape: true });
  return <div ref={ref}>menu</div>;
}

test("useOutsideClick marks the Escape it closes on as handled", () => {
  const onClose = vi.fn();
  act(() => { root.render(<Menu onClose={onClose} />); });
  const event = pressEscape();
  expect(onClose).toHaveBeenCalledTimes(1);
  expect(event.defaultPrevented).toBe(true);
});

test("useOutsideClick without escape leaves the Escape alone", () => {
  const onClose = vi.fn();
  function PlainMenu() {
    const ref = useRef(null);
    useOutsideClick(ref, null, onClose);
    return <div ref={ref}>menu</div>;
  }
  act(() => { root.render(<PlainMenu />); });
  const event = pressEscape();
  expect(onClose).not.toHaveBeenCalled();
  expect(event.defaultPrevented).toBe(false);
});
