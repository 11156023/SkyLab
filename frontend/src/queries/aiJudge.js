import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { AiJudgeService } from "../services/aiJudge";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

/** 整批執行的進度輪詢：剛接手的批次 1.5 秒後問第一次，之後每次回應後 2 秒；失敗時 5 秒後再試 */
export const RUN_BATCH_FIRST_POLL_DELAY = 1_500;
export const RUN_BATCH_POLL_INTERVAL = 2_000;
export const RUN_BATCH_RETRY_DELAY = 5_000;

export const aiJudgeKeys = Object.freeze({
  all: ["aiJudge"],
  sessions: (classId) => ["aiJudge", "sessions", String(classId)],
  files: (classId) => ["aiJudge", "files", String(classId)],
  /* 換了檢查表也要重讀一次對話（提案恢復以來源檢查表判斷），所以 key 帶 selected_file_id */
  sessionMessages: (classId, sessionId, selectedFileId) => [
    "aiJudge", "sessionMessages", String(classId), String(sessionId),
    selectedFileId == null ? null : String(selectedFileId),
  ],
  review: (classId, sessionId) => ["aiJudge", "review", String(classId), String(sessionId)],
  runBatch: (classId, sessionId, runBatchId) => [
    "aiJudge", "runBatch", String(classId), String(sessionId),
    runBatchId == null ? null : String(runBatchId),
  ],
});

/* 原本沒有背景刷新的資料：切回分頁、恢復連線都不自動重抓，只在呼叫端明確要求時重讀 */
const NO_BACKGROUND_REFETCH = Object.freeze({ refetchOnWindowFocus: false, refetchOnReconnect: false });

/** 整批／單次執行是否已結束 */
export function isTerminalRunStatus(status) {
  return status === "completed"
    || status === "completed_with_failures"
    || status === "failed"
    || status === "cancelled";
}

/** 目前生效（已核准且唯一啟用）的檢查點腳本集；沒有或不只一組時回 null */
export function getActiveScriptSet(sets) {
  const active = (Array.isArray(sets) ? sets : []).filter((set) => set?.is_active === true && set.status === "approved");
  return active.length === 1 ? active[0] : null;
}

/**
 * 背景重抓的清單可能在自動儲存回應之前就產生：同一份檢查表保留 analysis_revision 較新的那筆，
 * 否則舊清單會把畫面上的修改與版本號一起退回，下一次儲存就撞 409。
 * 當作檢查表清單查詢的 structuralSharing：每次寫進快取（重抓、自動儲存寫回）都經過這裡。
 */
export function keepNewerRubricFiles(current, rows) {
  const currentById = new Map((Array.isArray(current) ? current : []).map((entry) => [entry.id, entry]));
  return (Array.isArray(rows) ? rows : []).map((row) => {
    const existing = currentById.get(row.id);
    return existing && Number(existing.analysis_revision) > Number(row.analysis_revision) ? existing : row;
  });
}

/** 最後一則是伺服器還在處理的使用者訊息時回傳它（重開頁面要接著等這一輪的結果），否則 null */
export function getProcessingSessionMessage(messages) {
  const latest = Array.isArray(messages) ? messages.at(-1) : undefined;
  return latest?.role === "user" && latest.metadata_json?.processing ? latest : null;
}

/* 對話只在伺服器還在處理上一輪時才背景重抓（每 30 秒、切回分頁時），其他時候不動畫面上的對話 */
function sessionMessagesPollInterval(query) {
  return getProcessingSessionMessage(query.state.data) ? LIST_REFRESH_INTERVAL : false;
}

function sessionMessagesRefetchOnFocus(query) {
  return Boolean(getProcessingSessionMessage(query.state.data));
}

/**
 * 整批執行進度的輪詢間隔（原 TeacherReviewTab 的 setTimeout 鏈）：到終態就停；
 * 剛接手（還沒問過）1.5 秒、之後 2 秒，最近一次失敗則 5 秒後再試。
 * 每次查詢狀態變動都會重新計時，所以間隔是從上一次回應算起。
 */
export function runBatchPollInterval(state) {
  if (isTerminalRunStatus(state?.data?.status)) return false;
  if (state?.status === "error") return RUN_BATCH_RETRY_DELAY;
  return state?.dataUpdateCount ? RUN_BATCH_POLL_INTERVAL : RUN_BATCH_FIRST_POLL_DELAY;
}

const defaultRunBatchInterval = (query) => runBatchPollInterval(query.state);

function throwIfAborted(signal) {
  if (signal?.aborted) throw signal.reason ?? new Error("aborted");
}

/**
 * 導師核查要的整份資料（原 TeacherReviewTab 的 loadReview）：
 * 腳本集＋全班自評回報（讀不到當成 null）→ 生效腳本集的執行紀錄與最近 10 批 →
 * 有批次時讀最新一批（還在跑的優先），只檢查了部分學生就往前補較舊的批次（最多 5 批）；
 * 沒有批次的舊資料改讀最近一次（優先已完成的）單次執行。
 * 被較新的重抓或取消蓋過時，在原本的檢查點停下，不再多打後面的請求。
 */
