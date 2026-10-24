// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { I18nextProvider } from "react-i18next";
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

import LandingPage from "./LandingPage";
import { createI18n } from "../i18n";
import { CONTACT_EMAIL, DOCS_URL, GITHUB_URL } from "../config";
import { LANGUAGE_STORAGE_KEY } from "../languagePreference";
import STATS from "../stats.json";

let host;
let root;
const originalMatchMedia = window.matchMedia;

const page = (lang) => (
  <I18nextProvider i18n={createI18n(lang)}>
    <LandingPage />
  </I18nextProvider>
);

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  mocks.to.mockReset();
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  Object.defineProperty(window, "innerWidth", { value: 1280, configurable: true });
  host = document.createElement("div");
  document.body.append(host);
});

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  host.remove();
  window.matchMedia = originalMatchMedia;
});

test("教室座位的點亮動畫套到 stats.json 裡亮著的座位上", async () => {
  root = createRoot(host);
  await act(async () => { root.render(page("zh-TW")); });

  const online = STATS.classroom.online;
  expect(host.querySelectorAll("[data-seat]").length).toBe(online);
  const seatTweens = mocks.to.mock.calls.filter(
    ([targets]) => targets?.length && targets[0]?.hasAttribute?.("data-seat"),
  );
  expect(seatTweens.at(-1)?.[0].length).toBe(online);
});

/* 預渲染在 Node 上跑,沒有 window:暫時拿掉 happy-dom 的 window 來畫 */
function renderOnServer(lang) {
  vi.stubGlobal("window", undefined);
  try {
    return renderToString(page(lang));
  } finally {
    vi.unstubAllGlobals();
  }
}

/* 正式站是預渲染再 hydrate:首次 render 必須跟預渲染一模一樣(桌機運鏡版、夜間),
   否則 React 會丟掉預渲染的內容或留下錯的屬性;記住的日間模式要在 hydrate 後才切過去 */
test("預渲染的 HTML 在桌機上 hydrate 不會有不一致,記住的日間模式隨後套用", async () => {
  window.localStorage.setItem("skylab.landing.mode", "day");
  host.innerHTML = renderOnServer("zh-TW");
  expect(host.firstElementChild.getAttribute("data-mode")).toBe("night");

  const onRecoverableError = vi.fn();
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    await act(async () => { root = hydrateRoot(host, page("zh-TW"), { onRecoverableError }); });
    expect(onRecoverableError).not.toHaveBeenCalled();
    expect(consoleError).not.toHaveBeenCalled();
    expect(host.firstElementChild.getAttribute("data-mode")).toBe("day");
  } finally {
    consoleError.mockRestore();
    window.localStorage.clear();
  }
});

test("聯繫按鈕都寄信到聯絡信箱並帶上該語言的主旨", async () => {
  root = createRoot(host);
  await act(async () => { root.render(page("en")); });

  const mailto = [...host.querySelectorAll('a[href^="mailto:"]')].map((a) => a.getAttribute("href"));
  // 頂列、收尾按鈕、頁尾信箱
  expect(mailto).toEqual(Array(3).fill(`mailto:${CONTACT_EMAIL}?subject=SkyLab%20trial%20inquiry`));
  expect(host.textContent).toContain(CONTACT_EMAIL);
  expect(host.querySelector(`a[href="${GITHUB_URL}"]`)).not.toBeNull();
});

test.each([
  ["zh-TW", "/zh-TW/guides/deployment"],
  ["en", "/guides/deployment"],
  ["ja", "/guides/deployment"],
])("部署指南連到文件站對應語言的頁面(%s)", async (lang, docsPath) => {
  root = createRoot(host);
  await act(async () => { root.render(page(lang)); });

  expect(host.querySelector(`a[href="${DOCS_URL}${docsPath}"]`)).not.toBeNull();
});

test.each([
  ["zh-TW", "zh-TW"],
  ["en", "en"],
  ["ja", "en"],
])("實際操作影片用對應語言的畫面,分頁可以切換(%s)", async (lang, dir) => {
  root = createRoot(host);
  await act(async () => { root.render(page(lang)); });

  expect(host.querySelector("video").getAttribute("poster")).toBe(`/demo/${dir}/console.jpg`);
  // 還沒捲到附近前不載入影片
  expect(host.querySelector("video").getAttribute("src")).toBeNull();
  const tabs = [...host.querySelectorAll('[role="tab"]')];
  expect(tabs).toHaveLength(4);
  await act(async () => { tabs[3].click(); });
  expect(host.querySelector("video").getAttribute("poster")).toBe(`/demo/${dir}/firewall.jpg`);
  expect(tabs[3].getAttribute("aria-selected")).toBe("true");
  expect(tabs[0].getAttribute("aria-selected")).toBe("false");
});

test("語言切換是連到各語言的網址,目前語言標成 aria-current", async () => {
  root = createRoot(host);
  await act(async () => { root.render(page("ja")); });

  const links = [...host.querySelectorAll("a[hreflang]")];
  expect(links.map((a) => a.getAttribute("href"))).toEqual(["/", "/en/", "/ja/"]);
  expect(links.filter((a) => a.getAttribute("aria-current") === "page").map((a) => a.hreflang)).toEqual(["ja"]);
});

test("點語言選單會記下選擇,根網址之後就不再依瀏覽器語言轉走", async () => {
  root = createRoot(host);
  await act(async () => { root.render(page("ja")); });
  // 只看點擊時記了什麼,不讓 happy-dom 真的換頁
  const stopNavigation = (event) => event.preventDefault();
  document.addEventListener("click", stopNavigation, true);
  try {
    await act(async () => { host.querySelector('a[hreflang="zh-TW"]').click(); });
    expect(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("zh-TW");
  } finally {
    document.removeEventListener("click", stopNavigation, true);
    window.localStorage.clear();
  }
});
