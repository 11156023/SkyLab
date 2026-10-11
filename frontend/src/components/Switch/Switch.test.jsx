// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import Switch from "./Switch";

let host, root;

async function render(props) {
  await act(async () => root.render(<Switch {...props} />));
  return host.querySelector('[role="switch"]');
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

describe("Switch", () => {
  test("用 aria-checked 報目前狀態，點一下回傳相反的值", async () => {
    const onChange = vi.fn();
    const sw = await render({ checked: true, onChange, label: "啟用" });
    expect(sw.getAttribute("aria-checked")).toBe("true");
    expect(sw.textContent).toBe("啟用");

    await act(async () => sw.click());
    expect(onChange).toHaveBeenCalledWith(false);

    const off = await render({ checked: false, onChange, label: "啟用" });
    await act(async () => off.click());
    expect(onChange).toHaveBeenLastCalledWith(true);
  });

  test("停用時不能切換", async () => {
    const onChange = vi.fn();
    const sw = await render({ checked: false, onChange, label: "啟用", disabled: true });
    await act(async () => sw.click());
    expect(onChange).not.toHaveBeenCalled();
  });

  test("沒有可見文字時用 ariaLabel 當名稱", async () => {
    const sw = await render({ checked: false, onChange: () => {}, ariaLabel: "啟用規則" });
    expect(sw.getAttribute("aria-label")).toBe("啟用規則");
    expect(sw.textContent).toBe("");
  });
});
