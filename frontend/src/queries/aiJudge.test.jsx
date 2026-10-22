// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import {
  RUN_BATCH_FIRST_POLL_DELAY,
  RUN_BATCH_POLL_INTERVAL,
  RUN_BATCH_RETRY_DELAY,
  aiJudgeKeys,
  fetchAiJudgeReview,
  getActiveScriptSet,
  getProcessingSessionMessage,
  isTerminalRunStatus,
  keepNewerRubricFiles,
  runBatchPollInterval,
  useAiJudgeCacheUpdater,
  useAiJudgeFilesQuery,
  useAiJudgeRunBatchQuery,
  useAiJudgeSessionMessagesQuery,
} from "./aiJudge";

const mocks = vi.hoisted(() => ({
  listSessions: vi.fn(),
  listFiles: vi.fn(),
  listSessionMessages: vi.fn(),
  listSessionScriptSets: vi.fn(),
  getSessionProgress: vi.fn(),
  listSessionRuns: vi.fn(),
  listSessionRunBatches: vi.fn(),
  getSessionRunBatch: vi.fn(),
  getSessionRun: vi.fn(),
}));
vi.mock("../services/aiJudge", () => ({ AiJudgeService: mocks }));

let host;
let root;
let seen;
let client;

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  /* 同步通知：讀取結果在同一個 act 裡就反映到 probe（假 timer 也適用） */
  notifyManager.setScheduler((callback) => callback());
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
  notifyManager.setScheduler((callback) => setTimeout(callback, 0));
  vi.useRealTimers();
});

async function render(ui) {
  await act(async () => { root.render(withQueryClient(ui, client)); });
}

const file = (id, revision, title = "t") => ({ id, analysis_revision: revision, analysis_json: { items: [{ title }] } });

describe("純函式", () => {
  test("終態判斷與生效腳本集", () => {
    expect(["completed", "completed_with_failures", "failed", "cancelled"].every(isTerminalRunStatus)).toBe(true);
    expect(["pending", "running", undefined].some(isTerminalRunStatus)).toBe(false);
    const approved = { artifact_set_id: "a", is_active: true, status: "approved" };
    expect(getActiveScriptSet([approved, { artifact_set_id: "b", is_active: false, status: "approved" }])).toBe(approved);
    // 不只一組生效時不猜
    expect(getActiveScriptSet([approved, { ...approved, artifact_set_id: "c" }])).toBeNull();
    expect(getActiveScriptSet(null)).toBeNull();
  });

  test("keepNewerRubricFiles 同一份保留版本較新的那筆，其餘照新清單", () => {
    const newer = file("f1", 5, "畫面上的");
    const merged = keepNewerRubricFiles([newer, file("f2", 1)], [file("f1", 4, "舊的"), file("f2", 2), file("f3", 1)]);
    expect(merged[0]).toBe(newer);
    expect(merged.map((row) => [row.id, row.analysis_revision])).toEqual([["f1", 5], ["f2", 2], ["f3", 1]]);
    expect(keepNewerRubricFiles(undefined, null)).toEqual([]);
  });

  test("處理中訊息只看最後一則使用者訊息", () => {
    const processing = { role: "user", metadata_json: { processing: true } };
    expect(getProcessingSessionMessage([{ role: "assistant" }, processing])).toBe(processing);
    expect(getProcessingSessionMessage([processing, { role: "assistant" }])).toBeNull();
    expect(getProcessingSessionMessage(undefined)).toBeNull();
  });

  test("整批進度輪詢間隔：首次 1.5 秒、之後 2 秒、失敗 5 秒、終態停止", () => {
    expect(runBatchPollInterval({ status: "success", dataUpdateCount: 0, data: { status: "pending" } })).toBe(RUN_BATCH_FIRST_POLL_DELAY);
    expect(runBatchPollInterval({ status: "success", dataUpdateCount: 2, data: { status: "running" } })).toBe(RUN_BATCH_POLL_INTERVAL);
    expect(runBatchPollInterval({ status: "error", dataUpdateCount: 2, data: { status: "running" } })).toBe(RUN_BATCH_RETRY_DELAY);
    expect(runBatchPollInterval({ status: "success", dataUpdateCount: 3, data: { status: "completed" } })).toBe(false);
  });
});

