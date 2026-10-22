// @vitest-environment happy-dom
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot } from "react-dom/client";
import { notifyManager } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { TeacherReviewTab } from "./AiJudgePanel";
import { AiJudgeService } from "../../../services/aiJudge";
import { ConfirmProvider } from "../../../components/ConfirmDialog/ConfirmProvider";
import { createTestQueryClient, withQueryClient } from "../../../queries/testing";
import { toast } from "sonner";

let root;
let container;
let queryClient;
const pending = (id) => ({
  run_batch_id: id, status: "pending", summary: { targets: 100, completed: 0, failed: 0 },
  nodes: [{ run_id: "run-1", target_node_key: "web", status: "pending", progress_json: { stage: "preparing", done: 0, total: 100 } }],
  students: [],
});

beforeEach(() => {
  vi.useFakeTimers();
  /* TanStack Query 預設用 setTimeout(0) 通知元件；這裡的 timer 是假的，改成同步通知 */
  notifyManager.setScheduler((callback) => callback());
  queryClient = createTestQueryClient();
  vi.spyOn(AiJudgeService, "listSessionScriptSets").mockResolvedValue([
    { artifact_set_id: "set-1", is_active: true, status: "approved", children: [{ id: "script-1", target_node_key: "web" }] },
  ]);
  vi.spyOn(AiJudgeService, "listSessionRuns").mockResolvedValue([]);
  vi.spyOn(AiJudgeService, "getSessionProgress").mockResolvedValue({ items: [], students: [] });
  vi.spyOn(AiJudgeService, "listSessionRunBatches").mockResolvedValue([]);
  vi.spyOn(AiJudgeService, "getSessionRunBatch").mockRejectedValue({ status: 404 });
  vi.spyOn(AiJudgeService, "createSessionScriptSetRun").mockRejectedValue({ status: 408, timeout: true });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  notifyManager.setScheduler((callback) => setTimeout(callback, 0));
  queryClient.clear();
  vi.restoreAllMocks();
});

async function flush(ms = 50) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

async function mount(classId = "class-1", sessionId = "session-1") {
  await act(async () => root.render(withQueryClient(
    <ConfirmProvider><TeacherReviewTab classId={classId} sessionId={sessionId} members={[]} /></ConfirmProvider>,
    queryClient,
  )));
  await flush();
}

async function confirmRun({ twice = false } = {}) {
  const open = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("一次執行"));
  await act(async () => open.click());
  await flush();
  const confirm = [...document.body.querySelectorAll("button")].find((button) => button.textContent === "確認執行");
  expect(confirm).toBeTruthy();
  await act(async () => {
    confirm.click();
    if (twice) confirm.click();
  });
  await flush(250);
}