export async function fetchAiJudgeReview(classId, sessionId, signal) {
  const [sets, progress] = await Promise.all([
    AiJudgeService.listSessionScriptSets(classId, sessionId),
    AiJudgeService.getSessionProgress(classId, sessionId).catch(() => null),
  ]);
  const activeSet = getActiveScriptSet(sets);
  const [runs, batchSummaries] = activeSet ? await Promise.all([
    AiJudgeService.listSessionRuns(classId, sessionId, activeSet.artifact_set_id),
    AiJudgeService.listSessionRunBatches(classId, sessionId, 10, activeSet.artifact_set_id),
  ]) : [[], []];
  throwIfAborted(signal);
  const orderedRuns = Array.isArray(runs) ? runs : [];
  const latest = orderedRuns[0] ?? null;
  const latestBatchId = (Array.isArray(batchSummaries) ? batchSummaries : [])
    .find((batch) => !isTerminalRunStatus(batch.status))?.run_batch_id
    ?? batchSummaries?.[0]?.run_batch_id ?? latest?.run_batch_id;
  let review = null;
  let runningBatch = null;
  if (latestBatchId) {
    const batch = await AiJudgeService.getSessionRunBatch(classId, sessionId, latestBatchId);
    throwIfAborted(signal);
    // 離開再回來時整批還在跑：呼叫端接回進度輪詢，「一次執行」維持停用
    if (batch?.run_batch_id && !isTerminalRunStatus(batch.status)) runningBatch = batch;
    // 最新一批只檢查了部分學生時，往前補較舊的批次，其他人的結果才不會消失
    const batches = [batch];
    const totalStudents = progress?.students?.length ?? 0;
    const covered = new Set((batch?.students ?? []).map((student) => String(student?.student_id)));
    for (const summary of Array.isArray(batchSummaries) ? batchSummaries : []) {
      if (batches.length >= 5 || covered.size >= totalStudents) break;
      if (!summary?.run_batch_id || summary.run_batch_id === batch?.run_batch_id) continue;
      const older = await AiJudgeService.getSessionRunBatch(classId, sessionId, summary.run_batch_id);
      throwIfAborted(signal);
      batches.push(older);
      for (const student of older?.students ?? []) covered.add(String(student?.student_id));
    }
    review = { mode: "batch", batches };
  } else {
    const preferred = orderedRuns.find((item) => item.status === "completed") ?? latest;
    if (preferred) {
      const detail = await AiJudgeService.getSessionRun(classId, sessionId, preferred.id);
      throwIfAborted(signal);
      review = { mode: "run", run: detail };
    }
  }
  return {
    scriptSets: Array.isArray(sets) ? sets : [],
    progress,
    activeSetId: activeSet?.artifact_set_id ?? null,
    review,
    runningBatch,
  };
}

/** 班級的檢查會話（一週一個）；原本沒有背景刷新，製作腳本、執行後由呼叫端 invalidateSessions */
export function useAiJudgeSessionsQuery(classId, { enabled = true } = {}) {
  return useQuery({
    queryKey: aiJudgeKeys.sessions(classId),
    queryFn: () => AiJudgeService.listSessions(classId),
    enabled,
    ...NO_BACKGROUND_REFETCH,
  });
}

/**
 * 班級已保存的檢查表（背景 30 秒刷新）。
 * structuralSharing 用 keepNewerRubricFiles：較早送出的重抓晚於自動儲存回來時，保留版本較新的那筆。
 */
export function useAiJudgeFilesQuery(classId, { enabled = true, refetchInterval = LIST_REFRESH_INTERVAL } = {}) {
  return useQuery({
    queryKey: aiJudgeKeys.files(classId),
    queryFn: () => AiJudgeService.listFiles(classId),
    enabled,
    refetchInterval,
    structuralSharing: keepNewerRubricFiles,
  });
}

/**
 * 一個檢查的對話紀錄（最新一頁；面板沒有往前載入，所以不是 infinite query）。
 * 快取就是畫面上的對話：送出中的訊息、AI 回覆、忽略提案都由呼叫端用 setSessionMessages 寫進來。
 * - gcTime 0：關掉這個檢查就丟掉，重開一定向伺服器讀（跟原本每次進來先清空再讀一樣）
 * - 只在最後一則仍在伺服器處理時背景重抓；paused（傳送中）時完全不重抓，免得蓋掉剛送出的訊息
 */
export function useAiJudgeSessionMessagesQuery(classId, sessionId, selectedFileId, { enabled, paused = false } = {}) {
  return useQuery({
    queryKey: aiJudgeKeys.sessionMessages(classId, sessionId, selectedFileId),
    queryFn: () => AiJudgeService.listSessionMessages(classId, sessionId),
    /* 預設值不寫在參數解構裡：React Compiler 不處理參數預設值裡的運算式 */
    enabled: enabled ?? sessionId != null,
    gcTime: 0,
    refetchInterval: paused ? false : sessionMessagesPollInterval,
    refetchOnWindowFocus: paused ? false : sessionMessagesRefetchOnFocus,
    refetchOnReconnect: false,
  });
}

