// @vitest-environment happy-dom
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import useDragReorder from "./useDragReorder";

/* 每項高 36、間距 4：第 i 項的中線在 i*40+18 */
const ROW = 40;

function List({ initial, disabled = false }) {
  const [items, setItems] = useState(initial);
  const { listRef, draggingKey, getItemProps, getHandleProps } = useDragReorder({
    disabled,
    onMove: (from, to) => setItems((prev) => {
      const next = [...prev];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    }),
  });
  return (
    <div ref={listRef} data-testid="list" data-dragging={draggingKey ?? ""}>
      {items.map((key) => (
        <div key={key} {...getItemProps(key)}>
          <span data-handle={key} {...getHandleProps()} />
          <span data-body={key}>{key}</span>
          <button type="button" data-button={key}>x</button>
        </div>
      ))}
    </div>
  );
}

let host, root;
const order = () => [...host.querySelectorAll("[data-drag-key]")].map((el) => el.dataset.dragKey);
const list = () => host.querySelector('[data-testid="list"]');

async function render(props) {
  await act(async () => root.render(<List {...props} />));
}

async function pointer(target, type, clientY, pointerType = "mouse") {
  await act(async () => {
    target.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, button: 0, pointerType, clientY }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function rect() {
    const index = [...(this.parentNode?.children ?? [])].indexOf(this);
    return { top: index * ROW, bottom: index * ROW + 36, height: 36, left: 0, right: 100, width: 100, x: 0, y: index * ROW };
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
});

describe("useDragReorder", () => {
  test("拖過下一項的中線就換位，放開後結束拖移", async () => {
    await render({ initial: ["a", "b", "c"] });
    await pointer(host.querySelector('[data-handle="a"]'), "pointerdown", 18);
    expect(list().dataset.dragging).toBe("a");

    await pointer(window, "pointermove", 60);
    expect(order()).toEqual(["b", "a", "c"]);

    await pointer(window, "pointermove", 110);
    expect(order()).toEqual(["b", "c", "a"]);

    await pointer(window, "pointerup", 110);
    expect(list().dataset.dragging).toBe("");

    /* 放開後再移動不會再搬 */
    await pointer(window, "pointermove", 0);
    expect(order()).toEqual(["b", "c", "a"]);
  });

  test("一次拖過好幾項也直接落到指標所在位置", async () => {
    await render({ initial: ["a", "b", "c", "d"] });
    await pointer(host.querySelector('[data-handle="d"]'), "pointerdown", 138);
    await pointer(window, "pointermove", 5);
    expect(order()).toEqual(["d", "a", "b", "c"]);
    await pointer(window, "pointerup", 5);
  });

  test("滑鼠按住整列任何地方都能拖，按在列裡的按鈕上不會", async () => {
    await render({ initial: ["a", "b", "c"] });
    await pointer(host.querySelector('[data-button="a"]'), "pointerdown", 18);
    expect(list().dataset.dragging).toBe("");

    await pointer(host.querySelector('[data-body="a"]'), "pointerdown", 18);
    expect(list().dataset.dragging).toBe("a");
    await pointer(window, "pointermove", 60);
    expect(order()).toEqual(["b", "a", "c"]);
    await pointer(window, "pointerup", 60);
  });

  test("觸控只認把手，按在列的其他地方照常捲動頁面", async () => {
    await render({ initial: ["a", "b"] });
    await pointer(host.querySelector('[data-body="a"]'), "pointerdown", 18, "touch");
    expect(list().dataset.dragging).toBe("");

    await pointer(host.querySelector('[data-handle="a"]'), "pointerdown", 18, "touch");
    expect(list().dataset.dragging).toBe("a");
    await pointer(window, "pointerup", 18, "touch");
  });

  test("停用時按住把手不會開始拖移", async () => {
    await render({ initial: ["a", "b"], disabled: true });
    await pointer(host.querySelector('[data-handle="a"]'), "pointerdown", 18);
    await pointer(window, "pointermove", 60);
    expect(order()).toEqual(["a", "b"]);
    expect(list().dataset.dragging).toBe("");
  });
});
