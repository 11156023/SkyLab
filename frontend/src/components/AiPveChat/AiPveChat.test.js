import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  AiPveMarkdownContent,
  AiPveToolHistory,
  initialAiPveMessages,
  isAiPveLogNearBottom,
  sanitizeAiPveContent,
  shouldSubmitAiPveInput,
} from "./AiPveChat";
import zh from "../../locales/zh-TW/components.json";

describe("sanitizeAiPveContent", () => {
  it("removes internal model markers from visible messages", () => {
    expect(sanitizeAiPveContent("<think>分析中</think>節點正常<|endoftext|>")).toBe("節點正常");
    expect(sanitizeAiPveContent(null)).toBe("");
  });

  it("renders assistant Markdown instead of showing formatting markers", () => {
    const html = renderToStaticMarkup(
      React.createElement(AiPveMarkdownContent, { content: "**CPU 使用率**\n\n- 85%" }),
    );

    expect(html).toContain("<strong>CPU 使用率</strong>");
    expect(html).toContain("<li>85%</li>");
    expect(html).not.toContain("**CPU 使用率**");
  });

  it("sanitizes raw HTML in assistant Markdown", () => {
    const html = renderToStaticMarkup(
      React.createElement(AiPveMarkdownContent, {
        content: '<script>alert("xss")</script>\n\n**安全**',
      }),
    );

    expect(html).not.toContain("<script");
    expect(html).toContain("<strong>安全</strong>");
  });
});

describe("composer keyboard behavior", () => {
  it("sends with Enter and preserves Shift+Enter for multiline drafts", () => {
    expect(shouldSubmitAiPveInput({ key: "Enter" })).toBe(true);
    expect(shouldSubmitAiPveInput({ key: "Enter", shiftKey: true })).toBe(false);
    expect(shouldSubmitAiPveInput({ key: "a" })).toBe(false);
  });

  it("does not send while confirming Chinese or Japanese composition", () => {
    expect(shouldSubmitAiPveInput({ key: "Enter" }, true)).toBe(false);
    expect(shouldSubmitAiPveInput({ key: "Enter", nativeEvent: { isComposing: true } })).toBe(false);
    // Some browsers report composition as ended on the confirming Enter.
    expect(shouldSubmitAiPveInput({ key: "Enter", nativeEvent: { keyCode: 229 } })).toBe(false);
  });

  it("ignores held Enter and modified shortcuts", () => {
    for (const modifier of ["repeat", "ctrlKey", "altKey", "metaKey"]) {
      expect(shouldSubmitAiPveInput({ key: "Enter", [modifier]: true })).toBe(false);
    }
  });
});

describe("chat reading position", () => {
  it("follows short conversations and fractional positions near the bottom", () => {
    expect(isAiPveLogNearBottom({ scrollTop: 0, scrollHeight: 200, clientHeight: 400 })).toBe(true);
    expect(isAiPveLogNearBottom({ scrollTop: 599.5, scrollHeight: 1000, clientHeight: 400 })).toBe(true);
  });

  it("stops following when the reader scrolls up into older messages", () => {
    expect(isAiPveLogNearBottom({ scrollTop: 300, scrollHeight: 1000, clientHeight: 400 })).toBe(false);
  });
});

describe("tool activity disclosure", () => {
  const t = (key, options) => (zh[key] ?? key).replace("{{count}}", String(options?.count));

  it("starts collapsed and shows readable actions and targets without raw results", () => {
    const html = renderToStaticMarkup(React.createElement(AiPveToolHistory, {
      t,
      tools: [
        { name: "get_nodes", args: { node: "pve2" } },
        { name: "ssh_exec", args: { vmid: 101, command: "private-command" }, result: { confirm_token: "private-token", error: "private-error" } },
      ],
    }));
    expect(html).toContain("<details");
    expect(html).not.toMatch(/<details[^>]*\bopen(?:=|\s|>)/);
    expect(html).toContain("查詢紀錄（2）");
    expect(html).toContain("查詢節點狀態");
    expect(html).toContain("pve2");
    expect(html).toContain("遠端指令");
    expect(html).toContain("VMID 101");
    expect(html).not.toContain("get_nodes");
    expect(html).not.toContain("private-");
  });

  it("omits empty activity and handles new tool names without leaking them", () => {
    expect(renderToStaticMarkup(React.createElement(AiPveToolHistory, { tools: [], t }))).toBe("");
    const html = renderToStaticMarkup(React.createElement(AiPveToolHistory, { tools: [{ name: "future_internal_tool" }], t }));
    expect(html).toContain("系統查詢");
    expect(html).not.toContain("future_internal_tool");
  });
});

describe("initialAiPveMessages", () => {
  it("skips the greeting when the user already asked something", () => {
    // 首頁是先在輸入列打字才展開對話，再自我介紹一次只是白佔一格
    expect(initialAiPveMessages("節點 pve2 為什麼離線？", "我是助手")).toEqual([]);
    expect(initialAiPveMessages("  1  ", "我是助手")).toEqual([]);
  });

  it("keeps the greeting when the conversation starts empty", () => {
    expect(initialAiPveMessages("", "我是助手")).toEqual([
      { role: "assistant", content: "我是助手" },
    ]);
    expect(initialAiPveMessages(null, "我是助手")).toHaveLength(1);
  });
});
