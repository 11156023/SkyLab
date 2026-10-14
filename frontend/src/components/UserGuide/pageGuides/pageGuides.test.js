import { describe, expect, it } from "vitest";
import en from "../../../locales/en/common.json";
import ja from "../../../locales/ja/common.json";
import zhTW from "../../../locales/zh-TW/common.json";
import { ROUTE_GUIDES } from "../routeGuides";
import { ROUTE_DETAILED_GUIDES } from "./index";

/* 這些路由的專屬導覽寫在 UserGuide.jsx（PAGE_GUIDES 等），不經 pageGuides */
const BUILT_IN = new Set([
  "dashboard", "courses", "course", "course-week", "my-resources", "resource-detail",
  "my-requests", "managed-resource-detail", "ai-api", "firewall", "domain",
]);

/* 值是函式的導覽依網址挑步驟，逐一餵進這些網址檢查 */
const SAMPLE_PATHS = {
  "class-workspace": [
    "/class-management/class-1",
    ...["students", "machines", "weekly", "progress", "classroom"].map((section) => `/class-management/class-1/${section}`),
  ],
};

const sources = import.meta.glob(["../../../**/*.jsx", "!../../../**/*.test.jsx"], {
  query: "?raw",
  import: "default",
  eager: true,
});

const ANCHOR_PATTERNS = [
  /data-guide(-tab)?="([^"]+)"/g,
  /["']data-guide(-tab)?["']\s*:\s*["']([^"']+)["']/g,
];

function collectAnchors(text) {
  const found = [];
  for (const pattern of ANCHOR_PATTERNS) {
    for (const match of text.matchAll(pattern)) found.push(`${match[1] ? "tab" : "guide"}:${match[2]}`);
  }
  return found;
}

const definedAnchors = new Set(Object.values(sources).flatMap(collectAnchors));

function resolveGuides(id, entry) {
  if (typeof entry !== "function") return [entry];
  return (SAMPLE_PATHS[id] ?? []).map((path) => entry(path));
}

const guides = Object.entries(ROUTE_DETAILED_GUIDES).flatMap(([id, entry]) =>
  resolveGuides(id, entry).map((guide) => ({ id, guide })));

describe("page-specific guides", () => {
  it("gives every routed page its own guide instead of the generic tour", () => {
    const missing = ROUTE_GUIDES
      .map((route) => route.id)
      .filter((id) => !BUILT_IN.has(id) && !ROUTE_DETAILED_GUIDES[id]);
    expect(missing).toEqual([]);
    expect(ROUTE_DETAILED_GUIDES["dashboard-admin"]).toBeTruthy();
    expect(ROUTE_DETAILED_GUIDES["dashboard-teacher"]).toBeTruthy();
  });

  it("resolves function guides for every sample path", () => {
    for (const [id, entry] of Object.entries(ROUTE_DETAILED_GUIDES)) {
      if (typeof entry === "function") expect(SAMPLE_PATHS[id]?.length).toBeGreaterThan(0);
    }
    for (const { guide } of guides) {
      expect(guide?.steps?.length).toBeGreaterThan(0);
    }
  });

  it("has every title and step text in all three languages", () => {
    const keys = guides.flatMap(({ guide }) => [
      guide.titleKey,
      ...guide.steps.flatMap((step) => [step.titleKey, step.textKey]),
    ]);
    for (const [lang, catalog] of [["zh-TW", zhTW], ["en", en], ["ja", ja]]) {
      expect(keys.filter((key) => !catalog[key]), lang).toEqual([]);
    }
  });

  it("points every selector at an anchor that exists in the page source", () => {
    const missing = guides.flatMap(({ id, guide }) => {
      const selectors = [
        guide.cleanupSelector,
        ...guide.steps.flatMap((step) => [
          step.selector, step.activateSelector, step.deactivateSelector,
          step.conditionSelector, step.performSelector,
        ]),
      ].filter(Boolean);
      return selectors
        .flatMap((selector) => [...selector.matchAll(/\[data-guide(-tab)?="([^"]+)"/g)])
        .map((match) => `${match[1] ? "tab" : "guide"}:${match[2]}`)
        .filter((anchor) => !definedAnchors.has(anchor))
        .map((anchor) => `${id} → ${anchor}`);
    });
    expect(missing).toEqual([]);
  });
});
