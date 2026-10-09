// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import ConnectionLostState from "./ConnectionLostState";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));

let host;
let root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

test("連不上：警示角色、標題說明與重新連線鈕，按下就重試", async () => {
  const onRetry = vi.fn();
  await act(async () => root.render(<ConnectionLostState onRetry={onRetry} />));
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.textContent).toContain("App.connectionUnavailable");
  const button = host.querySelector("button");
  expect(button.textContent).toContain("App.retryConnect");
  await act(async () => button.click());
  expect(onRetry).toHaveBeenCalledTimes(1);
});

test("重試中：改成狀態角色、按鈕停用並顯示重試中", async () => {
  await act(async () => root.render(<ConnectionLostState onRetry={() => {}} retrying />));
  expect(host.querySelector('[role="status"]')).not.toBeNull();
  const button = host.querySelector("button");
  expect(button.disabled).toBe(true);
  expect(button.textContent).toContain("App.retrying");
});
