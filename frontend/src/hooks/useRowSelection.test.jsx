// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import useRowSelection, { rangeBetween } from "./useRowSelection";

describe("rangeBetween", () => {
  test("不論往上或往下點，都取兩端之間（含兩端）", () => {
    expect(rangeBetween([5, 6, 7, 8], 6, 8)).toEqual([6, 7, 8]);
    expect(rangeBetween([5, 6, 7, 8], 8, 5)).toEqual([5, 6, 7, 8]);
  });

  test("上一次點的那筆已不在清單上，就只動這一筆", () => {
    expect(rangeBetween([5, 6], 99, 6)).toEqual([6]);
  });
});

describe("useRowSelection", () => {
  let root;
  let host;
  let api;

  function Probe({ ids }) {
    api = useRowSelection(ids);
    return null;
  }

  async function render(ids) {
    await act(async () => { root.render(<Probe ids={ids} />); });
  }

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
  });

  test("Shift 連選一段、全選與部分選取", async () => {
    await render(["a", "b", "c", "d"]);
    await act(async () => api.toggle("a"));
    await act(async () => api.toggle("c", true));
    expect([...api.selected].sort()).toEqual(["a", "b", "c"]);
    expect(api.someSelected).toBe(true);

    await act(async () => api.toggleAll());
    expect(api.allSelected).toBe(true);
    await act(async () => api.toggleAll());
    expect(api.count).toBe(0);
  });

  test("看不到的那幾筆自動取消勾選（篩選、翻頁、刷新後消失）", async () => {
    await render(["a", "b", "c"]);
    await act(async () => api.toggleAll());
    await render(["b", "c"]);
    expect([...api.selected].sort()).toEqual(["b", "c"]);
    await render(["a", "b", "c"]);
    expect(api.isSelected("a")).toBe(false);
  });

  test("勾選狀態下點整列也是勾選；列裡的按鈕照舊，沒勾任何一筆時不攔", async () => {
    const row = document.createElement("div");
    const text = document.createElement("span");
    const button = document.createElement("button");
    row.append(text, button);
    const click = (target) => ({ currentTarget: row, target, shiftKey: false, preventDefault() {} });

    await render(["a", "b"]);
    let handled;
    await act(async () => { handled = api.rowClick("b", click(text)); });
    expect(handled).toBe(false);

    await act(async () => api.toggle("a"));
    await act(async () => { handled = api.rowClick("b", click(button)); });
    expect(handled).toBe(false);
    await act(async () => { handled = api.rowClick("b", click(text)); });
    expect(handled).toBe(true);
    expect([...api.selected].sort()).toEqual(["a", "b"]);
    /* 不能勾的那筆（不在清單上）照舊 */
    await act(async () => { handled = api.rowClick("zzz", click(text)); });
    expect(handled).toBe(false);
  });

  test("replace 只留指定的那幾筆（批量失敗的保留勾選）", async () => {
    await render([1, 2, 3]);
    await act(async () => api.toggleAll());
    await act(async () => api.replace([2]));
    expect([...api.selected]).toEqual([2]);
  });
});
