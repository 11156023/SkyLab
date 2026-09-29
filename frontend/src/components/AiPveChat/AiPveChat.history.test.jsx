// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AiPveChat, { findAiPvePendingRoundIndex, trimAiPveHistory } from "./AiPveChat";
import { AiPveLogService } from "../../services/aiPveLog";

vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal()),
  useTranslation: () => ({ t: (key) => key }),
}));
vi.mock("../../hooks/useToast", () => {
  const toast = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
  return { useToast: () => toast };
});
vi.mock("../../services/aiPveLog", () => ({
  AiPveLogService: { chat: vi.fn(), confirmSsh: vi.fn() },
}));

function sshCall(id, command = "uptime") {
  return {
    id,
    type: "function",
    function: { name: "ssh_exec", arguments: JSON.stringify({ vmid: 101, command }) },
  };
}

function toolContents(payload) {
  return (payload.messages ?? [])
    .filter((message) => message.role === "tool")
    .map((message) => JSON.parse(message.content));
}

function hasPendingToolResult(payload) {
  return toolContents(payload).some((content) => content.pending === true);
}

describe("trimAiPveHistory", () => {
  function buildTurns(count) {
    const history = [{ role: "system", content: "server prompt" }];
    for (let turn = 0; turn < count; turn += 1) {
      history.push({ role: "user", content: `question ${turn}` });
      history.push({
        role: "assistant",
        content: null,
        tool_calls: [
          { id: `call-${turn}-a`, type: "function", function: { name: "get_nodes", arguments: "{}" } },
          { id: `call-${turn}-b`, type: "function", function: { name: "get_storage", arguments: "{}" } },
        ],
      });
      history.push({ role: "tool", tool_call_id: `call-${turn}-a`, content: "{}" });
      history.push({ role: "tool", tool_call_id: `call-${turn}-b`, content: "{}" });
      history.push({ role: "assistant", content: `answer ${turn}` });
    }
    return history;
  }

  it("keeps whole turns under the cap without system messages or orphaned tool results", () => {
    const history = buildTurns(12);
    expect(history).toHaveLength(61);

    const trimmed = trimAiPveHistory(history);

    expect(trimmed.length).toBeLessThanOrEqual(38);
    expect(trimmed[0].role).toBe("user");
    expect(trimmed.some((message) => message.role === "system")).toBe(false);
    trimmed.forEach((message, index) => {
      if (message.role !== "tool") return;
      const issued = trimmed.slice(0, index).some(
        (previous) => previous.role === "assistant"
          && previous.tool_calls?.some((call) => call.id === message.tool_call_id),
      );
      expect(issued).toBe(true);
    });
    // The newest turn always survives.
    expect(trimmed.at(-1)).toEqual({ role: "assistant", content: "answer 11" });
  });

  it("drops system messages even when the history is short", () => {
    expect(trimAiPveHistory([
      { role: "system", content: "server prompt" },
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ])).toEqual([
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello" },
    ]);
    expect(trimAiPveHistory(undefined)).toEqual([]);
  });

  it("never splits a single oversized turn", () => {
    const history = buildTurns(1);
    expect(trimAiPveHistory(history, 2)).toEqual(history.slice(1));
  });
});

describe("findAiPvePendingRoundIndex", () => {
  const history = [
    { role: "user", content: "check" },
    { role: "assistant", content: null, tool_calls: [sshCall("call-1")] },
    { role: "tool", tool_call_id: "call-1", content: JSON.stringify({ pending: true, confirm_token: "tok-1" }) },
  ];

  it("locates the assistant tool-call by id, then by the confirmation token", () => {
    expect(findAiPvePendingRoundIndex(history, { toolCallId: "call-1", token: "tok-1" })).toBe(1);
    expect(findAiPvePendingRoundIndex(history, { toolCallId: null, token: "tok-1" })).toBe(1);
    expect(findAiPvePendingRoundIndex(history, { toolCallId: "other", token: "missing" })).toBe(-1);
  });
});

