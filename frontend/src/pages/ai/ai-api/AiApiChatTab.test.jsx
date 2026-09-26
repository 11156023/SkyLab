// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import AiApiChatTab from "./AiApiChatTab";
import { loadChatHistory, saveChatHistory } from "./chatHistory";

const mocks = vi.hoisted(() => ({
  userId: "user-a", listModels: vi.fn(), chat: vi.fn(), confirm: vi.fn(), configured: true,
}));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: mocks.userId } }) }));
vi.mock("../../../components/ConfirmDialog/ConfirmProvider", () => ({ useConfirm: () => mocks.confirm }));
vi.mock("../../../services/aiApiChat", async (importOriginal) => ({
  ...await importOriginal(),
  AiApiChatService: {
    isConfigured: () => mocks.configured, listModels: mocks.listModels, chat: mocks.chat,
  },
}));
vi.mock("react-i18next", async (importOriginal) => ({ ...await importOriginal(), useTranslation: () => ({ t: (key) => key }) }));

let host, root;
beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  mocks.userId = "user-a";
  mocks.configured = true;
  mocks.listModels.mockResolvedValue(["model-a", "model-b"]);
  mocks.chat.mockResolvedValue("模型回覆");
  mocks.confirm.mockResolvedValue(true);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function render() { await act(async () => root.render(<AiApiChatTab />)); }
