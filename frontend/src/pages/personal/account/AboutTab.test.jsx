// @vitest-environment happy-dom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const translation = { t: (key, opts) => (opts?.count !== undefined ? `${key}:${opts.count}` : key), i18n: { language: "zh-TW" } };
vi.mock("react-i18next", async (original) => ({ ...await original(), useTranslation: () => translation }));

/* 建置時由 vite define 注入；測試自己塞一份，驗證分頁照資料渲染 */
vi.stubGlobal("__SKYLAB_ABOUT__", {
  name: "SkyLab",
  version: "1.0.0",
  commit: "abc1234",
  builtAt: "2026-10-04T12:00:00.000Z",
  license: "AGPL-3.0",
  repository: "https://github.com/example/SkyLab",
  dependencies: [
    { name: "react", version: "19.2.8", license: "MIT", repository: "https://github.com/facebook/react" },
    { name: "gsap", version: "3.15.0", license: "GSAP Standard License", repository: "" },
  ],
});

const { default: AboutTab, aboutInfo } = await import("./AboutTab");

let host, root;
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

it("reads the build-time about info", () => {
  expect(aboutInfo.commit).toBe("abc1234");
  expect(aboutInfo.dependencies).toHaveLength(2);
});

it("renders version, license links and the dependency table", async () => {
  await act(async () => root.render(<AboutTab />));

  expect(host.textContent).toContain("1.0.0");
  expect(host.textContent).toContain("abc1234");

  const links = [...host.querySelectorAll("a")].map((a) => a.getAttribute("href"));
  expect(links).toContain("https://github.com/example/SkyLab/blob/main/LICENSE");
  expect(links).toContain("https://github.com/example/SkyLab/blob/main/THIRD_PARTY_NOTICES.md");
  expect(links).toContain("https://github.com/example/SkyLab");
  for (const a of host.querySelectorAll("a")) {
    expect(a.getAttribute("target")).toBe("blank".replace(/^/, "_"));
    expect(a.getAttribute("rel")).toContain("noopener");
  }

  const rows = [...host.querySelectorAll("tbody tr")];
  expect(rows).toHaveLength(2);
  expect(rows[0].textContent).toContain("react");
  expect(rows[0].querySelector("a").getAttribute("href")).toBe("https://github.com/facebook/react");
  /* 沒有 repository 的套件只顯示純文字，不產生空連結 */
  expect(rows[1].querySelector("a")).toBeNull();
  expect(rows[1].textContent).toContain("GSAP Standard License");
  expect(host.textContent).toContain("AboutTab.componentsHint:2");
});