describe("fetchAiJudgeReview", () => {
  const batch = (id, students, status = "completed") => ({
    run_batch_id: id, status, students: students.map((student_id) => ({ student_id, nodes: [] })),
  });

  test("最新一批只檢查部分學生時往前補較舊的批次，補滿全班就停", async () => {
    mocks.listSessionScriptSets.mockResolvedValue([{ artifact_set_id: "set-1", is_active: true, status: "approved" }]);
    mocks.getSessionProgress.mockResolvedValue({ students: [{ user_id: "a" }, { user_id: "b" }] });
    mocks.listSessionRuns.mockResolvedValue([]);
    mocks.listSessionRunBatches.mockResolvedValue([batch("b3", []), batch("b2", []), batch("b1", [])]);
    mocks.getSessionRunBatch.mockImplementation(async (_c, _s, id) => (
      { b3: batch("b3", ["a"], "running"), b2: batch("b2", ["b"]), b1: batch("b1", ["a", "b"]) }[id]
    ));

    const data = await fetchAiJudgeReview("c1", "s1");

    expect(mocks.listSessionRuns).toHaveBeenCalledWith("c1", "s1", "set-1");
    expect(mocks.listSessionRunBatches).toHaveBeenCalledWith("c1", "s1", 10, "set-1");
    expect(mocks.getSessionRunBatch.mock.calls.map((call) => call[2])).toEqual(["b3", "b2"]);
    expect(data.review.mode).toBe("batch");
    expect(data.review.batches.map((entry) => entry.run_batch_id)).toEqual(["b3", "b2"]);
    expect(data.runningBatch.run_batch_id).toBe("b3");
    expect(data.activeSetId).toBe("set-1");
  });

  test("沒有批次時讀最近一次已完成的單次執行；回報讀不到當成 null", async () => {
    mocks.listSessionScriptSets.mockResolvedValue([{ artifact_set_id: "set-1", is_active: true, status: "approved" }]);
    mocks.getSessionProgress.mockRejectedValue(new Error("boom"));
    mocks.listSessionRuns.mockResolvedValue([{ id: "r2", status: "running" }, { id: "r1", status: "completed" }]);
    mocks.listSessionRunBatches.mockResolvedValue([]);
    mocks.getSessionRun.mockResolvedValue({ id: "r1" });

    const data = await fetchAiJudgeReview("c1", "s1");

    expect(mocks.getSessionRun).toHaveBeenCalledWith("c1", "s1", "r1");
    expect(data).toMatchObject({ progress: null, review: { mode: "run", run: { id: "r1" } }, runningBatch: null });
  });

  test("沒有生效腳本集時不查執行紀錄", async () => {
    mocks.listSessionScriptSets.mockResolvedValue([]);
    mocks.getSessionProgress.mockResolvedValue({ students: [] });
    const data = await fetchAiJudgeReview("c1", "s1");
    expect(mocks.listSessionRuns).not.toHaveBeenCalled();
    expect(data).toEqual({ scriptSets: [], progress: { students: [] }, activeSetId: null, review: null, runningBatch: null });
  });

  test("被取消後不再打後面的請求", async () => {
    const controller = new AbortController();
    mocks.listSessionScriptSets.mockResolvedValue([{ artifact_set_id: "set-1", is_active: true, status: "approved" }]);
    mocks.getSessionProgress.mockResolvedValue(null);
    mocks.listSessionRuns.mockImplementation(async () => {
      controller.abort();
      return [{ id: "r1", run_batch_id: "b1", status: "completed" }];
    });
    mocks.listSessionRunBatches.mockResolvedValue([]);
    await expect(fetchAiJudgeReview("c1", "s1", controller.signal)).rejects.toBeTruthy();
    expect(mocks.getSessionRunBatch).not.toHaveBeenCalled();
  });
});

describe("useAiJudgeFilesQuery", () => {
  function Probe({ updater }) {
    const query = useAiJudgeFilesQuery("c1");
    updater.current = useAiJudgeCacheUpdater();
    seen = query.data;
    return null;
  }

  test("自動儲存寫回較新版本後，較早送出的重抓回來也不會退回舊版本", async () => {
    const updater = {};
    mocks.listFiles.mockResolvedValueOnce([file("f1", 3, "舊")]);
    await render(<Probe updater={updater} />);
    expect(seen[0].analysis_revision).toBe(3);

    let resolveStale;
    mocks.listFiles.mockReturnValueOnce(new Promise((resolve) => { resolveStale = resolve; }));
    act(() => { void client.refetchQueries({ queryKey: aiJudgeKeys.files("c1") }); });
    act(() => updater.current.updateFile("c1", file("f1", 4, "新")));
    expect(seen[0].analysis_revision).toBe(4);

    await act(async () => resolveStale([file("f1", 3, "舊"), file("f2", 1)]));
    expect(seen.map((row) => [row.id, row.analysis_revision])).toEqual([["f1", 4], ["f2", 1]]);
    expect(seen[0].analysis_json.items[0].title).toBe("新");
  });
});

