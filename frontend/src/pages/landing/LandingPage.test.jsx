// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({ to: vi.fn() }));

vi.mock("gsap", () => ({
  default: {
    registerPlugin: () => {},
    context: (fn) => { fn(); return { revert: () => {} }; },
    set: () => {},
    to: (...args) => mocks.to(...args),
    from: () => {},
    timeline: () => ({ to() { return this; }, set() { return this; }, fromTo() { return this; } }),
  },
}));
vi.mock("gsap/ScrollTrigger", () => ({ ScrollTrigger: { create: () => {} } }));
vi.mock("react-router-dom", () => ({ Link: ({ children }) => <a>{children}</a> }));

import LandingPage from "./LandingPage";

let host;
let root;
const originalFetch = globalThis.fetch;
const originalMatchMedia = window.matchMedia;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.to.mockReset();
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  Object.defineProperty(window, "innerWidth", { value: 1280, configurable: true });
  globalThis.fetch = vi.fn(async () => ({
    ok: true,
    json: async () => ({ classroom: { seats: 40, online: 37 } }),
  }));
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  globalThis.fetch = originalFetch;
  window.matchMedia = originalMatchMedia;
});

/* 亮的座位（data-seat）數量來自 stats.json，掛載時還沒回來：
   座位點亮的 scrub 必須在資料到了之後才建立，否則永遠不會亮 */
test("教室座位的點亮動畫在 stats.json 回來後套到亮著的座位上", async () => {
  await act(async () => { root.render(<LandingPage />); });
  await act(async () => { for (let i = 0; i < 10; i += 1) await Promise.resolve(); });

  expect(host.querySelectorAll("[data-seat]").length).toBe(37);
  const seatTweens = mocks.to.mock.calls.filter(
    ([targets]) => targets?.length && targets[0]?.hasAttribute?.("data-seat"),
  );
  expect(seatTweens.at(-1)?.[0].length).toBe(37);
});