describe("AiPveChat SSH confirmation keeps a history the server accepts", () => {
  let host;
  let root;

  const pendingResponse = {
    reply: "需要確認",
    needs_confirmation: true,
    tools_called: [{
      name: "ssh_exec",
      tool_call_id: "call-1",
      args: { vmid: 101, command: "uptime", reason: "檢查負載" },
      result: { pending: true, confirm_token: "tok-1" },
    }],
    messages: [
      { role: "system", content: "server prompt" },
      { role: "user", content: "VM 101 很慢" },
      { role: "assistant", content: null, tool_calls: [sshCall("call-1")] },
      { role: "tool", tool_call_id: "call-1", content: JSON.stringify({ pending: true, confirm_token: "tok-1" }) },
    ],
  };

  function button(text) {
    // MIcon 以 ligature 文字渲染圖示，按鈕文字會帶著圖示名稱，所以比對結尾。
    const match = [...host.querySelectorAll("button")].find((item) => item.textContent.trim().endsWith(text));
    expect(match).toBeTruthy();
    return match;
  }

  async function send(text) {
    const composer = host.querySelector('textarea[aria-label="AiPveChat.composerLabel"]');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(composer, text);
      composer.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => button("AiPveChat.sendButton").click());
  }

  beforeEach(async () => {
    vi.clearAllMocks();
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    if (!globalThis.ResizeObserver) {
      globalThis.ResizeObserver = class {
        observe() {}
        disconnect() {}
      };
    }
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => root.render(<AiPveChat />));
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
  });

  it("sends the rejection back right away and never resends a pending tool result", async () => {
    const rejected = { vmid: 101, command: "uptime", error: "使用者已拒絕執行", exit_code: -1 };
    AiPveLogService.chat
      .mockResolvedValueOnce(pendingResponse)
      .mockResolvedValueOnce({
        reply: "好的，不執行。",
        messages: [
          ...pendingResponse.messages.slice(0, 3),
          { role: "tool", tool_call_id: "call-1", content: JSON.stringify(rejected) },
          { role: "assistant", content: "好的，不執行。" },
        ],
      })
      .mockResolvedValueOnce({ reply: "節點正常", messages: [] });
    AiPveLogService.confirmSsh.mockResolvedValue(rejected);

    await send("VM 101 很慢");
    await act(async () => button("AiPveChat.rejectButton").click());

    expect(AiPveLogService.confirmSsh).toHaveBeenCalledWith({
      token: "tok-1", approved: false, command: undefined,
    });
    const continuation = AiPveLogService.chat.mock.calls[1][0];
    expect(hasPendingToolResult(continuation)).toBe(false);
    expect(toolContents(continuation)).toEqual([{ ...rejected, confirmation_token: "tok-1" }]);
    expect(host.textContent).toContain("AiPveChat.commandCancelled");
    expect(host.textContent).toContain("好的，不執行。");

    await send("那節點呢？");
    const next = AiPveLogService.chat.mock.calls[2][0];
    expect(hasPendingToolResult(next)).toBe(false);
    expect(next.messages.some((message) => message.role === "system")).toBe(false);
    expect(next.messages.at(-1)).toEqual({ role: "user", content: "那節點呢？" });
  });

  it("drops the unfinished tool-call round when the approved continuation fails", async () => {
    AiPveLogService.chat
      .mockResolvedValueOnce(pendingResponse)
      .mockRejectedValueOnce(new Error("HTTP 500"))
      .mockResolvedValueOnce({ reply: "節點正常", messages: [] });
    AiPveLogService.confirmSsh.mockResolvedValue({ vmid: 101, command: "uptime", stdout: "load 0.1" });

    await send("VM 101 很慢");
    await act(async () => button("AiPveChat.allowButton").click());
    expect(AiPveLogService.chat).toHaveBeenCalledTimes(2);
    expect(host.textContent).not.toContain("AiPveChat.pendingHeading");

    await send("再試一次");
    const next = AiPveLogService.chat.mock.calls[2][0];
    expect(hasPendingToolResult(next)).toBe(false);
    expect(next.messages).toEqual([
      { role: "user", content: "VM 101 很慢" },
      { role: "user", content: "再試一次" },
    ]);
  });
});
