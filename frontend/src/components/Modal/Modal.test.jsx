// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Modal from "./Modal";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));

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

async function render(element) {
  await act(async () => root.render(element));
}

function press(key, options = {}) {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...options });
  (document.activeElement ?? document.body).dispatchEvent(event);
  return event;
}

const dialog = () => document.querySelector("[aria-modal='true']");

test("renders into body as a labelled modal dialog", async () => {
  await render(<Modal title="重設密碼" description="新密碼只顯示一次" onClose={() => {}}>內容</Modal>);
  const el = dialog();
  expect(host.contains(el)).toBe(false);
  expect(el.getAttribute("role")).toBe("dialog");
  expect(document.getElementById(el.getAttribute("aria-labelledby")).textContent).toBe("重設密碼");
  expect(document.getElementById(el.getAttribute("aria-describedby")).textContent).toBe("新密碼只顯示一次");
});

test("Escape closes, but not while busy", async () => {
  const onClose = vi.fn();
  await render(<Modal title="A" onClose={onClose} busy />);
  await act(async () => press("Escape"));
  expect(onClose).not.toHaveBeenCalled();

  await render(<Modal title="A" onClose={onClose} />);
  await act(async () => press("Escape"));
  expect(onClose).toHaveBeenCalledOnce();
});

test("Escape only closes the topmost of stacked modals", async () => {
  const closeBottom = vi.fn();
  const closeTop = vi.fn();
  await render(
    <>
      <Modal title="底層" onClose={closeBottom} />
      <Modal title="上層" onClose={closeTop} />
    </>,
  );
  await act(async () => press("Escape"));
  expect(closeTop).toHaveBeenCalledOnce();
  expect(closeBottom).not.toHaveBeenCalled();
});

test("mousedown on the backdrop closes, inside the card does not", async () => {
  const onClose = vi.fn();
  await render(<Modal title="A" onClose={onClose}><input /></Modal>);
  await act(async () => {
    document.querySelector("input").dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  });
  expect(onClose).not.toHaveBeenCalled();
  await act(async () => {
    dialog().parentElement.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  });
  expect(onClose).toHaveBeenCalledOnce();
});

test("moves focus in, keeps autoFocus, and restores focus when closed", async () => {
  const trigger = document.createElement("button");
  document.body.appendChild(trigger);
  trigger.focus();

  await render(<Modal title="A" onClose={() => {}}><button type="button">取消</button></Modal>);
  expect(document.activeElement).toBe(dialog());

  await render(null);
  expect(document.activeElement).toBe(trigger);

  await render(<Modal title="A" onClose={() => {}}><button type="button">取消</button><button type="button" autoFocus>確定</button></Modal>);
  expect(document.activeElement.textContent).toBe("確定");
});

test("Tab wraps around inside the dialog", async () => {
  await render(
    <Modal title="A" onClose={() => {}}>
      <button type="button">第一</button>
      <button type="button">最後</button>
    </Modal>,
  );
  const [first, last] = dialog().querySelectorAll("button");
  /* happy-dom 沒有版面，getClientRects 一律為空；測試裡當成都看得到 */
  for (const el of [first, last]) el.getClientRects = () => [{}];

  last.focus();
  expect(press("Tab").defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(first);

  expect(press("Tab", { shiftKey: true }).defaultPrevented).toBe(true);
  expect(document.activeElement).toBe(last);
});

test("header layout renders a close button that honours busy", async () => {
  const onClose = vi.fn();
  await render(<Modal title="A" closeButton onClose={onClose} closeProps={{ "data-guide": "x" }} />);
  const close = document.querySelector("[data-guide='x']");
  expect(close.getAttribute("aria-label")).toBe("Modal.close");
  await act(async () => close.click());
  expect(onClose).toHaveBeenCalledOnce();

  await render(<Modal title="A" closeButton busy onClose={onClose} closeProps={{ "data-guide": "x" }} />);
  expect(document.querySelector("[data-guide='x']").disabled).toBe(true);
});

test("screen layer leaves Escape and Tab to the screen, and exposes the dialog through ref", async () => {
  const onClose = vi.fn();
  const ref = { current: null };
  await render(
    <Modal ref={ref} bare layer="screen" aria-label="終端機" onClose={onClose}>
      <button type="button">第一</button>
      <button type="button">最後</button>
    </Modal>,
  );
  expect(ref.current).toBe(dialog());
  expect(dialog().querySelector("h2")).toBeNull();

  await act(async () => press("Escape"));
  expect(onClose).not.toHaveBeenCalled();

  const last = dialog().querySelectorAll("button")[1];
  last.getClientRects = () => [{}];
  last.focus();
  expect(press("Tab").defaultPrevented).toBe(false);
});
