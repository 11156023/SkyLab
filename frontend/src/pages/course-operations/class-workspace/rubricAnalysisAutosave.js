/** 非 409 儲存失敗後自動重試的間隔（毫秒）；用完就等使用者按「重試」或繼續編輯 */
export const RUBRIC_AUTOSAVE_RETRY_DELAYS = [2_000, 5_000, 15_000];

/**
 * 檢查表分析的自動儲存：輸入 debounce 後串行保存最新內容。
 *
 * 儲存失敗時：
 * - 409（版本衝突）：丟掉排隊中的內容，由呼叫端重新載入伺服器版本。
 * - 其他錯誤：保留最新一份沒存成的內容並標成「未儲存」，依退避自動重試幾次，
 *   之後等 retry()、下一次 flush() 或使用者繼續編輯再試。這段期間 isPending() 維持 true，
 *   背景重抓的清單就不會把畫面上的修改蓋回伺服器上的舊版本。
 *
 * onError(error, { conflict, announce })：announce 為 false 時呼叫端不必再跳通知
 * （自動重試、已經是未儲存狀態下的再次失敗，或呼叫端用 flush({ quiet: true }) 自己說明）。
 * onStatusChange({ unsaved, saving, error, autoRetryScheduled })：狀態有變才呼叫。
 */
export function createRubricAnalysisAutosave({
  save,
  onError,
  onStatusChange,
  delay = 650,
  retryDelays = RUBRIC_AUTOSAVE_RETRY_DELAYS,
  isConflict = (error) => error?.status === 409,
}) {
  let timer = null;
  let retryTimer = null;
  let retryCount = 0;
  let pending = null;
  let inFlightValue = null;
  let inFlight = null;
  let unsaved = false;
  let lastError = null;
  let disposed = false;
  let lastStatus = null;

  function getStatus() {
    return {
      unsaved,
      saving: inFlight !== null,
      error: unsaved ? lastError : null,
      autoRetryScheduled: retryTimer !== null,
    };
  }

  function notify() {
    if (disposed) return;
    const next = getStatus();
    if (lastStatus
      && lastStatus.unsaved === next.unsaved
      && lastStatus.saving === next.saving
      && lastStatus.error === next.error
      && lastStatus.autoRetryScheduled === next.autoRetryScheduled) return;
    lastStatus = next;
    onStatusChange?.(next);
  }

  function clearTimer() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function clearRetryTimer() {
    if (retryTimer !== null) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
  }

  function scheduleRetry() {
    if (disposed || retryTimer !== null || retryCount >= retryDelays.length) return;
    const wait = retryDelays[retryCount];
    retryCount += 1;
    retryTimer = setTimeout(() => {
      retryTimer = null;
      void runFlush("auto");
    }, wait);
  }

  /** mode：explicit（呼叫端 flush／retry）、debounce、auto（自動重試）、quiet */
  function attempt(value, mode) {
    return Promise.resolve()
      .then(() => save(value))
      .then(() => {
        unsaved = false;
        lastError = null;
        retryCount = 0;
        return true;
      })
      .catch((error) => {
        if (isConflict(error)) {
          pending = null;
          unsaved = false;
          lastError = null;
          retryCount = 0;
          clearRetryTimer();
          onError?.(error, { conflict: true, announce: mode !== "quiet" });
          return false;
        }
        const wasUnsaved = unsaved;
        // 失敗期間又有新輸入就保留較新的那份（內容是累積的）；否則把這次沒存成的放回去
        if (pending === null) pending = value;
        unsaved = true;
        lastError = error;
        scheduleRetry();
        const announce = mode === "explicit" || (mode === "debounce" && !wasUnsaved);
        onError?.(error, { conflict: false, announce });
        return false;
      })
      .finally(() => {
        inFlight = null;
        inFlightValue = null;
        notify();
      });
  }

  async function runFlush(mode) {
    clearTimer();
    clearRetryTimer();

    while (!disposed && (pending !== null || inFlight !== null)) {
      if (inFlight !== null) {
        if (!(await inFlight)) return false;
        continue;
      }

      const value = pending;
      pending = null;
      inFlightValue = value;
      inFlight = attempt(value, mode);
      notify();
      if (!(await inFlight)) return false;
    }

    return true;
  }

  /** 立刻保存排隊中的內容；quiet 時一般失敗不跳通知，由呼叫端自己說明為什麼停下來 */
  function flush({ quiet = false } = {}) {
    return runFlush(quiet ? "quiet" : "explicit");
  }

  /** 使用者按「重試」：重新給自動重試的次數，馬上再存一次 */
  function retry() {
    retryCount = 0;
    return runFlush("explicit");
  }

  function schedule(value) {
    if (disposed) return;
    pending = value;
    retryCount = 0;
    clearTimer();
    timer = setTimeout(() => {
      timer = null;
      void runFlush("debounce");
    }, delay);
  }

  function dispose() {
    disposed = true;
    pending = null;
    unsaved = false;
    clearTimer();
    clearRetryTimer();
  }

  return {
    schedule,
    flush,
    retry,
    dispose,
    isPending: () => pending !== null || inFlight !== null || timer !== null,
    /** 最近一次儲存失敗、修改還沒存到伺服器（409 除外） */
    hasUnsaved: () => unsaved,
    getStatus,
    /** 排程中或正在保存的最新內容；供待更新判定以即將保存的版本為基準。 */
    pendingValue: () => pending ?? inFlightValue,
  };
}
