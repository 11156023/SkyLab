// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import Pagination from "./Pagination";

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

async function render(props) {
  const onChange = vi.fn();
  await act(async () => {
    root.render(
      <Pagination info="第 x 頁" prevLabel="上一頁" nextLabel="下一頁" onChange={onChange} {...props} />,
    );
  });
  const [prev, next] = host.querySelectorAll("button");
  return { prev, next, onChange };
}

test("顯示傳入的資訊與按鈕文字", async () => {
  const { prev, next } = await render({ page: 1, totalPages: 3 });
  expect(host.textContent).toContain("第 x 頁");
  expect(prev.textContent).toContain("上一頁");
  expect(next.textContent).toContain("下一頁");
});

test("第一頁停用上一頁、最後一頁停用下一頁", async () => {
  let btns = await render({ page: 0, totalPages: 2 });
  expect(btns.prev.disabled).toBe(true);
  expect(btns.next.disabled).toBe(false);
  btns = await render({ page: 1, totalPages: 2 });
  expect(btns.prev.disabled).toBe(false);
  expect(btns.next.disabled).toBe(true);
});

test("點擊回報目標頁碼（從 0 起算）", async () => {
  const { prev, next, onChange } = await render({ page: 2, totalPages: 5 });
  await act(async () => prev.click());
  await act(async () => next.click());
  expect(onChange.mock.calls).toEqual([[1], [3]]);
});