async function draft(text) {
  const input = host.querySelector("textarea");
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function send(text) {
  await draft(text);
  await act(async () => host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
}

test("切換模型保留上下文；重新掛載恢復對話並依使用者隔離", async () => {
  await render();
  await send("第一句");
  const select = host.querySelector("select");
  await act(async () => { select.value = "model-b"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  await send("第二句");
  expect(mocks.chat.mock.calls[1][0]).toBe("model-b");
  expect(mocks.chat.mock.calls[1][1].map(({ role, content }) => ({ role, content }))).toEqual([
    { role: "user", content: "第一句" }, { role: "assistant", content: "模型回覆" }, { role: "user", content: "第二句" },
  ]);
  expect(loadChatHistory("user-a").conversations[0].messages).toHaveLength(4);
  expect(loadChatHistory("user-b").conversations).toEqual([]);
  await act(async () => root.unmount());
  root = createRoot(host);
  await render();
  expect(host.querySelector('[role="log"]').textContent).toContain("第二句");
  expect(host.querySelector("select").value).toBe("model-b");
  mocks.userId = "user-b";
  await render();
  expect(host.querySelector('[role="log"]').textContent).not.toContain("第一句");
});

test("服務未啟動時保留本機紀錄，重新載入模型後可送出", async () => {
  saveChatHistory("user-a", { activeId: "existing", conversations: [{ id: "existing", title: "舊對話", model: "model-a", messages: [
    { id: "1", role: "user", content: "保留我的紀錄", createdAt: "2026-09-26T00:00:00Z" },
  ] }] });
  mocks.listModels.mockRejectedValueOnce({ status: 503 });
  await render();
  expect(host.querySelector('[role="alert"]').textContent).toBe("AiApiChat.unavailable");
  expect(host.querySelector('[role="log"]').textContent).toContain("保留我的紀錄");
  await draft("新訊息");
  expect(host.querySelector('[type="submit"]').disabled).toBe(true);
  await act(async () => [...host.querySelectorAll("button")].find((button) => button.textContent.includes("AiApiChat.reloadModels")).click());
  expect(host.querySelector('[type="submit"]').disabled).toBe(false);
});

test("既有對話只顯示 think 結束後的最終答案", async () => {
  saveChatHistory("user-a", { activeId: "existing", conversations: [{
    id: "existing", title: "舊對話", model: "NVIDIA-Nemotron-Nano-9B-v2-FP8", messages: [
      { id: "1", role: "assistant", content: "不應顯示的推理</think>\n**可見答案**", model: "NVIDIA-Nemotron-Nano-9B-v2-FP8", createdAt: "2026-09-26T00:00:00Z" },
    ],
  }] });
  await render();
  expect(host.querySelector('[role="log"]').textContent).not.toContain("不應顯示的推理");
  expect(host.querySelector('[role="log"]').textContent).toContain("可見答案");
});

test("生成失敗保留輸入，重試不會重複保存使用者訊息", async () => {
  await render();
  mocks.chat.mockRejectedValueOnce({ status: 429 });
  await send("再試一次");
  expect(host.querySelector('[role="alert"]').textContent).toBe("AiApiChat.rateLimited");
  expect(host.querySelector("textarea").value).toBe("再試一次");
  expect(loadChatHistory("user-a").conversations).toEqual([]);
  await send("再試一次");
  expect(loadChatHistory("user-a").conversations[0].messages).toHaveLength(2);
});

test("首個 token 前顯示思考中，收到串流內容後即時套用 Markdown", async () => {
  let pushDelta;
  let finish;
  mocks.chat.mockImplementation((_model, _messages, options) => {
    pushDelta = options.onDelta;
    return new Promise((resolve) => { finish = resolve; });
  });
  await render();
  await send("請逐步回答");

  expect(host.textContent).toContain("AiApiChat.thinking");
  await act(async () => pushDelta("**即時**"));
  expect(host.textContent).toContain("AiApiChat.streaming");
  expect(host.querySelector('[role="log"] strong').textContent).toBe("AiApiChat.you");
  expect(host.querySelector('[role="log"]').textContent).toContain("即時");

  await act(async () => finish("**即時** 回覆"));
  expect(host.textContent).not.toContain("AiApiChat.thinking");
  expect(loadChatHistory("user-a").conversations[0].messages[1].content).toBe("**即時** 回覆");
});

test("停止等待忽略遲到回覆，離開頁面也會取消請求", async () => {
  let resolveChat;
  mocks.chat.mockImplementation(() => new Promise((resolve) => { resolveChat = resolve; }));
  await render();
  await send("等待中");
  const signal = mocks.chat.mock.calls[0][2].signal;
  await act(async () => [...host.querySelectorAll("button")].find((button) => button.textContent.includes("AiApiChat.stop")).click());
  expect(signal.aborted).toBe(true);
  expect(host.querySelector("textarea").value).toBe("等待中");
  await act(async () => resolveChat("不應出現的回覆"));
  expect(host.textContent).not.toContain("不應出現的回覆");
  expect(loadChatHistory("user-a").conversations).toEqual([]);
  await send("離開時取消");
  const secondSignal = mocks.chat.mock.calls[1][2].signal;
  await act(async () => root.render(null));
  expect(secondSignal.aborted).toBe(true);
});

test("中文輸入法確認文字不送出，Enter 送出只呼叫一次", async () => {
  await render();
  await draft("中文輸入");
  await act(async () => host.querySelector("textarea").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true, cancelable: true })));
  expect(mocks.chat).not.toHaveBeenCalled();
  await act(async () => host.querySelector("textarea").dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })));
  expect(mocks.chat).toHaveBeenCalledTimes(1);
});

test("儲存空間已滿時顯示警告，對話仍可繼續；刪除需確認", async () => {
  await render();
  const actualStorage = localStorage;
  vi.stubGlobal("localStorage", {
    getItem: actualStorage.getItem.bind(actualStorage),
    setItem: () => { throw new DOMException("Full", "QuotaExceededError"); },
  });
  await send("儲存失敗");
  expect(host.textContent).toContain("AiApiChat.storageWarning");
  expect(host.querySelector('[role="log"]').textContent).toContain("模型回覆");
  vi.stubGlobal("localStorage", actualStorage);
  await act(async () => host.querySelector('button[aria-label^="AiApiChat.deleteConversation"]').click());
  expect(mocks.confirm).toHaveBeenCalledOnce();
  expect(loadChatHistory("user-a").conversations).toEqual([]);
});

test("沒有設定金鑰時不查模型，不能送出", async () => {
  mocks.configured = false;
  await render();
  expect(mocks.listModels).not.toHaveBeenCalled();
  expect(host.textContent).toContain("AiApiChat.notConfigured");
  await draft("測試");
  expect(host.querySelector('[type="submit"]').disabled).toBe(true);
});
