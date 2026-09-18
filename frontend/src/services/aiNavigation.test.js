/**
 * aiNavigation.test.js
 * 導覽 API 送出的內容：前文、目前頁面與會話 id。
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { AiNavigationService } from "./aiNavigation";

function fakeStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

const jsonRes = (status, body = {}) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

let fetchMock;

beforeEach(() => {
  vi.stubGlobal("localStorage", fakeStorage());
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

function sentBody() {
  const [, init] = fetchMock.mock.calls[0];
  return JSON.parse(init.body);
}

describe("AiNavigationService.resolve", () => {
  test("帶上前文與目前頁面", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { action: "clarify" }));

    await AiNavigationService.resolve("然後呢？", {
      history: [
        { role: "user", content: "我要申請一台機器" },
        { role: "assistant", content: "先去填申請單" },
      ],
      currentPath: "/my-requests",
    });

    expect(sentBody()).toEqual({
      query: "然後呢？",
      history: [
        { role: "user", content: "我要申請一台機器" },
        { role: "assistant", content: "先去填申請單" },
      ],
      current_path: "/my-requests",
    });
  });

  test("只留最近 12 則，避免把整段對話塞進 prompt", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));
    const history = Array.from({ length: 20 }, (_, index) => ({
      role: index % 2 === 0 ? "user" : "assistant",
      content: `第 ${index} 則`,
    }));

    await AiNavigationService.resolve("再一次", { history });

    const body = sentBody();
    expect(body.history).toHaveLength(12);
    expect(body.history[0].content).toBe("第 8 則");
  });

  test("丟掉不是對話的項目，並截斷過長內容", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await AiNavigationService.resolve("哈囉", {
      history: [
        { role: "system", content: "不該被送出" },
        { role: "user", content: "x".repeat(5000) },
      ],
    });

    const body = sentBody();
    expect(body.history).toHaveLength(1);
    expect(body.history[0].content).toHaveLength(2000);
  });

  test("配置模式帶上獨立保存的需求與正在回答的欄位", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, { ready: false }));

    await AiNavigationService.intake(
      [{ role: "user", content: "我要架網站" }, { role: "system", content: "略過" }],
      { facts: { purpose: "Node.js 網站", gpu: "不需要 GPU" }, pendingKey: "duration", goal: "Node.js 網站上線" },
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/ai/navigation/intake");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      history: [{ role: "user", content: "我要架網站" }],
      facts: { purpose: "Node.js 網站", gpu: "不需要 GPU" },
      pending_key: "duration",
      goal: "Node.js 網站上線",
    });
  });

  test("沒給選項時也能單獨呼叫", async () => {
    fetchMock.mockResolvedValueOnce(jsonRes(200, {}));

    await AiNavigationService.resolve("帶我到我的資源");

    expect(sentBody()).toEqual({
      query: "帶我到我的資源",
      history: [],
      current_path: null,
    });
  });
});
