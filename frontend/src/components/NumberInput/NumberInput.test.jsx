// @vitest-environment happy-dom
import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import NumberInput from "./NumberInput";

let host, root;
beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

/* 模擬頁面：定稿值存在父層 state，跟表單實際用法一致 */
function Harness({ initial = 20, min = 20, max = 500, step = 1, onCommit = () => {} }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <NumberInput
        value={value} min={min} max={max} step={step}
        onCommit={(n) => { setValue(n); onCommit(n); }}
      />
      <output>{value}</output>
    </>
  );
}

const input = () => host.querySelector("input");
const output = () => host.querySelector("output").textContent;
const setNative = (el, v) => {
  /* React 追蹤 value 的 tracker 要先繞過，input 事件才會被當成有變化 */
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  setter.call(el, v);
  el.dispatchEvent(new Event("input", { bubbles: true }));
};
const type = async (v) => act(async () => setNative(input(), v));
const blur = async () => act(async () => {
  input().dispatchEvent(new FocusEvent("blur", { bubbles: false }));
  input().dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
});

describe("NumberInput", () => {
  it("全選重打 30：編輯中不夾值，離開欄位才定稿（之前會變 200）", async () => {
    const onCommit = vi.fn();
    await act(async () => root.render(<Harness onCommit={onCommit} />));
    await type("3");
    expect(input().value).toBe("3");
    expect(output()).toBe("20");
    await type("30");
    expect(input().value).toBe("30");
    expect(onCommit).not.toHaveBeenCalled();
    await blur();
    expect(onCommit).toHaveBeenCalledWith(30);
    expect(output()).toBe("30");
    expect(input().value).toBe("30");
  });

  it("超出範圍離開欄位時壓回上下限", async () => {
    await act(async () => root.render(<Harness min={20} max={500} />));
    await type("9999");
    await blur();
    expect(output()).toBe("500");
    await type("3");
    await blur();
    expect(output()).toBe("20");
  });

  it("清空或亂打就回到原值，不呼叫 onCommit", async () => {
    const onCommit = vi.fn();
    await act(async () => root.render(<Harness initial={40} onCommit={onCommit} />));
    await type("");
    expect(input().value).toBe("");
    await blur();
    expect(onCommit).not.toHaveBeenCalled();
    expect(input().value).toBe("40");
  });

  it("對齊步進：記憶體 0.5 GB 一格", async () => {
    const onCommit = vi.fn();
    await act(async () => root.render(
      <Harness initial={1} min={0.5} max={64} step={0.5} onCommit={onCommit} />,
    ));
    await type("2.3");
    await blur();
    expect(onCommit).toHaveBeenCalledWith(2.5);
  });

  it("Enter 定稿並讓欄位失焦，不送出表單", async () => {
    const onSubmit = vi.fn((e) => e.preventDefault());
    const onCommit = vi.fn();
    await act(async () => root.render(
      <form onSubmit={onSubmit}><Harness onCommit={onCommit} /></form>,
    ));
    input().focus();
    await type("45");
    await act(async () => {
      input().dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    });
    await blur();
    expect(onCommit).toHaveBeenCalledWith(45);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
