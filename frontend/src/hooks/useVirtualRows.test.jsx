// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import useVirtualRows, { VIRTUALIZE_FROM_ROWS } from "./useVirtualRows";

const ROW = 40;

function Table({ count }) {
  const rows = useVirtualRows({ count, estimateSize: ROW });
  return (
    <table>
      <tbody ref={rows.attachBody} data-enabled={String(rows.enabled)}>
        {rows.paddingTop > 0 && <tr data-pad="top" style={{ height: rows.paddingTop }} />}
        {rows.items.map((item) => (
          <tr key={item.index} ref={rows.measureElement} data-index={item.index}>
            <td>row-{item.index}</td>
          </tr>
        ))}
        {rows.paddingBottom > 0 && <tr data-pad="bottom" style={{ height: rows.paddingBottom }} />}
      </tbody>
    </table>
  );
}

let host;
let root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  /* happy-dom 沒有版面：virtual-core 量列高讀的是 offsetHeight（不是 getBoundingClientRect），
     每個元素都給 40；視窗高 400，讓 virtualizer 有東西可算 */
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get: () => ROW });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: 400 });
  Object.defineProperty(window, "scrollY", { configurable: true, value: 0 });
  /* happy-dom 的 ResizeObserver 會回報 0 高度的 borderBoxSize，virtualizer 就把每列量成 0；
     換成不回呼的替身，量測改走上面 mock 的 getBoundingClientRect */
  class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
  /* virtualizer 讀的是 window.ResizeObserver（scrollElement 的 defaultView），不只 globalThis */
  Object.defineProperty(window, "ResizeObserver", { configurable: true, value: ResizeObserverStub });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  delete HTMLElement.prototype.offsetHeight;
  vi.unstubAllGlobals();
});

describe("useVirtualRows", () => {
  test("below the threshold every row renders and no spacer rows exist", async () => {
    const count = VIRTUALIZE_FROM_ROWS - 1;
    await act(async () => { root.render(<Table count={count} />); });

    expect(host.querySelector("tbody").dataset.enabled).toBe("false");
    expect(host.querySelectorAll("tr[data-index]")).toHaveLength(count);
    expect(host.querySelector("tr[data-pad]")).toBeNull();
  });

  test("above the threshold only the rows near the viewport render, padded to the full height", async () => {
    const count = 500;
    await act(async () => { root.render(<Table count={count} />); });

    const rendered = host.querySelectorAll("tr[data-index]");
    expect(host.querySelector("tbody").dataset.enabled).toBe("true");
    expect(rendered.length).toBeGreaterThan(0);
    expect(rendered.length).toBeLessThan(count);
    expect(rendered[0].dataset.index).toBe("0");

    const bottom = host.querySelector("tr[data-pad='bottom']");
    expect(bottom).not.toBeNull();
    const renderedHeight = rendered.length * ROW;
    expect(parseInt(bottom.style.height, 10)).toBe(count * ROW - renderedHeight);
    expect(host.querySelector("tr[data-pad='top']")).toBeNull();
  });

  test("content above the table growing without resizing the table still moves the rendered rows", async () => {
    const observers = [];
    class ResizeObserverSpy {
      constructor(callback) { this.callback = callback; this.targets = []; observers.push(this); }
      observe(target) { this.targets.push(target); }
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", ResizeObserverSpy);
    Object.defineProperty(window, "ResizeObserver", { configurable: true, value: ResizeObserverSpy });
    /* tbody 在文件中的位置；happy-dom 沒有版面，自己算 getBoundingClientRect */
    let tbodyTop = 0;
    const original = HTMLTableSectionElement.prototype.getBoundingClientRect;
    HTMLTableSectionElement.prototype.getBoundingClientRect = function () {
      const top = tbodyTop - window.scrollY;
      return { top, bottom: top, left: 0, right: 0, width: 0, height: 0, x: 0, y: top };
    };
    Object.defineProperty(window, "scrollY", { configurable: true, value: 2000 });
    const firstRow = () => Number(host.querySelector("tr[data-index]").dataset.index);
    try {
      await act(async () => { root.render(<Table count={500} />); });
      const before = firstRow();

      /* 表格上方的內容長高 1000px：整頁變高，表格本身大小不變 */
      tbodyTop = 1000;
      await act(async () => {
        observers.filter((o) => o.targets.includes(document.body)).forEach((o) => o.callback([]));
      });

      expect(firstRow()).toBe(before - 1000 / ROW);
    } finally {
      HTMLTableSectionElement.prototype.getBoundingClientRect = original;
    }
  });
});
