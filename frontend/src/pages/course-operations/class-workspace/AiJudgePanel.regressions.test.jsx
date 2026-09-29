// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, test, vi } from "vitest";
import { ChatPanel, ProposalPanel, RubricsTab } from "./AiJudgePanel";
import { AiJudgeService } from "../../../services/aiJudge";

const originalScrollIntoView = Element.prototype.scrollIntoView;

afterEach(() => {
  Element.prototype.scrollIntoView = originalScrollIntoView;
  vi.restoreAllMocks();
});

function mount(element) {
  Element.prototype.scrollIntoView = vi.fn();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(element);
  });
  const cleanup = () => {
    act(() => root.unmount());
    container.remove();
  };
  return { container, cleanup };
}

function typeInto(textarea, value) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
  act(() => {
    setter.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function pressEnter(textarea) {
  await act(async () => {
    textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

// 模擬正式建置的雜湊 class 名稱：只有原始 key 查得到，拿雜湊後的名稱再查一次會是 undefined。
vi.mock("./AiJudgePanel.module.scss", () => ({
  default: new Proxy(
    {},
    {
      get: (_, key) => (typeof key === "string" && !key.startsWith("_") ? `_${key}_h4sh` : undefined),
    },
  ),
}));

describe("ProposalPanel item-result badge", () => {
  test("不可套用項目的徽章帶有對應顏色 class，不會輸出 undefined", () => {
    const html = renderToStaticMarkup(
      <ProposalPanel
        proposal={[{ id: "item-1", operation: "add", title: "確認 Python 版本" }]}
        selectedIds={new Set()}
        onToggle={() => {}}
        onApply={() => {}}
        onSkip={() => {}}
        disabled={false}
        itemResults={[
          {
            source_index: 1,
            title: "檢查 Port 8080",
            status: "needs_information",
            operation: null,
            missing_information: ["連接埠範圍"],
          },
          {
            source_index: 2,
            title: "程式架構品質",
            status: "unsupported",
            operation: null,
            missing_information: [],
            detail: "需要人工判斷",
          },
        ]}
      />,
    );

    expect(html).toContain("detBadge_partial");
    expect(html).toContain("detBadge_manual");
    expect(html).not.toContain("undefined");
  });
});

describe("ChatPanel 送出失敗時保留輸入", () => {
  test("onSendMessage 回傳 false 時把原文放回輸入框", async () => {
    const onSendMessage = vi.fn().mockResolvedValue(false);
    const { container, cleanup } = mount(
      <ChatPanel messages={[]} onSendMessage={onSendMessage} isLoading={false} hasRubric />,
    );
    const textarea = container.querySelector("textarea");
    typeInto(textarea, "hello");
    await pressEnter(textarea);

    expect(onSendMessage).toHaveBeenCalledWith("hello", false, []);
    expect(container.querySelector("textarea").value).toBe("hello");
    cleanup();
  });

  test("訊息正常送出時清空輸入框", async () => {
    const onSendMessage = vi.fn().mockResolvedValue(undefined);
    const { container, cleanup } = mount(
      <ChatPanel messages={[]} onSendMessage={onSendMessage} isLoading={false} hasRubric />,
    );
    const textarea = container.querySelector("textarea");
    typeInto(textarea, "hello");
    await pressEnter(textarea);

    expect(onSendMessage).toHaveBeenCalledTimes(1);
    expect(container.querySelector("textarea").value).toBe("");
    cleanup();
  });
});

describe("RubricsTab 檢查表尚未載入時停用聊天", () => {
  test("listFiles 尚未回來時輸入框停用，不會送出訊息", async () => {
    vi.spyOn(AiJudgeService, "listFiles").mockReturnValue(new Promise(() => {}));
    vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);
    const sendMessage = vi.spyOn(AiJudgeService, "sendSessionMessage").mockResolvedValue({});

    const { container, cleanup } = mount(
      <RubricsTab classId="class-1" judgeSession={{ id: "session-1", selected_file_id: "file-1" }} />,
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });

    const textarea = container.querySelector('textarea[aria-label="與 AI 對話輸入框"]');
    expect(textarea).toBeTruthy();
    expect(textarea.disabled).toBe(true);
    await pressEnter(textarea);
    expect(sendMessage).not.toHaveBeenCalled();
    cleanup();
  });
});
