import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AiPveMarkdownContent } from "./AiPveChat";
import {
  parseLayerCell,
  parsePercentCell,
  parseStatusCell,
  sectionIcon,
  toPlainText,
} from "./aiPveRichText";

describe("toPlainText", () => {
  it("joins text children and refuses mixed content", () => {
    expect(toPlainText(["✅ ", "正常"])).toBe("✅ 正常");
    expect(toPlainText("87%")).toBe("87%");
    // 含 <strong> 等節點時交還原樣渲染，不要為了套 UI 把內容吃掉
    expect(toPlainText([React.createElement("strong", null, "x")])).toBeNull();
  });
});

describe("parseStatusCell", () => {
  it("recognises the four markers the prompt allows", () => {
    expect(parseStatusCell("✅ 正常")).toMatchObject({ tone: "ok", label: "正常" });
    expect(parseStatusCell("⚠️ 注意")).toMatchObject({ tone: "warn", label: "注意" });
    expect(parseStatusCell("❌ 異常")).toMatchObject({ tone: "bad", label: "異常" });
    expect(parseStatusCell("❓ 未取得")).toMatchObject({ tone: "unknown", label: "未取得" });
  });

  it("accepts the warning sign without its variation selector", () => {
    expect(parseStatusCell("⚠ 注意")).toMatchObject({ tone: "warn", label: "注意" });
  });

  it("leaves ordinary prose alone", () => {
    expect(parseStatusCell("節點 pve-01 離線")).toBeNull();
    expect(parseStatusCell("")).toBeNull();
  });
});

describe("parseLayerCell", () => {
  it("matches the five fixed layer tags case-insensitively", () => {
    expect(parseLayerCell("[PVE]")).toEqual({ layer: "PVE" });
    expect(parseLayerCell("[service]")).toEqual({ layer: "Service" });
  });

  it("ignores brackets that are part of a sentence", () => {
    expect(parseLayerCell("[PVE] 節點正常")).toBeNull();
    expect(parseLayerCell("[Kernel]")).toBeNull();
  });
});

describe("parsePercentCell", () => {
  it("reads a bare percentage", () => {
    expect(parsePercentCell("87.3%")).toEqual({ percent: 87.3, label: "87.3%" });
    expect(parsePercentCell("0%")).toEqual({ percent: 0, label: "0%" });
  });

  it("rejects prose, out-of-range and non-percentages", () => {
    expect(parsePercentCell("CPU 87%")).toBeNull();
    expect(parsePercentCell("120%")).toBeNull();
    expect(parsePercentCell("87")).toBeNull();
  });
});

describe("sectionIcon", () => {
  it("maps the fixed diagnostic sections", () => {
    expect(sectionIcon("診斷結論")).toBe("fact_check");
    expect(sectionIcon("分層結果")).toBe("layers");
    expect(sectionIcon("建議下一步")).toBe("lightbulb");
  });

  it("returns null for headings it does not know", () => {
    expect(sectionIcon("其他說明")).toBeNull();
  });
});

describe("AiPveMarkdownContent rich cells", () => {
  const table = [
    "| 分層 | 狀態 | 使用率 |",
    "| --- | --- | --- |",
    "| [PVE] | ✅ 正常 | 42.5% |",
    "| [Service] | ❌ 異常 | 100% |",
  ].join("\n");

  it("renders status markers and layer tags as UI instead of bare text", () => {
    const html = renderToStaticMarkup(
      React.createElement(AiPveMarkdownContent, { content: table }),
    );

    // 狀態換成徽章：圖示 + 文字，而且帶得到語意色的 class
    expect(html).toContain("check_circle");
    expect(html).toContain("cancel");
    expect(html).toMatch(/cellStatus_ok/);
    expect(html).toMatch(/cellStatus_bad/);
    // 分層換成晶片，方括號不再直接顯示
    expect(html).toMatch(/cellLayer_pve/);
    expect(html).not.toContain("[PVE]");
    // 百分比換成量表，寬度反映數值
    expect(html).toContain("width:42.5%");
    expect(html).toContain("width:100%");
  });

  it("keeps the original text so the table stays readable without styling", () => {
    const html = renderToStaticMarkup(
      React.createElement(AiPveMarkdownContent, { content: table }),
    );

    expect(html).toContain("正常");
    expect(html).toContain("異常");
    expect(html).toContain("42.5%");
  });

  it("does not touch cells it cannot classify", () => {
    const html = renderToStaticMarkup(
      React.createElement(AiPveMarkdownContent, {
        content: "| 節點 |\n| --- |\n| pve-01 離線 |",
      }),
    );

    expect(html).toContain("<td>pve-01 離線</td>");
  });

  it("adds a section icon to the fixed diagnostic headings only", () => {
    const html = renderToStaticMarkup(
      React.createElement(AiPveMarkdownContent, {
        content: "## 診斷結論\n\n節點正常\n\n## 其他\n\n內容",
      }),
    );

    expect(html).toContain("fact_check");
    expect(html).toContain("<h2>其他</h2>");
  });
});
