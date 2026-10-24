// @vitest-environment happy-dom

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { renderToStaticMarkup } from "react-dom/server";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { ChatPanel, ProposalPanel, RubricsTab } from "./AiJudgePanel";
import { AiJudgeService } from "../../../services/aiJudge";
import { ConfirmProvider } from "../../../components/ConfirmDialog/ConfirmProvider";
import { aiJudgeKeys } from "../../../queries/aiJudge";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";

const originalScrollIntoView = Element.prototype.scrollIntoView;
let queryClient;

beforeEach(() => {
  /* TanStack Query 預設用 setTimeout(0) 通知元件；改成同步通知，讀取結果在同一個 act 裡就上畫面 */
  notifyManager.setScheduler((callback) => callback());
  queryClient = createTestQueryClient();
});

afterEach(() => {
  Element.prototype.scrollIntoView = originalScrollIntoView;
  notifyManager.setScheduler((callback) => setTimeout(callback, 0));
  queryClient.clear();
  vi.restoreAllMocks();
});

function mount(element) {
  Element.prototype.scrollIntoView = vi.fn();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(withQueryClient(element, queryClient));
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
    const textarea = container.querySelector("textarea[aria-label]");
    typeInto(textarea, "hello");
    await pressEnter(textarea);

    expect(onSendMessage).toHaveBeenCalledWith("hello", false, []);
    expect(container.querySelector("textarea[aria-label]").value).toBe("hello");
    cleanup();
  });

  test("訊息正常送出時清空輸入框", async () => {
    const onSendMessage = vi.fn().mockResolvedValue(undefined);
    const { container, cleanup } = mount(
      <ChatPanel messages={[]} onSendMessage={onSendMessage} isLoading={false} hasRubric />,
    );
    const textarea = container.querySelector("textarea[aria-label]");
    typeInto(textarea, "hello");
    await pressEnter(textarea);

    expect(onSendMessage).toHaveBeenCalledTimes(1);
    expect(container.querySelector("textarea[aria-label]").value).toBe("");
    cleanup();
  });
});

describe("RubricsTab 檢查表尚未載入時停用聊天", () => {
  test("listFiles 尚未回來時輸入框停用，不會送出訊息", async () => {
    vi.spyOn(AiJudgeService, "listFiles").mockReturnValue(new Promise(() => {}));
    vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);
    const sendMessage = vi.spyOn(AiJudgeService, "sendSessionMessage").mockResolvedValue({});

    const { container, cleanup } = mount(
      <ConfirmProvider>
        <RubricsTab classId="class-1" judgeSession={{ id: "session-1", selected_file_id: "file-1" }} />
      </ConfirmProvider>,
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

describe("RubricsTab 背景重抓清單與自動儲存交錯", () => {
  test("自動儲存已寫回新版本後，較早送出的清單回應不會把編輯退回舊內容", async () => {
    const file = (title, revision) => ({
      id: "file-1",
      template_key: "linux",
      environment_keys: ["linux"],
      analysis_revision: revision,
      source_type: "created",
      display_name: "測試檢查表",
      updated_at: "2026-09-15T00:00:00Z",
      analysis_json: {
        items: [{ id: "item-1", title, checked: false, detectable: "manual", judgement_mode: "ai", check_steps: [] }],
        total_items: 1,
      },
    });
    const listFiles = vi.spyOn(AiJudgeService, "listFiles").mockResolvedValueOnce([file("舊標題", 3)]);
    vi.spyOn(AiJudgeService, "listSessionMessages").mockResolvedValue([]);
    const updateFileAnalysis = vi.spyOn(AiJudgeService, "updateFileAnalysis")
      .mockImplementation(async (_classId, _fileId, analysis, expected) => ({
        ...file(analysis.items[0].title, expected + 1),
        analysis_json: analysis,
      }));

    const { container, cleanup } = mount(
      <ConfirmProvider>
        <RubricsTab classId="class-1" judgeSession={{ id: "session-1", selected_file_id: "file-1" }} />
      </ConfirmProvider>,
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    const titleInput = () => container.querySelector("td textarea");
    expect(titleInput().value).toBe("舊標題");

    /* 背景重抓：這次清單回應拖到自動儲存完成之後才回來 */
    let resolveStaleList;
    listFiles.mockReturnValueOnce(new Promise((resolve) => { resolveStaleList = resolve; }));
    act(() => { void queryClient.refetchQueries({ queryKey: aiJudgeKeys.files("class-1") }); });
    expect(listFiles).toHaveBeenCalledTimes(2);

    act(() => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
      setter.call(titleInput(), "新標題");
      titleInput().dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 750));
    });
    expect(updateFileAnalysis).toHaveBeenCalledTimes(1);
    expect(updateFileAnalysis.mock.calls[0][3]).toBe(3);

    await act(async () => {
      resolveStaleList([file("舊標題", 3)]);
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(titleInput().value).toBe("新標題");
    cleanup();
  });
});