describe("useAiJudgeSessionMessagesQuery", () => {
  function Probe({ paused = false, updater }) {
    const query = useAiJudgeSessionMessagesQuery("c1", "s1", "f1", { paused });
    if (updater) updater.current = useAiJudgeCacheUpdater();
    seen = query.data;
    return null;
  }

  test("伺服器還在處理時每 30 秒重抓，處理完就停", async () => {
    vi.useFakeTimers();
    const processing = [{ id: "u1", role: "user", metadata_json: { processing: true } }];
    mocks.listSessionMessages.mockResolvedValueOnce(processing).mockResolvedValue([...processing, { id: "a1", role: "assistant" }]);
    await render(<Probe />);
    expect(mocks.listSessionMessages).toHaveBeenCalledExactlyOnceWith("c1", "s1");

    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(mocks.listSessionMessages).toHaveBeenCalledTimes(2);
    expect(seen.at(-1).id).toBe("a1");

    await act(async () => { await vi.advanceTimersByTimeAsync(90_000); });
    expect(mocks.listSessionMessages).toHaveBeenCalledTimes(2);
  });

  test("傳送中（paused）不重抓，免得蓋掉剛送出的訊息", async () => {
    vi.useFakeTimers();
    mocks.listSessionMessages.mockResolvedValue([{ id: "u1", role: "user", metadata_json: { processing: true } }]);
    await render(<Probe paused />);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(mocks.listSessionMessages).toHaveBeenCalledTimes(1);
  });

  test("setSessionMessages 改寫畫面上的對話；對話已關閉就不寫", async () => {
    const updater = {};
    mocks.listSessionMessages.mockResolvedValue([{ id: "m1" }]);
    await render(<Probe updater={updater} />);
    act(() => updater.current.setSessionMessages("c1", "s1", "f1", (current) => [...current, { id: "m2" }]));
    expect(seen.map((message) => message.id)).toEqual(["m1", "m2"]);

    act(() => updater.current.setSessionMessages("c1", "other", "f1", [{ id: "x" }]));
    expect(client.getQueryState(aiJudgeKeys.sessionMessages("c1", "other", "f1"))).toBeUndefined();
  });
});

describe("useAiJudgeRunBatchQuery", () => {
  function Probe({ runBatchId, initialData }) {
    const query = useAiJudgeRunBatchQuery("c1", "s1", runBatchId, { initialData });
    seen = query.data;
    return null;
  }

  test("先顯示接手的那份，1.5 秒後才第一次查詢，之後 2 秒一次，到終態就停", async () => {
    vi.useFakeTimers();
    const accepted = { run_batch_id: "b1", status: "pending" };
    mocks.getSessionRunBatch
      .mockResolvedValueOnce({ run_batch_id: "b1", status: "running" })
      .mockResolvedValue({ run_batch_id: "b1", status: "completed" });
    await render(<Probe runBatchId="b1" initialData={accepted} />);
    expect(seen).toBe(accepted);
    expect(mocks.getSessionRunBatch).not.toHaveBeenCalled();

    await act(async () => { await vi.advanceTimersByTimeAsync(1_500); });
    expect(mocks.getSessionRunBatch).toHaveBeenCalledExactlyOnceWith("c1", "s1", "b1");
    expect(seen.status).toBe("running");

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(seen.status).toBe("completed");
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocks.getSessionRunBatch).toHaveBeenCalledTimes(2);
  });

  test("沒有批次 id 時不查詢", async () => {
    await render(<Probe runBatchId={null} />);
    expect(seen).toBeUndefined();
    expect(mocks.getSessionRunBatch).not.toHaveBeenCalled();
  });
});

describe("useAiJudgeCacheUpdater", () => {
  test("會話清單就地新增、更新、移除", async () => {
    const updater = {};
    function Probe() {
      updater.current = useAiJudgeCacheUpdater();
      return null;
    }
    await render(<Probe />);
    const key = aiJudgeKeys.sessions("c1");
    client.setQueryData(key, [{ id: "a", title: "A" }, { id: "b", title: "B" }]);

    updater.current.upsertSession("c1", { id: "c", title: "C" });
    updater.current.updateSession("c1", { id: "a", title: "A2" });
    updater.current.removeSession("c1", "b");
    expect(client.getQueryData(key)).toEqual([{ id: "c", title: "C" }, { id: "a", title: "A2" }]);

    // 讀取失敗（還沒有清單）時建立的週次照樣放進去
    updater.current.upsertSession("c2", { id: "x" });
    expect(client.getQueryData(aiJudgeKeys.sessions("c2"))).toEqual([{ id: "x" }]);
    // 沒有清單時更新與移除不動
    updater.current.updateSession("c3", { id: "x" });
    expect(client.getQueryData(aiJudgeKeys.sessions("c3"))).toBeUndefined();
  });

  test("cancelReview 在首次載入（還沒有資料）時不取消", async () => {
    const updater = {};
    function Probe() {
      updater.current = useAiJudgeCacheUpdater();
      return null;
    }
    await render(<Probe />);
    const cancel = vi.spyOn(client, "cancelQueries");
    await updater.current.cancelReview("c1", "s1");
    expect(cancel).not.toHaveBeenCalled();
    client.setQueryData(aiJudgeKeys.review("c1", "s1"), { review: null });
    await updater.current.cancelReview("c1", "s1");
    expect(cancel).toHaveBeenCalledWith({ queryKey: aiJudgeKeys.review("c1", "s1"), exact: true });
  });
});
