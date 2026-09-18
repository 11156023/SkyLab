import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  AiJudgeService,
  RUBRIC_POLISH_PROMPT,
  RUBRIC_REASSESS_PROMPT,
  TEACHER_JUDGE_REQUEST_TIMEOUT_MS,
  TEMPLATE_OPTIONS,
  getTemplateLabel,
  shouldDisplayChatMessage,
} from "./aiJudge";

function fakeStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

const jsonResponse = (body = {}) => ({
  ok: true,
  status: 200,
  json: async () => body,
});

let fetchMock;

beforeEach(() => {
  vi.stubGlobal("localStorage", fakeStorage());
  fetchMock = vi.fn().mockResolvedValue(jsonResponse());
  vi.stubGlobal("fetch", fetchMock);
});

describe("AiJudgeService persistent sessions", () => {
  test("session 清單不再傳送進行中或已封存的狀態分類", async () => {
    await AiJudgeService.listSessions("class-1");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/teaching-classes/class-1/judge/sessions/");
    expect(url).not.toContain("status=");
  });

  test("評分環境提供 PostgreSQL 模板", () => {
    expect(TEMPLATE_OPTIONS.map((option) => option.key)).toContain("postgresql");
    expect(getTemplateLabel("postgresql")).toBe("PostgreSQL");
  });

  test("blank 建立請求會帶上檢查表名稱與多選環境", async () => {
    await AiJudgeService.createSession("class-1", {
      title: "期中環境檢查",
      creationMode: "blank",
      rubricName: "期中檢查表",
      environmentKeys: ["python", "linux"],
      selectedFileId: null,
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v1/teaching-classes/class-1/judge/sessions/");
    expect(JSON.parse(init.body)).toEqual({
      title: "期中環境檢查",
      selected_file_id: null,
      creation_mode: "blank",
      rubric_name: "期中檢查表",
      environment_keys: ["python", "linux"],
    });
  });

  test("直接建立空白檢查使用可在編輯頁修改的預設值", async () => {
    await AiJudgeService.createBlankSession("class-1");

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({
      title: "未命名檢查",
      selected_file_id: null,
      creation_mode: "blank",
      rubric_name: "空白檢查表",
      environment_keys: ["n8n"],
    });
  });

  test("直接建立空白檢查會帶入指定名稱", async () => {
    await AiJudgeService.createBlankSession("class-1", {
      title: "期末環境檢查",
      rubricName: "期末環境檢查",
    });

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toMatchObject({
      title: "期末環境檢查",
      creation_mode: "blank",
      rubric_name: "期末環境檢查",
      selected_file_id: null,
    });
  });

  test("existing 建立請求只綁定已保存的檢查表", async () => {
    await AiJudgeService.createSession("class-1", {
      title: "既有文件檢查",
      creationMode: "existing",
      selectedFileId: "file-1",
    });

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({
      title: "既有文件檢查",
      selected_file_id: "file-1",
      creation_mode: "existing",
    });
  });

  test("釘選使用 server-owned is_pinned 欄位", async () => {
    await AiJudgeService.updateSession("class-1", "check-1", { is_pinned: true });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/judge/sessions/check-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ is_pinned: true });
  });

  test("重構使用 class-scoped fork endpoint，不傳 rubric snapshot", async () => {
    await AiJudgeService.forkSession("class-1", "check-1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain(
      "/api/v1/teaching-classes/class-1/judge/sessions/check-1/fork",
    );
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({});
  });

  test("檢查表保存請求會帶 optimistic revision", async () => {
    const analysis = { items: [], total_items: 0 };
    await AiJudgeService.updateFileAnalysis("class-1", "file-1", analysis, 7);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/judge/files/file-1/analysis");
    expect(JSON.parse(init.body)).toEqual({ analysis, expected_revision: 7 });
  });

  test("清除 session 對話使用 messages DELETE endpoint", async () => {
    await AiJudgeService.clearSessionMessages("class-1", "session-1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain(
      "/api/v1/teaching-classes/class-1/judge/sessions/session-1/messages",
    );
    expect(init.method).toBe("DELETE");
  });

  test("標記為 UI 隱藏的 refine 訊息不會顯示在聊天室", () => {
    expect(shouldDisplayChatMessage({ role: "user", content: "內部提示詞" })).toBe(true);
    expect(shouldDisplayChatMessage({
      role: "user",
      content: "內部提示詞",
      metadata_json: { ui_hidden: true },
    })).toBe(false);
    expect(shouldDisplayChatMessage({ role: "user", content: RUBRIC_POLISH_PROMPT })).toBe(false);
    expect(shouldDisplayChatMessage({ role: "user", content: RUBRIC_REASSESS_PROMPT })).toBe(false);
  });

  test("只送出一則新訊息，不回傳 client history", async () => {
    await AiJudgeService.sendSessionMessage("class-1", "session-1", "檢查 nginx");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain(
      "/api/v1/teaching-classes/class-1/judge/sessions/session-1/messages",
    );
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ content: "檢查 nginx" });
  });

  test("聊天室訊息可攜帶已解析附件 ID", async () => {
    await AiJudgeService.sendSessionMessage(
      "class-1",
      "session-1",
      "請依文件補充項目",
      4,
      { attachmentIds: ["attachment-1"] },
    );

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({
      content: "請依文件補充項目",
      analysis_revision: 4,
      attachment_ids: ["attachment-1"],
    });
  });

  test("聊天室附件上傳使用 session-scoped multipart endpoint", async () => {
    const file = new File(["# requirements"], "requirements.md", { type: "text/markdown" });
    await AiJudgeService.uploadSessionAttachment("class-1", "session-1", file);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain(
      "/api/v1/teaching-classes/class-1/judge/sessions/session-1/attachments",
    );
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.body.get("file").name).toBe("requirements.md");
  });

  test("AI 提案請求可攜帶目前檢查表 revision", async () => {
    await AiJudgeService.sendSessionMessage("class-1", "check-1", "補充檢查步驟", 4);

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({
      content: "補充檢查步驟",
      analysis_revision: 4,
    });
  });

  test("潤飾檢查表請求會沿用目前檢查表 revision 並啟用 refine 模式", async () => {
    await AiJudgeService.sendSessionMessage(
      "class-1",
      "check-1",
      RUBRIC_POLISH_PROMPT,
      4,
      { isRefine: true },
    );

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({
      content: RUBRIC_POLISH_PROMPT,
      analysis_revision: 4,
      is_refine: true,
    });
  });

  test("Teacher Judge session AI request 以後端 60 秒 timeout 為準", async () => {
    vi.useFakeTimers();
    let settled = false;
    try {
      fetchMock.mockImplementationOnce((_url, init) => new Promise((_resolve, reject) => {
        init.signal.addEventListener(
          "abort",
          () => reject(new DOMException("aborted", "AbortError")),
          { once: true },
        );
      }));

      const pending = AiJudgeService.sendSessionMessage("class-1", "check-1", "補充檢查");
      pending.catch(() => { settled = true; });

      await vi.advanceTimersByTimeAsync(15_000);
      expect(settled).toBe(false);

      await vi.advanceTimersByTimeAsync(TEACHER_JUDGE_REQUEST_TIMEOUT_MS - 15_000);
      await expect(pending).rejects.toMatchObject({ status: 408, timeout: true });
    } finally {
      vi.useRealTimers();
    }
  });

  test("重新評估提示會要求 AI 更新自動檢測支援狀態與評分計劃", () => {
    expect(RUBRIC_REASSESS_PROMPT).toContain("自動檢測支援狀態");
    expect(RUBRIC_REASSESS_PROMPT).toContain("缺少資訊");
    expect(RUBRIC_REASSESS_PROMPT).toContain("評分計劃書");
    expect(RUBRIC_REASSESS_PROMPT).toContain("不改變原始評分目標");
    expect(RUBRIC_REASSESS_PROMPT).toContain("工作目錄");
    expect(RUBRIC_REASSESS_PROMPT).toContain("主要情境");
    expect(RUBRIC_REASSESS_PROMPT).toContain("其他已啟用的受控能力");
  });

  test("潤飾提示會保留老師目標並要求補足下一層 AI 的執行資訊", () => {
    expect(RUBRIC_POLISH_PROMPT).toContain("下一層檢查 AI");
    expect(RUBRIC_POLISH_PROMPT).toContain("auto、partial 或 manual");
    expect(RUBRIC_POLISH_PROMPT).toContain("成功條件");
    expect(RUBRIC_POLISH_PROMPT).toContain("fallback");
    expect(RUBRIC_POLISH_PROMPT).toContain("check_steps");
    expect(RUBRIC_POLISH_PROMPT).toContain("完整評分項目列表");
    expect(RUBRIC_POLISH_PROMPT).toContain("不要改成較容易但不同的檢查目標");
    expect(RUBRIC_POLISH_PROMPT).toContain("非硬性範圍");
  });

  test("session script endpoint 不接受 client rubric snapshot", async () => {
    await AiJudgeService.createSessionScript("class-1", "session-1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain(
      "/api/v1/teaching-classes/class-1/judge/sessions/session-1/scripts",
    );
    expect(JSON.parse(init.body)).toEqual({});
  });

  test("session script endpoint 可綁定目前檢查表 revision", async () => {
    await AiJudgeService.createSessionScript("class-1", "session-1", 7);

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ analysis_revision: 7 });
  });

  test("腳本產生 request 可超過一般 15 秒 timeout", async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockImplementationOnce((_url, init) => new Promise((resolve, reject) => {
        const timer = setTimeout(
          () => resolve(jsonResponse({ status: "reviewed" })),
          20_000,
        );
        init.signal.addEventListener(
          "abort",
          () => {
            clearTimeout(timer);
            reject(new DOMException("aborted", "AbortError"));
          },
          { once: true },
        );
      }));

      const pending = AiJudgeService.createSessionScript("class-1", "session-1");
      await vi.advanceTimersByTimeAsync(20_000);

      await expect(pending).resolves.toEqual({ status: "reviewed" });
    } finally {
      vi.useRealTimers();
    }
  });

  test("刪除 session 使用 DELETE endpoint", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204 });

    await AiJudgeService.deleteSession("class-1", "session-1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain(
      "/api/v1/teaching-classes/class-1/judge/sessions/session-1",
    );
    expect(init.method).toBe("DELETE");
  });

  test("session filter 會限制腳本 library", async () => {
    await AiJudgeService.listScripts("class-1", "session/1");

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/judge/scripts/?session_id=session%2F1");
  });
});