test("15 秒逾時後查回原批次，關閉確認視窗並開始輪詢，不重送 POST", async () => {
  AiJudgeService.getSessionRunBatch.mockImplementation(async (_classId, _sessionId, id) => pending(id));
  await mount();
  await confirmRun();
  const { runBatchId } = AiJudgeService.createSessionScriptSetRun.mock.calls[0][3];
  expect(runBatchId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  expect(AiJudgeService.getSessionRunBatch).toHaveBeenCalledWith("class-1", "session-1", runBatchId);
  expect(container.textContent).toContain("準備機器中");
  expect(container.textContent).toContain("執行中 0 / 100 台");
  expect(document.body.querySelector('[role="dialog"]')).toBeNull();
  await flush(1600);
  expect(AiJudgeService.getSessionRunBatch.mock.calls.length).toBeGreaterThan(1);
  expect(AiJudgeService.createSessionScriptSetRun).toHaveBeenCalledTimes(1);
});

test("查詢也暫時失敗時保持阻擋重複執行，稍後自動接回", async () => {
  AiJudgeService.getSessionRunBatch.mockRejectedValueOnce({ status: 503 })
    .mockImplementation(async (_classId, _sessionId, id) => pending(id));
  await mount();
  await confirmRun();
  expect(container.textContent).toContain("正在確認這次執行是否已受理");
  const run = [...container.querySelectorAll("button")].find((button) => button.textContent.includes("一次執行"));
  expect(run.disabled).toBe(true);
  await flush(5100);
  expect(container.textContent).toContain("準備機器中");
  expect(AiJudgeService.createSessionScriptSetRun).toHaveBeenCalledTimes(1);
});

test("沒有收到受理回應時，重新確認沿用原批次 ID 與範圍", async () => {
  await mount();
  await confirmRun();
  const first = AiJudgeService.createSessionScriptSetRun.mock.calls[0];
  AiJudgeService.createSessionScriptSetRun.mockImplementation(async (_classId, _sessionId, _setId, options) => pending(options.runBatchId));
  const retry = [...container.querySelectorAll("button")].find((button) => button.textContent === "重新確認執行");
  await act(async () => retry.click());
  await flush();
  expect(AiJudgeService.createSessionScriptSetRun).toHaveBeenCalledTimes(2);
  expect(AiJudgeService.createSessionScriptSetRun.mock.calls[1]).toEqual(first);
  expect(container.textContent).toContain("準備機器中");
});

test("重載時從批次清單找到準備中的批次，即使沒有 child run 摘要也接回", async () => {
  AiJudgeService.listSessionRunBatches.mockResolvedValue([pending("accepted-1")]);
  AiJudgeService.getSessionRunBatch.mockResolvedValue(pending("accepted-1"));
  await mount();
  expect(AiJudgeService.getSessionRunBatch).toHaveBeenCalledWith("class-1", "session-1", "accepted-1");
  expect(container.textContent).toContain("準備機器中");
  const run = [...container.querySelectorAll("button")].find((button) => button.textContent.endsWith("一次執行"));
  expect(run.disabled).toBe(true);
  expect(AiJudgeService.createSessionScriptSetRun).not.toHaveBeenCalled();
});

test("回應遺失時批次已完成，直接載入結果，不重新建立任務", async () => {
  AiJudgeService.getSessionRunBatch.mockImplementation(async (_classId, _sessionId, id) => ({ ...pending(id), status: "completed" }));
  await mount();
  await confirmRun();
  expect(AiJudgeService.listSessionScriptSets.mock.calls.length).toBeGreaterThan(1);
  expect(container.textContent).not.toContain("正在確認這次執行是否已受理");
  expect(AiJudgeService.createSessionScriptSetRun).toHaveBeenCalledTimes(1);
});

test("另一個請求已受理時，409 從批次清單接回，沒有新增任務", async () => {
  AiJudgeService.createSessionScriptSetRun.mockRejectedValue({ status: 409 });
  await mount();
  AiJudgeService.listSessionRunBatches.mockResolvedValue([pending("other-accepted")]);
  AiJudgeService.getSessionRunBatch.mockImplementation(async (_classId, _sessionId, id) => {
    if (id === "other-accepted") return pending(id);
    throw { status: 404 };
  });
  await confirmRun();
  expect(container.textContent).toContain("準備機器中");
  expect(AiJudgeService.getSessionRunBatch).toHaveBeenCalledWith("class-1", "session-1", "other-accepted");
  expect(AiJudgeService.createSessionScriptSetRun).toHaveBeenCalledTimes(1);
});

test("409 若來自腳本已變更且沒有受理批次，重新載入而不永久卡在恢復狀態", async () => {
  AiJudgeService.createSessionScriptSetRun.mockRejectedValue({ status: 409, message: "script changed" });
  await mount();
  await confirmRun();
  expect(container.textContent).not.toContain("正在確認這次執行是否已受理");
  expect(AiJudgeService.listSessionScriptSets.mock.calls.length).toBeGreaterThan(1);
});

test("快速連按確認也只提交一次", async () => {
  AiJudgeService.createSessionScriptSetRun.mockImplementation(async (_classId, _sessionId, _setId, options) => pending(options.runBatchId));
  await mount();
  await confirmRun({ twice: true });
  expect(AiJudgeService.createSessionScriptSetRun).toHaveBeenCalledTimes(1);
});

test("背景執行器拒收時顯示失敗，不顯示已開始執行或永久等待", async () => {
  const error = vi.spyOn(toast, "error");
  const success = vi.spyOn(toast, "success");
  AiJudgeService.createSessionScriptSetRun.mockResolvedValue({ ...pending("rejected"), status: "failed", error_message: "executor unavailable" });
  await mount();
  await confirmRun();
  expect(error).toHaveBeenCalledWith("executor unavailable");
  expect(success).not.toHaveBeenCalled();
  expect(container.textContent).not.toContain("正在確認這次執行是否已受理");
});

test("切換週次後，舊請求的逾時不影響新週次", async () => {
  let reject;
  AiJudgeService.createSessionScriptSetRun.mockImplementation(() => new Promise((_resolve, fail) => { reject = fail; }));
  await mount();
  await confirmRun();
  await mount("class-2", "session-2");
  await act(async () => reject({ status: 408, timeout: true }));
  await flush();
  expect(container.textContent).not.toContain("正在確認這次執行是否已受理");
  expect(AiJudgeService.getSessionRunBatch).not.toHaveBeenCalled();
});