/**
 * 導師核查的整份資料（見 fetchAiJudgeReview）。只在掛載與呼叫端 refetch() 時讀
 * （切回這個分頁、整批跑完、存完草稿後），不輪詢、不因切回視窗重抓。
 * - gcTime 0：每次進來都重新讀（原本先顯示載入中）
 * - structuralSharing false：每次讀完都是新的物件，呼叫端以參照變化判斷「有新的一份」
 *   （老師有沒存的核查時，新結果先不換上畫面）
 */
export function useAiJudgeReviewQuery(classId, sessionId, { enabled } = {}) {
  return useQuery({
    queryKey: aiJudgeKeys.review(classId, sessionId),
    queryFn: ({ signal }) => fetchAiJudgeReview(classId, sessionId, signal),
    enabled: enabled ?? sessionId != null,
    gcTime: 0,
    structuralSharing: false,
    ...NO_BACKGROUND_REFETCH,
  });
}

/**
 * 一批執行的進度（還沒到終態就輪詢，見 runBatchPollInterval）。
 * initialData 帶建立／恢復／載入時拿到的那份：先顯示它，1.5 秒後才第一次查詢。
 * staleTime Infinity＋refetchOnMount false：換成新的批次時不因「資料過期」立刻多打一次，
 * 只照輪詢的節奏問（不受 QueryClient 預設 staleTime 影響）。
 */
export function useAiJudgeRunBatchQuery(classId, sessionId, runBatchId, {
  enabled,
  initialData,
  refetchInterval = defaultRunBatchInterval,
} = {}) {
  return useQuery({
    queryKey: aiJudgeKeys.runBatch(classId, sessionId, runBatchId),
    queryFn: () => AiJudgeService.getSessionRunBatch(classId, sessionId, runBatchId),
    enabled: enabled ?? runBatchId != null,
    initialData,
    gcTime: 0,
    staleTime: Infinity,
    refetchOnMount: false,
    refetchInterval,
  });
}

export function useAiJudgeCacheUpdater() {
  const queryClient = useQueryClient();
  /* 回傳的物件要穩定：自動儲存只在掛載時建立一次，會一直拿著它 */
  return useMemo(() => ({
    /** 建立或複製週次檢查後放到清單最前面（讀取失敗、清單是空的時候也照樣放進去） */
    upsertSession(classId, session) {
      queryClient.setQueryData(aiJudgeKeys.sessions(classId), (prev) => (
        [session, ...(prev ?? []).filter((entry) => entry.id !== session.id)]
      ));
    },
    /** 改名、移週次、清空對話後就地換掉那一筆 */
    updateSession(classId, session) {
      queryClient.setQueryData(aiJudgeKeys.sessions(classId), (prev) => (
        prev?.map((entry) => (entry.id === session.id ? session : entry))
      ));
    },
    removeSession(classId, sessionId) {
      queryClient.setQueryData(aiJudgeKeys.sessions(classId), (prev) => (
        prev?.filter((entry) => entry.id !== sessionId)
      ));
    },
    invalidateSessions(classId) {
      return queryClient.invalidateQueries({ queryKey: aiJudgeKeys.sessions(classId), exact: true });
    },
    /** 自動儲存寫回後換掉那一份檢查表（版本較舊的不會蓋掉較新的，見 keepNewerRubricFiles） */
    updateFile(classId, file) {
      queryClient.setQueryData(aiJudgeKeys.files(classId), (prev) => (
        prev?.map((entry) => (entry.id === file.id ? file : entry))
      ));
    },
    invalidateFiles(classId) {
      return queryClient.invalidateQueries({ queryKey: aiJudgeKeys.files(classId), exact: true });
    },
    /**
     * 改寫畫面上的對話（next 是新的陣列或 (current) => 新陣列）。
     * 對話已關閉（查詢已從快取移除）就不寫：晚到的回應不能留下一份沒人管的舊對話。
     */
    setSessionMessages(classId, sessionId, selectedFileId, next) {
      const queryKey = aiJudgeKeys.sessionMessages(classId, sessionId, selectedFileId);
      if (!queryClient.getQueryState(queryKey)) return;
      queryClient.setQueryData(queryKey, (prev) => (typeof next === "function" ? next(prev ?? []) : next));
    },
    /**
     * 送出整批執行前丟掉還在路上的核查讀取（例如切回分頁觸發的），舊結果不會在建立後才蓋上來。
     * 還沒有資料（首次載入）時不動，不能把首載取消掉。
     */
    cancelReview(classId, sessionId) {
      const queryKey = aiJudgeKeys.review(classId, sessionId);
      if (queryClient.getQueryData(queryKey) === undefined) return Promise.resolve();
      return queryClient.cancelQueries({ queryKey, exact: true });
    },
  }), [queryClient]);
}
