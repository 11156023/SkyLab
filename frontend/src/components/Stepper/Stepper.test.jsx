// @vitest-environment happy-dom
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Stepper from "./Stepper";

function render(props) {
  const html = renderToStaticMarkup(React.createElement(Stepper, { onSelect: () => {}, ariaLabel: "流程", ...props }));
  const doc = new DOMParser().parseFromString(html, "text/html");
  return doc.querySelector("nav");
}

const STEPS = [
  { key: "overview", label: "總覽" },
  { key: "students", label: "學生", done: true },
  { key: "machines", label: "環境", done: true },
  { key: "weekly", label: "每週" },
];

describe("Stepper", () => {
  it("numbers unfinished steps and swaps finished ones for a check", () => {
    const nav = render({ steps: STEPS, activeKey: "overview" });
    const dots = [...nav.querySelectorAll("button")].map((button) => button.firstElementChild.textContent);
    expect(dots).toEqual(["1", "check", "check", "4"]);
    expect(nav.getAttribute("aria-label")).toBe("流程");
  });

  it("marks only the active step as current", () => {
    const nav = render({ steps: STEPS, activeKey: "machines" });
    const current = [...nav.querySelectorAll("[aria-current]")];
    expect(current).toHaveLength(1);
    expect(current[0].getAttribute("aria-current")).toBe("step");
    expect(current[0].textContent).toContain("環境");
  });

  it("lights a connector only when both ends are reached", () => {
    // 總覽（目前）— 學生 ✓ — 環境 ✓ — 每週（未完成）
    const nav = render({ steps: STEPS, activeKey: "overview" });
    const connectors = [...nav.querySelectorAll("ol > li[aria-hidden]")];
    expect(connectors).toHaveLength(3);
    const reached = connectors.map((li) => li.className.includes("connectorReached"));
    expect(reached).toEqual([true, true, false]);
  });

  it("puts extras after a divider without numbers or connectors", () => {
    const nav = render({
      steps: STEPS,
      extras: [{ key: "progress", label: "進度", icon: "cast_for_education" }, { key: "ai", label: "AI", icon: "auto_awesome" }],
      activeKey: "ai",
    });
    const items = [...nav.querySelectorAll("ol > li")];
    // 4 步 + 3 條連線 + 1 條分隔線 + 2 個額外分頁
    expect(items).toHaveLength(10);
    expect(items[7].className).toContain("divider");
    const current = nav.querySelector("[aria-current]");
    expect(current.getAttribute("aria-current")).toBe("page");
    expect(current.textContent).toContain("AI");
  });

  it("disables steps a wizard can't jump to yet", () => {
    const nav = render({
      steps: STEPS.map((step, index) => ({ ...step, disabled: index > 1 })),
      activeKey: "students",
    });
    const disabled = [...nav.querySelectorAll("button")].map((button) => button.disabled);
    expect(disabled).toEqual([false, false, true, true]);
  });

  it("renders no divider when there are no extras", () => {
    const nav = render({ steps: STEPS, activeKey: "overview" });
    expect(nav.querySelector("[class*='divider']")).toBeNull();
  });
});
