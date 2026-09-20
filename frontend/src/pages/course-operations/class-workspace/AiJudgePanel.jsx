import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useSearchParams } from "react-router-dom";
import styles from "./AiJudgePanel.module.scss";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { downloadBlob } from "../../../services/api";
import { focusInvalidField } from "../../../utils/focusField";
import { formatDateTime } from "../../../utils/formatDate";
import { createRubricAnalysisAutosave } from "./rubricAnalysisAutosave";
import {
  AiJudgeService,
  RUBRIC_POLISH_PROMPT,
  getTemplateLabel,
  shouldDisplayChatMessage,
} from "../../../services/aiJudge";

/**
 * Merge server messages by id while retaining chronological server order.
 * Identical content with different ids is valid (for example, a retry), so
 * content is deliberately never used as a de-duplication key.
 */
export function mergeSessionMessages(current = [], incoming = []) {
  const merged = new Map();
  const anonymous = [];
  [...(Array.isArray(current) ? current : []), ...(Array.isArray(incoming) ? incoming : [])]
    .forEach((message, index) => {
      if (!message || typeof message !== "object") return;
      const id = message.id;
      if (id) {
        merged.set(String(id), { message, index });
      } else {
        anonymous.push({ message, index });
      }
    });
  return [
    ...[...merged.values(), ...anonymous]
      .sort((a, b) => {
        const aTime = a.message.created_at ?? "";
        const bTime = b.message.created_at ?? "";
        if (aTime !== bTime) return aTime < bTime ? -1 : 1;
        const aId = a.message.id ? String(a.message.id) : "";
        const bId = b.message.id ? String(b.message.id) : "";
        if (aId !== bId) return aId < bId ? -1 : 1;
        return a.index - b.index;
      })
      .map(({ message }) => message),
  ];
}

/* ── 共用小元件 ─────────────────────────────────────────── */

function Spinner({ size = 16 }) {
  return (
    <span className={styles.spinning}>
      <MIcon name="autorenew" size={size} />
    </span>
  );
}

const SCRIPT_GENERATION_PROGRESS = {
  saving: {
    title: "正在儲存檢查項目",
    message: "正在確認目前編輯已安全儲存。",
  },
  reviewing: {
    title: "正在核對所有檢查項目",
    message: "AI 正在逐項確認檢查方式與必要資訊。",
  },
  queued: {
    title: "已收到製作要求",
    message: "正在準備建立檢查腳本。",
  },
  generating: {
    title: "正在製作檢查腳本",
    message: "AI 正在依目前檢查項目產生收集腳本。",
  },
  policy_review: {
    title: "正在進行安全檢查",
    message: "腳本已產生，正在檢查受管命令與安全規則。",
  },
  ai_review: {
    title: "正在進行 AI 複核",
    message: "正在確認腳本是否覆蓋目前的檢查項目。",
  },
};

export function ScriptGenerationNotice({
  isCreatingScript = false,
  status = null,
  notice = null,
}) {
  if (!isCreatingScript && !notice) return null;

  const workflowStatus = isCreatingScript ? (status || "queued") : notice.status;
  const isError = !isCreatingScript && notice.status === "error";
  const isSuccess = !isCreatingScript && notice.status === "success";
  const progress = SCRIPT_GENERATION_PROGRESS[workflowStatus]
    ?? SCRIPT_GENERATION_PROGRESS.generating;
  const title = isCreatingScript
    ? progress.title
    : isError
      ? "檢查腳本製作失敗"
      : "檢查腳本製作完成";
  const message = isCreatingScript
    ? `${progress.message} 請保持此頁面，完成後會顯示結果；若失敗會保留原因。`
    : notice.message;
  const className = isError
    ? styles.noticeWorkflowError
    : isSuccess
      ? styles.noticeWorkflowSuccess
      : styles.noticeProgress;

  return (
    <div
      className={`${styles.noticeInfo} ${className}`}
      role={isError ? "alert" : "status"}
      aria-live={isError ? "assertive" : "polite"}
      aria-busy={isCreatingScript || undefined}
      data-workflow-status={workflowStatus}
    >
      <p className={styles.noticeProgressTitle}>
        {isCreatingScript ? (
          <Spinner size={16} />
        ) : (
          <MIcon name={isError ? "error_outline" : isSuccess ? "check_circle" : "autorenew"} size={16} />
        )}
        <strong>{title}</strong>
      </p>
      <p>{message}</p>
    </div>
  );
}

/**
 * Session 名稱在清單中維持省略號；只有實際超出可視寬度時，才在 hover/focus
 * 時平移文字以揭示右側尾端。量測放在元件內，讓 sidebar 寬度變化時也能更新。
 */
export function SessionTitle({ children, title }) {
  const viewportRef = useRef(null);
  const titleRef = useRef(null);
  const [isOverflowing, setIsOverflowing] = useState(false);
  const accessibleTitle = title ?? (typeof children === "string" ? children : undefined);

  useEffect(() => {
    const viewport = viewportRef.current;
    const text = titleRef.current;
    if (!viewport || !text) return undefined;

    let frameId = 0;
    const measure = () => {
      if (frameId && typeof window !== "undefined") window.cancelAnimationFrame(frameId);
      const update = () => {
        frameId = 0;
        const overflowWidth = Math.max(0, text.scrollWidth - viewport.clientWidth);
        text.style.setProperty("--session-title-shift", `${overflowWidth}px`);
        setIsOverflowing((current) => {
          const next = overflowWidth > 1;
          return current === next ? current : next;
        });
      };
      if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
        frameId = window.requestAnimationFrame(update);
      } else {
        update();
      }
    };

    measure();
    let observer;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(measure);
      observer.observe(viewport);
      observer.observe(text);
    } else if (typeof window !== "undefined") {
      window.addEventListener("resize", measure);
    }

    return () => {
      if (frameId && typeof window !== "undefined") window.cancelAnimationFrame(frameId);
      observer?.disconnect();
      if (typeof window !== "undefined") window.removeEventListener("resize", measure);
    };
  }, [children]);

  return (
    <span ref={viewportRef} className={styles.sessionTitleViewport}>
      <strong
        ref={titleRef}
        className={`${styles.sessionTitle} ${isOverflowing ? styles.sessionTitleOverflowing : ""}`}
        title={accessibleTitle}
      >
        {children}
      </strong>
    </span>
  );
}

/** 檢查狀態固定收斂為三態：auto=綠、partial=琥珀、manual=紅。 */
const DETECTABLE_INFO = {
  auto: { label: "可以", icon: "check_circle", className: styles.detBadge_auto },
  partial: { label: "缺少資訊", icon: "warning_amber", className: styles.detBadge_partial },
  manual: { label: "導師核查／無法執行", icon: "cancel", className: styles.detBadge_manual },
};
const TEACHER_REVIEW_INFO = {
  label: "導師檢查",
  icon: "cancel",
  className: styles.detBadge_manual,
};

function getDetectableInfo(detectable) {
  return DETECTABLE_INFO[detectable] ?? DETECTABLE_INFO.manual;
}

function hasCompleteParameterizedStep(step) {
  const parameters = step?.parameters ?? {};
  const hasArgv = Array.isArray(parameters.argv)
    && parameters.argv.length > 0
    && parameters.argv.every((part) => typeof part === "string" && part.trim());
  const hasTimeout = Number.isInteger(parameters.timeout_seconds)
    && parameters.timeout_seconds >= 1
    && parameters.timeout_seconds <= 300;
  if (step?.command_key === "python.run_entrypoint") {
    return Boolean(typeof parameters.cwd === "string"
      && parameters.cwd.trim()
      && hasArgv
      && hasTimeout);
  }
  if (step?.command_key === "system.run_command") {
    return Boolean(hasArgv && hasTimeout);
  }
  return true;
}

/** 把單一 check step 的 parameters 轉成老師可讀的唯讀 chip 資料。 */
function stepParameterChips(step) {
  const collector = step?.collector ?? null;
  const parameters = collector ?? step?.parameters ?? {};
  const chips = [];
  const argv = Array.isArray(parameters.argv)
    ? parameters.argv.filter((part) => typeof part === "string" && part.trim())
    : [];
  if (argv.length > 0) {
    chips.push({ key: "argv", label: "指令", mono: true, parts: argv });
  }
  if (typeof parameters.cwd === "string" && parameters.cwd.trim()) {
    chips.push({ key: "cwd", label: "工作目錄", mono: true, parts: [parameters.cwd.trim()] });
  }
  if (typeof parameters.path === "string" && parameters.path.trim()) {
    chips.push({ key: "path", label: "路徑", mono: true, parts: [parameters.path.trim()] });
  }
  if (typeof parameters.url === "string" && parameters.url.trim()) {
    chips.push({ key: "url", label: "URL", mono: true, parts: [parameters.url.trim()] });
  }
  if (collector?.type === "file_text") {
    const mode = parameters.read_mode ?? "full";
    const lineText = Number.isInteger(parameters.lines) ? `，${parameters.lines} 行` : "";
    chips.push({ key: "read_mode", label: "讀取", mono: false, parts: [`${mode}${lineText}`] });
  }
  if (Number.isInteger(parameters.timeout_seconds)
    && parameters.timeout_seconds >= 1) {
    chips.push({ key: "timeout_seconds", label: "逾時", mono: false, parts: [`${parameters.timeout_seconds} 秒`] });
  }
  return chips;
}

/** 提案列的唯讀指令預覽；以分號串接多個步驟的 argv。 */
function proposalCommandPreview(item) {
  const steps = Array.isArray(item?.check_steps) ? item.check_steps : [];
  return steps
    .map((step) => (Array.isArray((step?.collector ?? step?.parameters)?.argv)
      ? (step.collector ?? step.parameters).argv
        .filter((part) => typeof part === "string" && part.trim()).join(" ")
      : ""))
    .filter(Boolean)
    .join("；");
}

/**
 * 解析檢查表中尚未重新確認的項目。新資料使用明確的項目 ID；舊資料只有
 * 整表旗標時，保守地將目前項目視為待確認，避免提示與表格列狀態不一致。
 */
export function getRubricReviewItemIds(analysis, candidateIds = null) {
  const items = Array.isArray(analysis?.items) ? analysis.items : [];
  const currentIds = new Set(items.map((item) => item?.id).filter(Boolean));
  const candidates = candidateIds instanceof Set
    ? new Set(candidateIds)
    : new Set(Array.isArray(candidateIds) ? candidateIds : []);
  const persisted = new Set(
    analysis?.detectability_needs_review && Array.isArray(analysis?.pending_review_item_ids)
      ? analysis.pending_review_item_ids.filter(Boolean)
      : [],
  );
  const reviewIds = candidates.size > 0 ? candidates : persisted;
  if (reviewIds.size === 0 && analysis?.detectability_needs_review) {
    return currentIds;
  }
  return new Set([...reviewIds].filter((itemId) => currentIds.has(itemId)));
}

export function getScriptCreationBlocker({ analysis, pendingProposal = null, pendingReviewIds = new Set() }) {
  const items = Array.isArray(analysis?.items) ? analysis.items : [];
  if (pendingProposal) return "請先套用或保留目前的 AI 檢查項目提案";
  if (items.length === 0) return "請先透過 AI 產生至少一個檢查項目";
  const reviewIds = getRubricReviewItemIds(analysis, pendingReviewIds);
  if (reviewIds.size > 0) {
    return "部分項目的自動檢測支援待更新，請先請 AI 重新確認";
  }
  const unsupportedCount = items.filter((item) => item.detectable === "manual").length;
  const missingCount = items.filter((item) => (
    item.detectable === "partial"
    || (item.detectable === "auto" && (
      !item.detection_method?.trim()
      || !Array.isArray(item.check_steps)
      || item.check_steps.length === 0
      || item.check_steps.some((step) => (
        !hasCompleteParameterizedStep(step)
      ))
    ))
  )).length;
  if (missingCount || unsupportedCount) {
    const details = [
      missingCount ? `${missingCount} 項缺少資訊` : null,
      unsupportedCount ? `${unsupportedCount} 項需要導師核查或無法執行` : null,
    ].filter(Boolean).join("、");
    return `${details}；所有項目都顯示「可以」後，才能製作檢查腳本`;
  }
  return null;
}

const RUBRIC_FILE_EXTENSION = /\.(?:md|txt|doc|docx|pdf)$/i;

/**
 * 檢查表的可讀名稱不應把匯入文件的副檔名帶進工作區標題；原始檔名仍
 * 保留在 `original_filename`，供衝突判斷與下載使用。
 */
export function getRubricDisplayName(file, fallback = "檢查表") {
  const rawName = typeof file === "string"
    ? file
    : [file?.name, file?.display_name, file?.original_filename]
      .find((value) => typeof value === "string" && value.trim());
  const title = String(rawName ?? "")
    .trim()
    .replace(RUBRIC_FILE_EXTENSION, "")
    .trim();
  return title || fallback;
}

export function getRubricCheckTitle(file) {
  return getRubricDisplayName(file, "未命名檢查").slice(0, 255);
}

const SESSION_MENU_WIDTH = 220;
const SESSION_MENU_HEIGHT = 280;
const SESSION_MENU_MARGIN = 12;

/**
 * 將 session 的更多功能選單定位在觸發按鈕附近，同時限制在視窗可見範圍內。
 * 使用 fixed/portal 顯示時，這個位置不會受 session sidebar 的 overflow 影響。
 */
export function getSessionMenuPosition(anchorRect, options = {}) {
  const viewportWidth = options.width ?? (typeof window !== "undefined" ? window.innerWidth : 1024);
  const viewportHeight = options.height ?? (typeof window !== "undefined" ? window.innerHeight : 768);
  const menuWidth = options.menuWidth ?? SESSION_MENU_WIDTH;
  const menuHeight = options.menuHeight ?? SESSION_MENU_HEIGHT;
  const margin = options.margin ?? SESSION_MENU_MARGIN;
  const maxLeft = Math.max(margin, viewportWidth - menuWidth - margin);
  const preferredLeft = anchorRect.right - menuWidth;
  const left = Math.min(Math.max(margin, preferredLeft), maxLeft);
  const belowTop = anchorRect.bottom + margin;
  const aboveTop = anchorRect.top - menuHeight - margin;
  const fitsBelow = belowTop + menuHeight <= viewportHeight - margin;
  const fitsAbove = aboveTop >= margin;
  const preferredTop = fitsBelow ? belowTop : fitsAbove ? aboveTop : belowTop;
  const maxTop = Math.max(margin, viewportHeight - menuHeight - margin);
  const top = Math.min(Math.max(margin, preferredTop), maxTop);
  return { top: Math.round(top), left: Math.round(left) };
}

function proposalOperationLabel(item) {
  const operation = item.operation ?? item.action;
  if (operation === "delete" || operation === "remove") return "刪除";
  if (operation === "update" || operation === "modify") return "修改";
  if (operation === "add" || operation === "create") return "新增";
  return item.id ? "修改" : "新增";
}

function comparableItem(item) {
  return JSON.stringify({
    title: item.title ?? "",
    checked: Boolean(item.checked),
    detectable: item.detectable ?? "manual",
    judgement_mode: item.judgement_mode ?? "ai",
    target_node_key: item.target_node_key ?? null,
    peer_node_key: item.peer_node_key ?? null,
    detection_method: item.detection_method ?? null,
    fallback: item.fallback ?? null,
    missing_information: item.missing_information ?? [],
    check_steps: item.check_steps ?? [],
  });
}

/** 只比較會影響自動檢測支援判斷的檢查項目內容。 */
export function getRubricItemsValue(analysis) {
  const items = Array.isArray(analysis?.items) ? analysis.items : [];
  return JSON.stringify(items.map((item) => ({
    id: item.id ?? "",
    value: comparableItem(item),
  })));
}

/** 只標記與最後儲存內容不同的檢查項目；整表旗標不會外溢到其他列。
 *
 * `pendingSaveAnalysis` 為尚在排程／傳送中的分析內容（最新將被保存的版本）；
 * 競態期間（例如 AI 提案套用後保存仍在進行）以它為基準，避免把 AI 剛套用、
 * 教師實際上沒有編輯的項目誤判成待更新。
 */
export function getPendingRubricItemIds(
  currentItems,
  lastSavedItems,
  previousIds = [],
  lastSavedNeedsReview = false,
  pendingSaveAnalysis = null,
) {
  const baselineItems = pendingSaveAnalysis && Array.isArray(pendingSaveAnalysis.items)
    ? pendingSaveAnalysis.items
    : lastSavedItems;
  const baselineNeedsReview = pendingSaveAnalysis
    ? Boolean(pendingSaveAnalysis.detectability_needs_review)
    : lastSavedNeedsReview;
  const current = Array.isArray(currentItems) ? currentItems : [];
  const saved = Array.isArray(baselineItems) ? baselineItems : [];
  const savedById = new Map(saved.filter((item) => item?.id).map((item) => [item.id, comparableItem(item)]));
  const currentIds = new Set(current.filter((item) => item?.id).map((item) => item.id));
  const next = new Set(previousIds ?? []);

  current.forEach((item) => {
    if (!item?.id) return;
    const savedValue = savedById.get(item.id);
    if (savedValue === undefined || savedValue !== comparableItem(item)) {
      next.add(item.id);
    } else if (!baselineNeedsReview) {
      next.delete(item.id);
    }
  });

  [...next].forEach((itemId) => {
    if (!currentIds.has(itemId)) next.delete(itemId);
  });
  return next;
}

/**
 * 待更新旗標一律由「待確認項目清單」推導：清單為空代表沒有任何項目實際
 * 被編輯，不得保存整表旗標；否則重新載入或自動保存完成後，整表旗標的
 * fallback 會把所有未編輯項目一起標成待更新。
 */
export function resolveDetectabilityNeedsReview({
  requested = null,
  reviewItemIds = new Set(),
  hasActualChange = false,
  lastSavedNeedsReview = false,
  fallbackNeedsReview = false,
}) {
  if (typeof requested !== "boolean") return fallbackNeedsReview;
  if (!requested) return false;
  return (hasActualChange || lastSavedNeedsReview) && reviewItemIds.size > 0;
}

/**
 * 將 AI 回傳的完整項目清單轉成可逐項確認的差異；未出現在回應中的
 * 既有項目保留，只有 AI 明確標示 delete/remove 才會刪除。
 */
export function buildProposalDiff(currentItems, proposedItems) {
  const currentById = new Map(currentItems.map((item) => [item.id, item]));
  const changes = [];
  (Array.isArray(proposedItems) ? proposedItems : []).forEach((rawItem) => {
    const item = { ...rawItem };
    const operation = item.operation ?? item.action;
    if (operation === "delete" || operation === "remove") {
      if (item.id && currentById.has(item.id)) changes.push({ ...item, operation: "delete" });
      return;
    }
    if (!item.id || !currentById.has(item.id)) {
      changes.push({ ...item, operation: "add" });
      return;
    }
    if (comparableItem(currentById.get(item.id)) !== comparableItem(item)) {
      changes.push({ ...item, operation: "update" });
    }
  });
  return changes;
}

/** 只有後端明確標為 Ready／導師檢查的操作可進入套用選取。 */
export function getSelectableProposalIds(proposalItems, itemResults = null) {
  const proposal = Array.isArray(proposalItems) ? proposalItems : [];
  if (!Array.isArray(itemResults)) {
    return new Set(proposal.map((item, index) => item?.id ?? `proposal-${index}`));
  }
  if (itemResults.length === 0) return new Set();
  const resultByOperationId = new Map(
    itemResults
      .filter((result) => result?.operation?.id)
      .map((result) => [String(result.operation.id), result]),
  );
  return new Set(
    proposal
      .map((item, index) => ({ item, id: item?.id ?? `proposal-${index}` }))
      .filter(({ id }) => {
        const result = resultByOperationId.get(String(id));
        return result?.status === "ready" || result?.status === "teacher_review";
      })
      .map(({ id }) => id),
  );
}

/** 將選定的 AI 差異套用成候選項目；未明確刪除的既有項目一律保留。 */
export function applyProposalOperations(currentItems, proposalItems, selectedIds = null) {
  const byId = new Map((Array.isArray(currentItems) ? currentItems : []).map((item) => [item.id, item]));
  const evaluatedIds = new Set();
  (Array.isArray(proposalItems) ? proposalItems : []).forEach((item, index) => {
    const proposalId = item.id ?? `proposal-${index}`;
    if (selectedIds instanceof Set && !selectedIds.has(proposalId)) return;
    const operation = item.operation ?? item.action;
    const cleanItem = { ...item };
    delete cleanItem.operation;
    delete cleanItem.action;
    if (operation === "delete" || operation === "remove") {
      if (item.id) evaluatedIds.add(item.id);
      byId.delete(item.id);
    } else if (item.id && byId.has(item.id)) {
      evaluatedIds.add(item.id);
      byId.set(item.id, { ...byId.get(item.id), ...cleanItem });
    } else {
      const id = item.id ?? `item-${Date.now()}-${byId.size}`;
      evaluatedIds.add(id);
      byId.set(id, { ...cleanItem, id });
    }
  });
  return { items: [...byId.values()], evaluatedIds };
}

const ITEMWISE_STATUS_INFO = {
  needs_information: { label: "缺少資訊", className: styles.detBadge_partial },
  unsupported: { label: "無法自動檢查", className: styles.detBadge_manual },
  analysis_error: { label: "分析失敗", className: styles.detBadge_manual },
};

export function ProposalPanel({ proposal, selectedIds, onToggle, onApply, onSkip, disabled, isRefine = false, itemResults = null }) {
  const contentId = useId();
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    setExpanded(true);
  }, [proposal, itemResults]);

  if (!proposal?.length) return null;
  const results = Array.isArray(itemResults) && itemResults.length ? itemResults : null;
  const proposalById = new Map(proposal.map((item, index) => [item.id ?? `proposal-${index}`, item]));
  return (
    <section className={styles.proposalPreview} aria-label="AI 提案" aria-live="polite">
      <button
        type="button"
        className={styles.proposalToggle}
        aria-expanded={expanded}
        aria-controls={contentId}
        onClick={() => setExpanded((current) => !current)}
      >
        <span className={styles.proposalHeading}>
          <strong>{isRefine ? "AI 核對提案" : "AI 提案"}</strong>
          <small>
            {isRefine ? "待確認" : "Ready"} {proposal.length} · 已選 {selectedIds.size}
          </small>
        </span>
        <span className={styles.proposalToggleAction}>
          {expanded ? "收合" : "展開"}
          <MIcon name={expanded ? "expand_less" : "expand_more"} size={18} />
        </span>
      </button>
      {expanded && (
        <div id={contentId} className={styles.proposalContent}>
          <p className={styles.proposalDescription}>
            {isRefine
              ? "尚有項目未達全綠，請確認 AI 核對結果後再套用。"
              : "只有同意套用的項目才會正式保存到目前檢查表。"}
          </p>
          <div className={styles.proposalList}>
            {results
              ? results.map((result, index) => {
                  const operationId = result.operation?.id;
                  const selectable = (result.status === "ready" || result.status === "teacher_review") && operationId && proposalById.has(operationId);
                  if (selectable) {
                    const item = proposalById.get(operationId);
                    const commandPreview = proposalCommandPreview(item);
                    return (
                      <label className={styles.proposalRow} key={operationId}>
                        <input
                          type="checkbox"
                          checked={selectedIds.has(operationId)}
                          disabled={disabled}
                          onChange={() => onToggle(operationId)}
                        />
                        <span>
                          <b>{result.source_label ? `${result.source_label}·` : ""}{item.title || "未命名項目"}</b>
                          <small><em>{proposalOperationLabel(item)}</em>AI 建議新增或調整此檢查項目</small>
                          {commandPreview && (
                            <code className={styles.proposalCommandPreview}>{commandPreview}</code>
                          )}
                        </span>
                      </label>
                    );
                  }
                  const info = ITEMWISE_STATUS_INFO[result.status] ?? ITEMWISE_STATUS_INFO.analysis_error;
                  const gaps = Array.isArray(result.missing_information) ? result.missing_information.filter(Boolean) : [];
                  const reason = gaps.length
                    ? gaps.join("、")
                    : (result.status === "unsupported" ? result.detail || "" : "");
                  return (
                    <div className={styles.proposalRow} key={`${result.source_index ?? index}-${result.title ?? ""}`}>
                      <span className={`${styles.detBadge} ${styles[info.className]}`}>
                        <MIcon name={result.status === "needs_information" ? "warning_amber" : "cancel"} size={16} aria-hidden="true" />
                        <span>{info.label}</span>
                      </span>
                      <span>
                        <b>{result.source_label ? `${result.source_label}·` : ""}{result.title || "未命名項目"}</b>
                        {reason && <small><em>{reason}</em></small>}
                      </span>
                    </div>
                  );
                })
              : proposal.map((item, index) => {
                  const id = item.id ?? `proposal-${index}`;
                  const commandPreview = proposalCommandPreview(item);
                  return (
                    <label className={styles.proposalRow} key={id}>
                      <input
                        type="checkbox"
                        checked={selectedIds.has(id)}
                        disabled={disabled}
                        onChange={() => onToggle(id)}
                      />
                      <span>
                        <b>{item.title || "未命名項目"}</b>
                        <small><em>{proposalOperationLabel(item)}</em>AI 建議新增或調整此檢查項目</small>
                        {commandPreview && (
                          <code className={styles.proposalCommandPreview}>{commandPreview}</code>
                        )}
                      </span>
                    </label>
                  );
                })}
          </div>
          <div className={styles.proposalActions}>
            <button type="button" className={styles.btnSecondary} disabled={disabled} onClick={onSkip}>忽略</button>
            <button type="button" className={styles.btnPrimary} disabled={disabled || selectedIds.size === 0} onClick={onApply}>同意套用</button>
          </div>
        </div>
      )}
    </section>
  );
}

/* ── 可編輯檢查項目表格 ───────────────────────────────── */

function DetectabilityBadge({ detectable, judgementMode = "ai", needsReview = false }) {
  const detectableInfo = needsReview || detectable === "partial"
    ? DETECTABLE_INFO.partial
    : judgementMode === "teacher"
      ? TEACHER_REVIEW_INFO
      : getDetectableInfo(detectable);
  return (
    <span
      className={`${styles.detBadge} ${detectableInfo.className} ${needsReview ? styles.detBadge_stale : ""}`}
      title={needsReview ? "缺少資訊（自動檢測支援待更新）" : detectableInfo.label}
    >
      <MIcon name={detectableInfo.icon} size={16} aria-hidden="true" />
      <span>{detectableInfo.label}</span>
      {needsReview && <em>待更新</em>}
    </span>
  );
}

function RubricTableRow({ item, index, onChange, onDelete, disabled, needsReview }) {
  const [expanded, setExpanded] = useState(false);
  const checkSteps = item.check_steps ?? [];
  const detailId = `rubric-detail-${index}`;
  const missingInformation = Array.isArray(item.missing_information)
    ? item.missing_information.filter(Boolean)
    : [];
  const hasDetails = Boolean(
    item.detection_method || item.fallback || checkSteps.length || missingInformation.length,
  );

  return (
    <>
      <tr className={`${styles.rubricTableRow} ${expanded ? styles.rubricTableRowExpanded : ""}`}>
        <td className={styles.rubricDetailToggleCell}>
          <button
            type="button"
            className={styles.detailToggle}
            aria-expanded={expanded}
            aria-controls={detailId}
            aria-label={`${expanded ? "收合" : "展開"}第 ${index + 1} 項檢查設定`}
            title={expanded ? "收合檢查設定" : "展開檢查設定"}
            onClick={() => setExpanded((current) => !current)}
          >
            <MIcon name={expanded ? "expand_less" : "expand_more"} size={17} aria-hidden="true" />
          </button>
        </td>
        <td className={styles.rubricNumberCell}>{index + 1}</td>
        <td className={styles.rubricTitleCell}>
          <label className={styles.tableField}>
            <span className={styles.srOnly}>第 {index + 1} 項檢查點</span>
            <input
              value={item.title}
              onChange={(event) => onChange({ ...item, title: event.target.value })}
              placeholder="例如：Python 版本檢查"
              disabled={disabled}
            />
          </label>
        </td>
        <td className={styles.rubricDescriptionCell}>
          <div className={styles.tableField}>
            <span className={styles.srOnly}>第 {index + 1} 項檢測方式</span>
            <p className={`${styles.rubricMethodText} ${!item.detection_method ? styles.rubricMethodTextEmpty : ""}`}>
              {item.detection_method || "尚未提供檢測方式"}
            </p>
          </div>
        </td>
        <td className={styles.rubricDetectabilityCell}>
          <DetectabilityBadge
            detectable={item.detectable}
            judgementMode={item.judgement_mode}
            needsReview={needsReview}
          />
        </td>
        <td className={styles.rubricActionsCell}>
          <div className={styles.tableActions}>
            <button
              type="button"
              className={`${styles.iconBtn} ${styles.iconBtnDanger}`}
              title="刪除項目"
              aria-label={`刪除第 ${index + 1} 項：${item.title || "未命名項目"}`}
              onClick={onDelete}
              disabled={disabled}
            >
              <MIcon name="delete" size={16} />
            </button>
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className={styles.rubricDetailRow}>
          <td id={detailId} colSpan={6}>
            <div className={styles.rubricDetail}>
              <div className={styles.rubricDetailHead}>
                <div>
                  <strong>詳細檢查設定</strong>
                  <span>由 AI 產生，僅供檢視；套用前仍需老師確認。</span>
                </div>
              </div>
              {!hasDetails ? (
                <p className={styles.rubricDetailEmpty}>AI 尚未提供檢測方式，這一項目前以人工確認為主。</p>
              ) : (
                <div className={styles.detectGrid}>
                  {item.detectable === "partial" && (
                    <div className={`${styles.detectItem} ${styles.detectItemWide}`}>
                      <span>缺少資訊</span>
                      <p>{missingInformation.length
                        ? missingInformation.join("、")
                        : "請補充完整的服務名稱、程式位置、連接埠或取證範圍。"}</p>
                    </div>
                  )}
                  {item.fallback && (
                    <div className={styles.detectItem}>
                      <span>無法使用腳本取證時</span>
                      <p>{item.fallback}</p>
                    </div>
                  )}
                  {checkSteps.length > 0 && (
                    <div className={`${styles.detectItem} ${styles.detectItemWide}`}>
                      <span>預計檢查步驟（尚未執行）</span>
                      <div className={styles.stepPlanList}>
                        {checkSteps.map((step, stepIndex) => (
                          <div
                            key={`${step.template_key}-${step.command_key}-${stepIndex}`}
                            className={styles.stepPlanRow}
                          >
                            <span className={styles.chip}>
                              {step.collector
                                ? `受控取證 / ${step.collector.type}`
                                : <>
                                  {getTemplateLabel(step.template_key)} /{" "}
                                  {step.command_label ?? step.command_key}
                                  <code>{step.command_key}</code>
                                </>}
                            </span>
                            {stepParameterChips(step).map((chip) => (
                              <span key={chip.key} className={styles.chip}>
                                <span className={styles.chipLabel}>{chip.label}</span>
                                {chip.mono
                                  ? chip.parts.map((part, partIndex) => (
                                    <code key={partIndex}>{part}</code>
                                  ))
                                  : <span className={styles.chipText}>{chip.parts.join(" ")}</span>}
                              </span>
                            ))}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function RubricTable({ items, onChange, onDelete, disabled, needsReviewIds }) {
  const reviewIds = needsReviewIds instanceof Set
    ? needsReviewIds
    : new Set(Array.isArray(needsReviewIds) ? needsReviewIds : []);
  return (
    <div className={styles.rubricTableWrap}>
      <table className={styles.rubricTable}>
        <caption className={styles.srOnly}>可編輯的 AI 檢查表</caption>
        <thead>
          <tr>
            <th scope="col" className={styles.rubricDetailToggleHeader}>
              <span className={styles.srOnly}>詳細設定</span>
            </th>
            <th scope="col">#</th>
            <th scope="col">檢查點</th>
            <th scope="col">檢測方式</th>
            <th scope="col">自動檢測支援</th>
            <th scope="col"><span className={styles.srOnly}>操作</span></th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => (
            <RubricTableRow
              key={item.id}
              item={item}
              index={index}
              onChange={(updated) => onChange(index, updated)}
              onDelete={() => onDelete(index)}
              disabled={disabled}
              needsReview={reviewIds.has(item.id)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── AI 對話面板 ────────────────────────────────────────── */

/**
 * 工具呼叫結果的教師顯示文字；以後端實際執行結果為準，
 * 覆蓋模型回覆文字可能宣稱但實際未建立的狀態。
 */
export function proposalToolCallLines(message) {
  const toolCalls = Array.isArray(message?.metadata_json?.tool_calls)
    ? message.metadata_json.tool_calls
    : [];
  const lines = [];
  toolCalls.forEach((call) => {
    if (!call || typeof call !== "object") return;
    if (call.status === "staged") {
      const label =
        call.operation === "update"
          ? "已送出修改提案"
          : "已建立提案";
      lines.push({ icon: "check_circle", text: `${label}：${call.title ?? ""}` });
    } else if (call.status === "rejected") {
      lines.push({
        icon: "cancel",
        text: `提案未建立：${call.title ?? ""}`,
      });
    } else if (call.status === "no_change") {
      lines.push({
        icon: "info",
        text: `內容未變更，未建立提案：${call.title ?? ""}`,
      });
    }
  });
  // 去重保留最新：同一文字只保留最後一次（重試只顯示一次錯誤）。
  const seen = new Set();
  const dedupedReversed = [];
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i];
    if (seen.has(line.text)) continue;
    seen.add(line.text);
    dedupedReversed.push(line);
  }
  return dedupedReversed.reverse();
}

export function ChatPanel({
  messages,
  onSendMessage,
  onClearMessages = () => {},
  isLoading,
  isClearing = false,
  disabled = false,
  hasRubric = false,
  onToggleSources,
  sourcesOpen = false,
  sourcesContent,
  pendingAttachments = [],
  onRemoveAttachment,
  onUploadFile,
  isUploading = false,
  loadingText = "",
}) {
  const [input, setInput] = useState("");
  const fileInputRef = useRef(null);
  const messagesEndRef = useRef(null);
  const visibleMessages = messages.filter(shouldDisplayChatMessage);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  function send() {
    const content = input.trim();
    if ((!content && !pendingAttachments.length) || isLoading || isClearing || isUploading || disabled) return;
    onSendMessage(content, false, pendingAttachments);
    setInput("");
  }

  function handleAttachmentInput(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) onUploadFile?.(file);
  }

  return (
    <div className={styles.chatPanel}>
      <div className={styles.chatMessages}>
        {visibleMessages.length === 0 ? (
          <div className={styles.chatEmpty}>
            <MIcon name="smart_toy" size={32} />
            <p>{hasRubric ? "與 AI 對話來精煉你的檢查表" : "先和 AI 討論你的檢查需求"}</p>
            <p className={styles.chatEmptyMeta}>
              {hasRubric
                ? "可以詢問修改建議，或直接下達調整指令"
                : "點擊輸入框旁的＋上傳文件，完成後即可接續討論"}
            </p>
          </div>
        ) : (
          visibleMessages.map((msg, i) => (
            <div
              key={`${msg.role}-${i}`}
              className={`${styles.chatMsgRow} ${msg.role === "user" ? styles.chatMsgRow_user : ""}`}
            >
              {msg.role === "assistant" && (
                <span className={styles.chatAvatar}>
                  <MIcon name="smart_toy" size={16} />
                </span>
              )}
              <div
                className={`${styles.chatBubble} ${msg.role === "user" ? styles.chatBubble_user : ""}`}
              >
                {msg.attachments?.length > 0 && (
                  <div className={styles.chatMessageAttachments}>
                    {msg.attachments.map((attachment) => (
                      <span key={attachment.id} className={styles.chatMessageAttachment}>
                        <MIcon name="description" size={14} />
                        {attachment.original_filename}
                      </span>
                    ))}
                  </div>
                )}
                {msg.content}
                {msg.role === "assistant" && (
                  (() => {
                    const toolLines = proposalToolCallLines(msg);
                    if (!toolLines.length) return null;
                    return (
                      <ul className={styles.chatToolCallList} aria-label="AI 工具執行結果">
                        {toolLines.map((line, idx) => (
                          <li key={`${line.text}-${idx}`} className={styles.chatToolCallItem}>
                            <MIcon name={line.icon} size={14} />
                            <span>{line.text}</span>
                          </li>
                        ))}
                      </ul>
                    );
                  })()
                )}
              </div>
              {msg.role === "user" && (
                <span className={`${styles.chatAvatar} ${styles.chatAvatar_user}`}>
                  <MIcon name="person" size={16} />
                </span>
              )}
            </div>
          ))
        )}

        {isLoading && (
          <div className={styles.chatMsgRow}>
            <span className={styles.chatAvatar}>
              <MIcon name="smart_toy" size={16} />
            </span>
            <div className={styles.chatBubble}>
              {loadingText ? <p className={styles.chatLoadingText}>{loadingText}</p> : null}
              <span className={styles.typing}>
                <span />
                <span />
                <span />
              </span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className={styles.chatInputArea}>
        {pendingAttachments.length > 0 && (
          <div className={styles.chatAttachmentRail} aria-label="待送出的附件">
            {pendingAttachments.map((attachment) => (
              <div key={attachment.id} className={styles.chatAttachmentChip}>
                <MIcon name="description" size={15} />
                <span title={attachment.original_filename}>{attachment.original_filename}</span>
                <small>{attachment.status === "ready" ? "已讀取" : "處理中"}</small>
                {onRemoveAttachment && <button
                  type="button"
                  className={styles.chatAttachmentRemove}
                  aria-label={`移除附件 ${attachment.original_filename}`}
                  disabled={isLoading || isClearing || isUploading || disabled}
                  onClick={() => onRemoveAttachment(attachment)}
                >
                  <MIcon name="close" size={14} />
                </button>}
              </div>
            ))}
          </div>
        )}
        <div className={styles.chatActions}>
          {onToggleSources && <button
            type="button"
            className={styles.btnSecondary}
            disabled={isLoading || isClearing || isUploading || disabled}
            onClick={onToggleSources}
            aria-expanded={sourcesOpen}
            aria-controls="ai-chat-data-sources"
          >
            <MIcon name="description" size={14} />
            資料來源
          </button>}
          <button
            type="button"
            className={styles.btnSecondary}
            disabled={isLoading || isClearing || isUploading || disabled || messages.length === 0}
            onClick={onClearMessages}
          >
            {isClearing ? <Spinner size={14} /> : <MIcon name="delete_sweep" size={14} />}
            清除內容
          </button>
        </div>
        {sourcesOpen && sourcesContent && (
          <div id="ai-chat-data-sources" className={styles.chatSourcesPanel}>
            {sourcesContent}
          </div>
        )}
        <form
          className={styles.chatForm}
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          {onUploadFile && (
            <>
              <input
                ref={fileInputRef}
                type="file"
                accept=".md,.txt,.doc,.docx,.pdf"
                className={styles.srOnly}
                tabIndex={-1}
                onChange={handleAttachmentInput}
              />
              <button
                type="button"
                className={`${styles.iconBtn} ${styles.chatAttachButton}`}
                disabled={isLoading || isClearing || isUploading || disabled}
                aria-label="新增附件"
                title="新增附件"
                onClick={() => fileInputRef.current?.click()}
              >
                <MIcon name="add" size={19} />
              </button>
            </>
          )}
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={
              hasRubric
                ? "輸入訊息...（Shift+Enter 換行）"
                : "描述想檢查的環境或問題...（Shift+Enter 換行）"
            }
            rows={1}
            disabled={isLoading || isClearing || isUploading || disabled}
          />
          <button
            type="submit"
            className={styles.btnPrimary}
            disabled={isLoading || isClearing || isUploading || disabled || (!input.trim() && !pendingAttachments.length)}
            aria-label="送出"
          >
            <MIcon name="send" size={16} />
          </button>
        </form>
        <p className={styles.chatHint}>
          {hasRubric
            ? "提示：描述想檢查的需求，AI 會先核查必要資訊；同意提案後才會正式保存"
            : "提示：先用＋上傳文件；分析完成後，AI 才會提出可套用的檢查項目修改"}
        </p>
      </div>
    </div>
  );
}

export function SaveAndCreateAction({
  onClick,
  disabled = false,
  blocker = null,
  isProcessing = false,
  status = null,
}) {
  const label = {
    saving: "儲存中…",
    reviewing: "核對項目中…",
    queued: "準備製作…",
    generating: "製作腳本中…",
  }[status] ?? "儲存並製作";
  return (
    <div className={styles.rubricActionBar}>
      <p>先儲存目前內容，再由 AI 核對全部項目；全綠後會直接製作腳本。</p>
      <button
        type="button"
        className={`${styles.btnPrimary} ${isProcessing ? styles.btnPrimaryProcessing : ""}`}
        disabled={disabled || isProcessing || Boolean(blocker)}
        onClick={onClick}
        title={blocker || undefined}
        aria-busy={isProcessing}
        data-generation-status={status || undefined}
      >
        {isProcessing ? <Spinner size={14} /> : <MIcon name="save" size={14} />}
        {label}
      </button>
    </div>
  );
}

/* ── 確認 Modal（覆蓋/副本、刪除） ──────────────────────── */

function ConfirmModal({ title, description, actions, closing = false, onClose }) {
  return (
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onMouseDown={onClose}
    >
      <div className={styles.confirm} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.confirmIcon}>
          <MIcon name="warning" size={24} />
        </div>
        <h2>{title}</h2>
        <p>{description}</p>
        <div className={styles.modalActions}>{actions}</div>
      </div>
    </div>
  );
}

/* ── 新增檢查命名 Dialog ──────────────────────────────── */

export function CreateCheckDialog({
  closing = false,
  busy = false,
  error = "",
  onClose = () => {},
  onSubmit = () => {},
}) {
  const [title, setTitle] = useState("");
  const [invalid, setInvalid] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!busy && !closing) inputRef.current?.focus();
  }, [busy, closing]);

  useEffect(() => {
    function closeOnEscape(event) {
      if (event.key === "Escape" && !busy && !closing) {
        event.preventDefault();
        onClose();
      }
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [busy, closing, onClose]);

  function submit(event) {
    event.preventDefault();
    const nextTitle = title.trim();
    if (!nextTitle) {
      setInvalid(true);
      inputRef.current?.focus();
      return;
    }
    onSubmit(nextTitle);
  }

  return (
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <section
        className={`${styles.modal} ${styles.createCheckNameDialog}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-check-name-title"
        aria-describedby="create-check-name-description"
        aria-busy={busy || undefined}
      >
        <div className={styles.modalHeader}>
          <div>
            <h2 id="create-check-name-title">新增檢查</h2>
            <p id="create-check-name-description">輸入名稱後，會直接建立一份空白檢查表。</p>
          </div>
          <button
            type="button"
            className={styles.dialogClose}
            aria-label="關閉"
            disabled={busy}
            onClick={onClose}
          >
            <MIcon name="close" size={18} />
          </button>
        </div>

        <form onSubmit={submit}>
          <label className={styles.dialogField} htmlFor="create-check-name-input">
            <span>檢查名稱</span>
            <input
              id="create-check-name-input"
              ref={inputRef}
              className={`${styles.createCheckNameInput} ${invalid ? styles.fieldInvalid : ""}`}
              value={title}
              maxLength={255}
              placeholder="例如：期中 Python 環境檢查"
              disabled={busy}
              aria-invalid={invalid}
              aria-describedby={invalid ? "create-check-name-error" : undefined}
              onChange={(event) => {
                setTitle(event.target.value);
                setInvalid(false);
              }}
            />
          </label>
          {invalid && (
            <p id="create-check-name-error" className={styles.dialogError} role="alert">
              請輸入檢查名稱。
            </p>
          )}
          {error && <p className={styles.dialogError} role="alert">{error}</p>}
          <div className={styles.modalActions}>
            <button type="button" className={styles.btnSecondary} disabled={busy} onClick={onClose}>
              取消
            </button>
            <button type="submit" className={styles.btnPrimary} disabled={busy}>
              {busy ? <><Spinner size={15} />建立中…</> : "建立空白檢查"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

export function getSelectedRubricSource(files, selectedFileId) {
  if (!selectedFileId || !Array.isArray(files)) return null;
  return files.find((file) => file.status === "active" && file.id === selectedFileId) ?? null;
}

export function resolveActiveSessionId(currentId, sessions) {
  if (!currentId || !Array.isArray(sessions)) return null;
  return sessions.some((session) => session.id === currentId) ? currentId : null;
}

function RubricSourceRail({ classId, file, onClose, embedded = false }) {
  const toast = useToast();

  async function download() {
    try {
      const blob = await AiJudgeService.downloadFile(classId, file.id);
      downloadBlob(blob, file.original_filename ?? `${getRubricDisplayName(file)}.pdf`);
    } catch (error) {
      toast.error(error?.message ?? "下載資料文件失敗");
    }
  }

  if (!file || file.source_type === "created") return null;
  return (
    <aside className={`${styles.sourceRail} ${embedded ? styles.sourceRailEmbedded : ""}`} aria-label="資料來源">
      <div className={styles.sourceRailHead}>
        <div>
          <h3>資料來源</h3>
          <p>這是歷史上傳來源，僅供檢視與下載。</p>
        </div>
        <div className={styles.sourceRailActions}>
          {onClose && <button type="button" className={styles.iconBtn} aria-label="關閉資料來源" title="關閉" onClick={onClose}><MIcon name="close" size={18} /></button>}
        </div>
      </div>
      <div className={`${styles.sourceRow} ${styles.sourceRowSelected}`}>
        <div className={styles.sourceSelect} aria-current="true">
          <span className={styles.sourceIndicator} aria-hidden="true"><MIcon name="description" size={17} /></span>
          <span className={styles.sourceText}>
            <b>{getRubricDisplayName(file, "未命名檢查表")}</b>
            <small>{(file.environment_keys?.length ? file.environment_keys : [file.template_key]).map(getTemplateLabel).join("、")} · {file.analysis_json?.items?.length ?? 0} 項 · {formatDateTime(file.updated_at)}</small>
          </span>
        </div>
        <button type="button" className={styles.iconBtn} aria-label={`下載 ${getRubricDisplayName(file)}`} title="下載原始文件" onClick={download}><MIcon name="download" size={18} /></button>
      </div>
    </aside>
  );
}

/* ── Tab 1：檢查表 ──────────────────────────────────────── */

export function RubricsTab({ classId, judgeSession, onSessionUpdated, onScriptCreated, sidebar = null, tabsBar = null }) {
  const toast = useToast();

  const [files, setFiles] = useState([]);
  const [filesLoaded, setFilesLoaded] = useState(false);

  const [analysis, setAnalysis] = useState(null);
  const [messages, setMessages] = useState([]);
  const [isUploading, setIsUploading] = useState(false);
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [isChatting, setIsChatting] = useState(false);
  const [isClearingMessages, setIsClearingMessages] = useState(false);
  const [isCreatingScript, setIsCreatingScript] = useState(false);
  const [scriptGenerationStatus, setScriptGenerationStatus] = useState(null);
  const [scriptGenerationNotice, setScriptGenerationNotice] = useState(null);
  const [sourceFileId, setSourceFileId] = useState(null);
  const [pendingProposal, setPendingProposal] = useState(null);
  const [selectedProposalIds, setSelectedProposalIds] = useState(() => new Set());
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [pendingProposalMeta, setPendingProposalMeta] = useState(null);
  const [pendingProposalIsRefine, setPendingProposalIsRefine] = useState(false);
  const [pendingItemResults, setPendingItemResults] = useState(null);
  const [isItemwiseAnalysis, setIsItemwiseAnalysis] = useState(false);
  const [environmentKeys, setEnvironmentKeys] = useState([]);
  const analysisRevisionsRef = useRef(new Map());
  const lastSavedValuesRef = useRef(new Map());
  const lastSavedItemsRef = useRef(new Map());
  const lastSavedNeedsReviewRef = useRef(new Map());
  const pendingReviewIdsByFileRef = useRef(new Map());
  const [pendingReviewIds, setPendingReviewIds] = useState(() => new Set());
  const autosaveRef = useRef(null);
  const classIdRef = useRef(classId);
  const toastRef = useRef(toast);
  const selectedSource = useMemo(
    () => files.find((file) => file.id === sourceFileId) ?? null,
    [files, sourceFileId],
  );
  classIdRef.current = classId;
  toastRef.current = toast;

  function clearPendingProposal() {
    setPendingProposal(null);
    setSelectedProposalIds(new Set());
    setPendingProposalMeta(null);
    setPendingProposalIsRefine(false);
    setPendingItemResults(null);
  }

  async function refreshSessionMessages({ silent = false, replace = false } = {}) {
    if (!judgeSession?.id) return false;
    try {
      const rows = await AiJudgeService.listSessionMessages(classId, judgeSession.id);
      setMessages((current) => (replace ? rows : mergeSessionMessages(current, rows)));
      return true;
    } catch (err) {
      if (!silent) toast.error(err?.message ?? "載入檢查對話失敗");
      return false;
    }
  }

  useEffect(() => {
    if (!sourcesOpen) return undefined;
    function closeOnEscape(event) {
      if (event.key === "Escape") setSourcesOpen(false);
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [sourcesOpen]);

  useEffect(() => {
    if (selectedSource?.source_type !== "uploaded") setSourcesOpen(false);
  }, [selectedSource?.source_type]);

  useEffect(() => {
    const autosave = createRubricAnalysisAutosave({
      async save({ fileId, analysis: nextAnalysis }) {
        const updated = await AiJudgeService.updateFileAnalysis(
          classIdRef.current,
          fileId,
          nextAnalysis,
          analysisRevisionsRef.current.get(fileId),
        );
        analysisRevisionsRef.current.set(fileId, updated.analysis_revision);
        const savedAnalysis = updated.analysis_json ?? nextAnalysis;
        lastSavedValuesRef.current.set(fileId, getRubricItemsValue(savedAnalysis));
        lastSavedItemsRef.current.set(fileId, Array.isArray(savedAnalysis.items) ? savedAnalysis.items : []);
        lastSavedNeedsReviewRef.current.set(fileId, Boolean(savedAnalysis.detectability_needs_review));
        pendingReviewIdsByFileRef.current.set(fileId, getRubricReviewItemIds(savedAnalysis));
        setFiles((current) => current.map((entry) => (
          entry.id === updated.id ? updated : entry
        )));
      },
      onError(error) {
        if (error?.status === 409) {
          clearPendingProposal();
          toastRef.current.error("檢查表已經有新的修改，請重新請 AI 產生提案。");
          void AiJudgeService.listFiles(classIdRef.current)
            .then((rows) => {
              setFiles(rows);
              setFilesLoaded(true);
            })
            .catch(() => {});
          return;
        }
        toastRef.current.error(error?.message ?? "更新檢查表失敗");
      },
    });
    autosaveRef.current = autosave;
    return () => {
      if (autosaveRef.current === autosave) autosaveRef.current = null;
      if (autosave.isPending()) {
        void autosave.flush().finally(() => autosave.dispose());
      } else {
        autosave.dispose();
      }
    };
  }, []);

  /** silent = true 時不觸發 loading / error state，供背景自動刷新使用 */
  const fetchFiles = useCallback(async (silent = false) => {
    try {
      setFiles(await AiJudgeService.listFiles(classId));
      setFilesLoaded(true);
    } catch {
      if (!silent) toast.error("載入目前資料來源失敗，請稍後再試。");
    }
  }, [classId, toast]);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles, judgeSession?.selected_file_id]);
  useAutoRefresh(() => fetchFiles(true));

  useEffect(() => {
    let cancelled = false;
    setMessages([]);
    setPendingAttachments([]);
    setPendingProposal(null);
    setSelectedProposalIds(new Set());
    setPendingProposalMeta(null);
    setPendingProposalIsRefine(false);
    if (!judgeSession?.id) return undefined;
    AiJudgeService.listSessionMessages(classId, judgeSession.id)
      .then((rows) => {
        if (!cancelled) setMessages(rows);
      })
      .catch(() => {
        if (!cancelled) toast.error("載入檢查對話失敗");
      });
    return () => {
      cancelled = true;
    };
  }, [classId, judgeSession?.id, judgeSession?.selected_file_id, toast]);

  useEffect(() => {
    function clearSelectedSourceState() {
      setAnalysis(null);
      setScriptGenerationNotice(null);
      setSourceFileId(null);
      setEnvironmentKeys([]);
      setPendingReviewIds(new Set());
      setPendingProposal(null);
      setSelectedProposalIds(new Set());
      setPendingProposalMeta(null);
      setPendingProposalIsRefine(false);
      setPendingItemResults(null);
    }

    if (!judgeSession?.selected_file_id) {
      clearSelectedSourceState();
      return;
    }
    // Keep the current view while the initial file list request is pending.
    if (!filesLoaded) return;
    const file = files.find((item) => item.id === judgeSession.selected_file_id);
    if (!file?.analysis_json) {
      clearSelectedSourceState();
      return;
    }
    if (sourceFileId === file.id && autosaveRef.current?.isPending()) return;
    setAnalysis(file.analysis_json);
    setSourceFileId(file.id);
    setEnvironmentKeys(file.environment_keys?.length ? file.environment_keys : [file.template_key]);
    analysisRevisionsRef.current.set(file.id, file.analysis_revision);
    lastSavedValuesRef.current.set(file.id, getRubricItemsValue(file.analysis_json));
    lastSavedItemsRef.current.set(file.id, Array.isArray(file.analysis_json.items) ? file.analysis_json.items : []);
    lastSavedNeedsReviewRef.current.set(file.id, Boolean(file.analysis_json.detectability_needs_review));
    const savedReviewIds = getRubricReviewItemIds(file.analysis_json);
    pendingReviewIdsByFileRef.current.set(file.id, savedReviewIds);
    setPendingReviewIds(savedReviewIds);
  }, [files, filesLoaded, judgeSession?.selected_file_id, sourceFileId]);

  /** 重算統計欄位後套用新的項目清單 */
  function applyItems(base, nextItems) {
    return {
      ...base,
      items: nextItems,
      total_items: nextItems.length,
      checked_count: nextItems.filter((item) => item.checked).length,
      auto_count: nextItems.filter((item) => item.detectable === "auto").length,
      partial_count: nextItems.filter((item) => item.detectable === "partial").length,
      manual_count: nextItems.filter((item) => item.detectable === "manual").length,
    };
  }

  /** 更新分析結果；persist 時同步寫回已保存的檢查表 */
  function applyAnalysis(
    nextAnalysis,
    {
      persist = false,
      immediate = false,
      detectabilityNeedsReview,
      reviewItemIds,
    } = {},
  ) {
    const currentValue = getRubricItemsValue(nextAnalysis);
    const lastSavedValue = sourceFileId ? lastSavedValuesRef.current.get(sourceFileId) : undefined;
    const hasActualChange = lastSavedValue !== undefined && currentValue !== lastSavedValue;
    const lastSavedNeedsReview = sourceFileId
      ? Boolean(lastSavedNeedsReviewRef.current.get(sourceFileId))
      : false;
    const nextReviewIds = getRubricReviewItemIds(nextAnalysis, reviewItemIds ?? pendingReviewIds);
    const evaluatedNeedsReview = resolveDetectabilityNeedsReview({
      requested: detectabilityNeedsReview,
      reviewItemIds: nextReviewIds,
      hasActualChange,
      lastSavedNeedsReview,
      fallbackNeedsReview: nextAnalysis.detectability_needs_review,
    });
    const evaluatedAnalysis = typeof evaluatedNeedsReview === "boolean"
      ? {
          ...nextAnalysis,
          detectability_needs_review: evaluatedNeedsReview,
          pending_review_item_ids: evaluatedNeedsReview ? [...nextReviewIds] : [],
        }
      : nextAnalysis;
    setAnalysis(evaluatedAnalysis);
    if (persist && sourceFileId) {
      autosaveRef.current?.schedule({ fileId: sourceFileId, analysis: evaluatedAnalysis });
      if (immediate) return autosaveRef.current?.flush() ?? Promise.resolve(false);
    }
    return Promise.resolve(true);
  }

  function updatePendingReviewIds(nextItems) {
    // 自動保存排程中／傳送中時，最新將被保存的內容才是正確基準；
    // 否則 AI 提案套用後的保存延遲期間編輯其他欄位，會把 AI 剛套用、
    // 教師沒有編輯的項目誤判成待更新。
    const pendingSave = autosaveRef.current?.pendingValue?.() ?? null;
    const pendingSaveAnalysis = pendingSave && pendingSave.fileId === sourceFileId
      ? pendingSave.analysis
      : null;
    const savedItems = sourceFileId ? lastSavedItemsRef.current.get(sourceFileId) : [];
    const lastSavedNeedsReview = sourceFileId
      ? Boolean(lastSavedNeedsReviewRef.current.get(sourceFileId))
      : false;
    const previousIds = sourceFileId
      ? pendingReviewIdsByFileRef.current.get(sourceFileId) ?? pendingReviewIds
      : pendingReviewIds;
    const nextIds = getPendingRubricItemIds(
      nextItems,
      savedItems,
      previousIds,
      lastSavedNeedsReview,
      pendingSaveAnalysis,
    );
    if (sourceFileId) pendingReviewIdsByFileRef.current.set(sourceFileId, nextIds);
    setPendingReviewIds(nextIds);
    return nextIds;
  }

  async function handleAddAttachment(file) {
    if (!judgeSession?.id || !file) return false;
    if (pendingAttachments.length >= 5) {
      toast.error("單次最多附加 5 個文件。");
      return false;
    }
    setIsUploading(true);
    try {
      const response = await AiJudgeService.uploadSessionAttachment(
        classId,
        judgeSession.id,
        file,
      );
      const attachment = response.attachment ?? response;
      setPendingAttachments((current) => [...current, attachment]);
      return true;
    } catch (err) {
      toast.error(err?.message ?? "讀取附件失敗");
      return false;
    } finally {
      setIsUploading(false);
    }
  }

  async function handleRemoveAttachment(attachment) {
    if (!judgeSession?.id || !attachment?.id || isUploading) return;
    try {
      await AiJudgeService.deleteSessionAttachment(
        classId,
        judgeSession.id,
        attachment.id,
      );
      setPendingAttachments((current) => current.filter((item) => item.id !== attachment.id));
    } catch (err) {
      toast.error(err?.message ?? "移除附件失敗");
    }
  }

  async function handleSendMessage(content, isRefine = false, attachments = []) {
    if (!judgeSession?.id || !analysis) return;
    if (autosaveRef.current && !(await autosaveRef.current.flush())) return;
    const requestMessages = [...messages, { role: "user", content, attachments }];
    const newMessages = isRefine ? messages : requestMessages;
    setMessages(newMessages);
    setIsChatting(true);
    const itemwise = !isRefine && attachments.length > 0;
    setIsItemwiseAnalysis(itemwise);
    try {
      const response = await AiJudgeService.sendSessionMessage(
        classId,
        judgeSession.id,
        content,
        analysisRevisionsRef.current.get(sourceFileId),
        { isRefine, attachmentIds: attachments.map((item) => item.id) },
      );
      setMessages((current) => {
        const baseMessages = isRefine ? current : current.slice(0, -1);
        return mergeSessionMessages(
          baseMessages,
          [response.user_message, response.assistant_message].filter(Boolean),
        ).filter(shouldDisplayChatMessage);
      });
      setPendingAttachments([]);
      const proposal = buildProposalDiff(analysis?.items ?? [], response.rubric_proposal);
      const itemResults = response.assistant_message?.metadata_json?.item_results;
      setPendingItemResults(Array.isArray(itemResults) && itemResults.length ? itemResults : null);
      setPendingProposal(proposal.length ? proposal : null);
      setSelectedProposalIds(getSelectableProposalIds(proposal, itemResults));
      setPendingProposalMeta(proposal.length ? { baseRevision: response.base_revision ?? analysisRevisionsRef.current.get(sourceFileId) } : null);
      setPendingProposalIsRefine(Boolean(proposal.length && isRefine));
      if (isRefine && !Array.isArray(response.rubric_proposal)) {
        toast.error("AI 未回傳完整檢查項目列表，潤飾尚未套用，請稍後再試");
      } else if (isRefine && !proposal.length && analysis) {
        const saved = await applyAnalysis(applyItems(analysis, analysis.items ?? []), {
          persist: true,
          immediate: true,
          detectabilityNeedsReview: false,
          reviewItemIds: [],
        });
        if (saved) {
          if (sourceFileId) pendingReviewIdsByFileRef.current.set(sourceFileId, new Set());
          setPendingReviewIds(new Set());
          toast.success("潤飾完成，檢查表目前無需修改。");
        }
      }
    } catch (err) {
      const message = err?.message ?? "對話失敗";
      const synced = await refreshSessionMessages({ silent: true, replace: true });
      if (!synced) {
        setMessages(messages);
        toast.error(`${message} 無法確認 Chat 紀錄是否已同步。`);
      } else {
        toast.error(message);
      }
    } finally {
      setIsChatting(false);
      setIsItemwiseAnalysis(false);
    }
  }

  async function applyPendingProposal() {
    if (!pendingProposal) return;
    if (autosaveRef.current && !(await autosaveRef.current.flush())) return;
    const currentRevision = sourceFileId ? analysisRevisionsRef.current.get(sourceFileId) : null;
    if (pendingProposalMeta?.baseRevision && currentRevision !== pendingProposalMeta.baseRevision) {
      clearPendingProposal();
      toast.error("檢查表已經有新的修改，請重新請 AI 產生提案。");
      return;
    }
    const previousAnalysis = analysis;
    const safeSelectedIds = getSelectableProposalIds(
      pendingProposal,
      pendingItemResults,
    );
    const selectedIds = new Set(
      [...selectedProposalIds].filter((id) => safeSelectedIds.has(id)),
    );
    const { items: nextItems, evaluatedIds } = applyProposalOperations(
      analysis?.items ?? [],
      pendingProposal,
      selectedIds,
    );
    const currentPendingIds = sourceFileId
      ? pendingReviewIdsByFileRef.current.get(sourceFileId) ?? pendingReviewIds
      : pendingReviewIds;
    const pendingIdsAfterApply = new Set(currentPendingIds);
    evaluatedIds.forEach((id) => pendingIdsAfterApply.delete(id));
    const saved = await applyAnalysis(applyItems(analysis, nextItems), {
      persist: true,
      immediate: true,
      detectabilityNeedsReview: pendingIdsAfterApply.size > 0,
      reviewItemIds: pendingIdsAfterApply,
    });
    if (!saved) {
      setAnalysis(previousAnalysis);
      return;
    }
    if (sourceFileId) pendingReviewIdsByFileRef.current.set(sourceFileId, pendingIdsAfterApply);
    setPendingReviewIds(pendingIdsAfterApply);
    clearPendingProposal();
    toast.success("已套用 AI 提出的檢查項目修改");
  }

  function handleItemChange(index, updatedItem) {
    const nextItems = [...analysis.items];
    nextItems[index] = updatedItem;
    const nextReviewIds = updatePendingReviewIds(nextItems);
    applyAnalysis(applyItems(analysis, nextItems), {
      persist: true,
      detectabilityNeedsReview: true,
      reviewItemIds: nextReviewIds,
    });
  }

  function handleItemDelete(index) {
    const nextItems = analysis.items.filter((_, i) => i !== index);
    const nextReviewIds = updatePendingReviewIds(nextItems);
    applyAnalysis(applyItems(analysis, nextItems), {
      persist: true,
      detectabilityNeedsReview: true,
      reviewItemIds: nextReviewIds,
    });
  }

  async function handleClearMessages() {
    if (isClearingMessages || isChatting || !messages.length) return;
    setIsClearingMessages(true);
    try {
      if (judgeSession?.id) {
        const updated = await AiJudgeService.clearSessionMessages(classId, judgeSession.id);
        onSessionUpdated?.(updated);
      }
      setMessages([]);
      setPendingAttachments([]);
      setPendingProposal(null);
      setSelectedProposalIds(new Set());
      setPendingProposalMeta(null);
      setPendingProposalIsRefine(false);
      setPendingItemResults(null);
      setScriptGenerationNotice(null);
      toast.success("對話內容已清除");
    } catch (err) {
      toast.error(err?.message ?? "清除對話內容失敗");
    } finally {
      setIsClearingMessages(false);
    }
  }

  async function handleSaveAndCreate() {
    if (!judgeSession?.id || !sourceFileId || !analysis || isCreatingScript) return;
    setIsCreatingScript(true);
    setScriptGenerationStatus("saving");
    setScriptGenerationNotice({
      status: "saving",
      message: "正在儲存目前檢查項目。",
    });
    try {
      if (autosaveRef.current && !(await autosaveRef.current.flush())) {
        setScriptGenerationNotice({
          status: "error",
          message: "檢查表尚未成功儲存，因此尚未開始製作檢查腳本。",
        });
        return;
      }
      const baseRevision = analysisRevisionsRef.current.get(sourceFileId);
      setScriptGenerationStatus("reviewing");
      const response = await AiJudgeService.sendSessionMessage(
        classId,
        judgeSession.id,
        RUBRIC_POLISH_PROMPT,
        baseRevision,
        { isRefine: true },
      );
      const assistantMessage = response?.assistant_message;
      setMessages((current) => mergeSessionMessages(
        current,
        [response?.user_message, assistantMessage].filter(Boolean),
      ).filter(shouldDisplayChatMessage));
      const assistantMetadata = assistantMessage?.metadata_json ?? {};
      if (!Array.isArray(response.rubric_proposal)) {
        const message = "AI 核對結果格式不完整，尚未變更目前檢查表；請稍後重試。";
        setScriptGenerationNotice({ status: "error", message });
        toast.error(message);
        return;
      }
      const proposal = buildProposalDiff(analysis.items ?? [], response.rubric_proposal);
      const itemResults = assistantMetadata.item_results;
      const selectableIds = getSelectableProposalIds(proposal, itemResults);
      const hasSelectable = selectableIds.size > 0;
      setPendingItemResults(
        hasSelectable && Array.isArray(itemResults) && itemResults.length ? itemResults : null,
      );
      setPendingProposal(hasSelectable ? proposal : null);
      setSelectedProposalIds(selectableIds);
      setPendingProposalMeta(hasSelectable ? { baseRevision } : null);
      setPendingProposalIsRefine(hasSelectable);
      if (assistantMetadata.script_ready === false) {
        const assistantSummary = typeof assistantMessage?.content === "string"
          ? assistantMessage.content.trim()
          : "";
        const compactSummary = assistantSummary.length > 360
          ? `${assistantSummary.slice(0, 357)}…`
          : assistantSummary;
        const message = compactSummary || (assistantMetadata.status === "unsupported"
          ? "部分項目目前無法安全取證，請查看 AI 聊天室中的項目說明。"
          : assistantMetadata.status === "analysis_error"
            ? "AI 重新核對未完成，請查看 AI 聊天室中的處理階段與原因。"
            : "尚有項目需要補充，請查看 AI 聊天室中的具體缺口。");
        setScriptGenerationNotice({ status: "error", message });
        toast.error(message);
        return;
      }
      if (assistantMetadata.script_ready !== true) {
        const assistantSummary = typeof assistantMessage?.content === "string"
          ? assistantMessage.content.trim()
          : "";
        const message = assistantSummary.length > 360
          ? `${assistantSummary.slice(0, 357)}…`
          : assistantSummary || "AI 核對結果缺少安全狀態，尚未開始製作檢查腳本；請稍後重試。";
        setScriptGenerationNotice({ status: "error", message });
        toast.error(message);
        return;
      }
      const safeSelectedIds = getSelectableProposalIds(proposal, itemResults);
      const { items: candidateItems } = applyProposalOperations(
        analysis.items ?? [],
        proposal,
        safeSelectedIds,
      );
      const candidateAnalysis = {
        ...applyItems(analysis, candidateItems),
        detectability_needs_review: false,
        pending_review_item_ids: [],
      };
      const saved = await applyAnalysis(candidateAnalysis, {
        persist: true,
        immediate: true,
        detectabilityNeedsReview: false,
        reviewItemIds: [],
      });
      if (!saved) {
        throw new Error("核對結果尚未成功儲存，因此尚未開始製作檢查腳本");
      }
      pendingReviewIdsByFileRef.current.set(sourceFileId, new Set());
      setPendingReviewIds(new Set());
      setPendingProposal(null);
      setSelectedProposalIds(new Set());
      setPendingProposalMeta(null);
      setPendingProposalIsRefine(false);
      const savedRevision = analysisRevisionsRef.current.get(sourceFileId);
      setScriptGenerationStatus("queued");
      setScriptGenerationNotice({
        status: "queued",
        message: "所有檢查項目皆已通過核對，正在準備建立檢查腳本。",
      });
      setScriptGenerationStatus("generating");
      const artifact = await AiJudgeService.createSessionScriptSet(
        classId,
        judgeSession.id,
        savedRevision,
      );
      if (artifact.status === "approved") {
        const message = `檢查腳本已完成契約驗證並建立 ${artifact.children?.length ?? 0} 台機器的腳本，可開始執行。`;
        setScriptGenerationNotice({ status: "success", message });
        toast.success(message);
        onScriptCreated?.(artifact);
      } else if (artifact.status === "review_failed") {
        const message = "腳本自動修正後仍未通過審查；已保留目前檢查表，請確認問題後再試一次。";
        setScriptGenerationNotice({ status: "error", message });
        toast.error(message);
      } else {
        const message = "檢查腳本已產生，請到腳本總覽查看審查結果。";
        setScriptGenerationNotice({ status: "success", message });
        toast.success(message);
        onScriptCreated?.(artifact);
      }
      const synced = await refreshSessionMessages({ silent: true });
      if (!synced) {
        const warning = "無法確認 Chat 紀錄是否已同步。";
        setScriptGenerationNotice((current) => current
          ? { ...current, message: `${current.message} ${warning}` }
          : { status: "error", message: warning });
        toast.error(warning);
      }
    } catch (err) {
      const message = err?.message ?? "儲存並製作檢查腳本失敗";
      const synced = await refreshSessionMessages({ silent: true, replace: true });
      const syncNotice = synced ? "" : "無法確認 Chat 紀錄是否已同步。";
      setScriptGenerationNotice({
        status: "error",
        message: `${message}。目前檢查表已保留，可再次按「儲存並製作」重試。${syncNotice ? ` ${syncNotice}` : ""}`,
      });
      toast.error(syncNotice ? `${message} ${syncNotice}` : message);
    } finally {
      setIsCreatingScript(false);
      setScriptGenerationStatus(null);
    }
  }

  const items = analysis?.items ?? [];
  const scriptCreationBlocker = getScriptCreationBlocker({
    analysis,
    pendingProposal,
    pendingReviewIds,
  });
  const saveAndCreateBlocker = pendingProposal
    ? "請先套用或略過目前的 AI 檢查項目提案"
    : items.length === 0
      ? "請先新增至少一個檢查項目"
      : null;

  return (
    <div className={styles.tabBody}>
      <ScriptGenerationNotice
        isCreatingScript={isCreatingScript}
        status={scriptGenerationStatus}
        notice={scriptGenerationNotice}
      />

      {analysis && items.length === 0 && (
        <div className={styles.noticeInfo}>
          <p><strong>尚未新增檢查項目</strong></p>
          <p>請在聊天室請 AI 產生至少一個檢查項目，才能製作檢查腳本。</p>
        </div>
      )}

      {analysis && items.length > 0 && scriptCreationBlocker && !pendingProposal && (
        <div className={styles.noticeInfo} role="status">
          <p><strong>尚未符合腳本製作條件</strong></p>
          <p>{scriptCreationBlocker}。</p>
        </div>
      )}

      <div className={sidebar ? styles.checkWorkspace : styles.analysisGrid}>
        {sidebar && (
          <aside className={`${styles.card} ${styles.checkSessionCol}`} aria-label="檢查清單">
            {sidebar}
          </aside>
        )}
        <div className={sidebar ? styles.checkRubricCol : styles.analysisMain}>
           {analysis ? (
            <>
              <div className={`${styles.card} ${styles.rubricTableCard} ${sidebar ? styles.checkRubricCard : ""}`}>
                {sidebar && tabsBar && <div className={styles.checkTabsWrap}>{tabsBar}</div>}
                <div className={`${styles.cardHead} ${sidebar ? styles.checkHead : ""}`}>
                  <h4 className={styles.cardTitle}>檢查項目（{items.length}）</h4>
                </div>
                {pendingProposal && <ProposalPanel
                  proposal={pendingProposal}
                  selectedIds={selectedProposalIds}
                  onToggle={(id) => setSelectedProposalIds((current) => {
                    const next = new Set(current);
                    if (next.has(id)) next.delete(id); else next.add(id);
                    return next;
                  })}
                  onApply={applyPendingProposal}
                  onSkip={clearPendingProposal}
                  isRefine={pendingProposalIsRefine}
                  itemResults={pendingItemResults}
                  disabled={isChatting || isClearingMessages}
                />}
                <div className={sidebar ? styles.checkRubricBody : undefined}>
                  <RubricTable
                    items={items}
                    onChange={handleItemChange}
                    onDelete={handleItemDelete}
                    disabled={isChatting || isCreatingScript}
                    needsReviewIds={pendingReviewIds}
                  />
                </div>
                <SaveAndCreateAction
                  onClick={handleSaveAndCreate}
                  disabled={isChatting || isClearingMessages || isUploading}
                  blocker={saveAndCreateBlocker}
                  isProcessing={isCreatingScript}
                  status={scriptGenerationStatus}
                />
              </div>
            </>
          ) : sidebar ? (
            <div className={`${styles.card} ${styles.checkRubricCard}`}>
              {tabsBar && <div className={styles.checkTabsWrap}>{tabsBar}</div>}
              <div className={`${styles.cardHead} ${styles.checkHead}`}>
                <h4 className={styles.cardTitle}>檢查項目</h4>
              </div>
              <div className={styles.mainEmpty}>
                <MIcon name="description" size={30} />
                <p>尚未選擇檢查表來源，請先上傳文件或與 AI 討論。</p>
              </div>
            </div>
          ) : null}
        </div>

        <div className={sidebar ? `${styles.card} ${styles.checkChatCol}` : styles.analysisAside}>
          <div className={sidebar ? styles.checkChatInner : `${styles.card} ${styles.chatCard}`}>
            <div className={sidebar ? styles.checkHead : undefined}>
              <h4 className={styles.cardTitle}>
                <MIcon name="smart_toy" size={18} />
                AI 聊天室
              </h4>
            </div>
            <ChatPanel
              messages={messages}
              onSendMessage={handleSendMessage}
              onClearMessages={handleClearMessages}
              isLoading={isChatting}
              loadingText={isItemwiseAnalysis ? "正在拆解評分表並逐項核查…" : ""}
              isClearing={isClearingMessages}
              disabled={isCreatingScript}
              hasRubric={Boolean(analysis)}
              onToggleSources={selectedSource?.source_type === "uploaded" ? () => setSourcesOpen((current) => !current) : undefined}
              sourcesOpen={sourcesOpen}
              sourcesContent={judgeSession?.id ? (
                <RubricSourceRail
                  classId={classId}
                  file={selectedSource}
                  onClose={() => setSourcesOpen(false)}
                  embedded
                />
              ) : null}
              pendingAttachments={pendingAttachments}
              onRemoveAttachment={handleRemoveAttachment}
              onUploadFile={!judgeSession?.id ? undefined : handleAddAttachment}
              isUploading={isUploading}
            />
          </div>
        </div>
      </div>

    </div>
  );
}

/* ── Tab 3：腳本總覽 ────────────────────────────────────── */

const SCRIPT_STATUS_LABELS = {
  draft: "草稿",
  review_failed: "審查未通過",
  reviewed: "待老師核准",
  approved: "已通過自動檢查",
  archived: "已停用",
};

const RETRY_STOP_REASON_LABELS = {
  passed: "檢查通過",
  same_failure_limit: "相同錯誤已重試 2 次",
  total_retry_limit: "總重試次數已達 4 次",
  unrecoverable_error: "無法自動修正的錯誤",
};

export function getScriptCreationDestination(artifact) {
  return artifact?.status === "approved" ? "review" : "scripts";
}

function scriptStatusBadgeClass(status) {
  if (status === "approved") return styles.badge_success;
  if (status === "review_failed") return styles.badge_danger;
  if (status === "reviewed") return styles.badge_info;
  return styles.badge_muted;
}

function ReviewPanel({ title, result }) {
  const issues = Array.isArray(result?.issues) ? result.issues : [];
  return (
    <div className={styles.reviewPanel}>
      <div className={styles.reviewPanelHead}>
        <span>{title}</span>
        <span
          className={`${styles.badge} ${result?.approved ? styles.badge_success : styles.badge_danger}`}
        >
          {result?.approved ? "通過" : "阻擋"}
        </span>
      </div>
      {issues.length > 0 ? (
        <ul className={styles.reviewIssues}>
          {issues.map((issue, index) => (
            <li key={`${title}-${index}`}>{String(issue)}</li>
          ))}
        </ul>
      ) : (
        <p className={styles.mutedText}>沒有列出風險項目。</p>
      )}
      {result?.suggested_fix && (
        <p className={styles.mutedText}>建議：{String(result.suggested_fix)}</p>
      )}
    </div>
  );
}

export function getScriptReviewAttemptIssues(attempt) {
  const uncoveredIssues = Array.isArray(attempt?.uncovered_rubric_items)
    ? attempt.uncovered_rubric_items.map((item) => {
      if (typeof item === "string") return `未覆蓋檢查項目：${item}`;
      if (!item || typeof item !== "object") return "";
      const label = item.title || item.id;
      return label ? `未覆蓋檢查項目：${label}` : "";
    })
    : [];
  return [...new Set([
    ...(Array.isArray(attempt?.safety_issues) ? attempt.safety_issues : []),
    ...(Array.isArray(attempt?.quality_issues) ? attempt.quality_issues : []),
    ...(Array.isArray(attempt?.coverage_issues) ? attempt.coverage_issues : []),
    ...uncoveredIssues,
    ...(Array.isArray(attempt?.ai_review_issues) ? attempt.ai_review_issues : []),
    ...(Array.isArray(attempt?.generation_issues) ? attempt.generation_issues : []),
  ].filter(Boolean).map((issue) => String(issue)))];
}

function RetrySummary({ script }) {
  const summary = script?.policy_check_result_json?.retry_summary;
  const attempts = Array.isArray(script?.policy_check_result_json?.review_attempts)
    ? script.policy_check_result_json.review_attempts
    : [];
  const coverage = script?.policy_check_result_json?.coverage;
  const coverageFallback = {
    phase: "coverage",
    coverage_issues: Array.isArray(coverage?.issues) ? coverage.issues : [],
    uncovered_rubric_items: Array.isArray(coverage?.uncovered_items)
      ? coverage.uncovered_items
      : [],
  };
  const hasCoverageAttempt = attempts.some(
    (attempt) => attempt?.phase === "coverage"
      || Array.isArray(attempt?.coverage_issues)
      || Array.isArray(attempt?.uncovered_rubric_items),
  );
  const displayedAttempts = !hasCoverageAttempt
    && getScriptReviewAttemptIssues(coverageFallback).length > 0
    ? [...attempts, coverageFallback]
    : attempts;
  if (script?.status !== "review_failed") return null;

  const retryCount = Number(summary?.retry_count ?? 0);
  const stopReason = RETRY_STOP_REASON_LABELS[summary?.stop_reason] ?? "審查未通過";
  return (
    <div className={styles.noticeInfo}>
      <p>
        <strong className={styles.dangerText}>{stopReason}</strong>
      </p>
      <p>
        Agent 已自動重試 {retryCount} 次；仍未通過時，請檢查下列原因，回到檢查表調整後重新製作檢查腳本。
      </p>
      {displayedAttempts.length > 0 && (
        <ul className={styles.reviewIssues}>
          {displayedAttempts.slice(-3).map((attempt, index) => {
            const issues = getScriptReviewAttemptIssues(attempt);
            return (
              <li key={`${attempt?.attempt ?? index}-${attempt?.failure_signature ?? "failure"}`}>
                第 {attempt?.attempt ?? index + 1} 次（{attempt?.phase ?? "審查"}）：
                {issues.slice(0, 2).join("；") || "未提供詳細錯誤"}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function ScriptsTab({
  classId,
  sessionId,
  initialSelectedId = null,
  onScriptApproved,
}) {
  const toast = useToast();
  const [scripts, setScripts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [renameTarget, setRenameTarget] = useState(null);
  const [renameName, setRenameName] = useState("");
  const [renameInvalid, setRenameInvalid] = useState(false);
  const [actionPending, setActionPending] = useState(null);
  const deleteScriptDialog = useDialogPresence(deleteTarget); // "approve" | "delete"
  const renameInputRef = useRef(null);

  const fetchScripts = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      setScripts(await AiJudgeService.listScripts(classId, sessionId));
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [classId, sessionId]);

  useEffect(() => {
    fetchScripts();
  }, [fetchScripts]);

  useEffect(() => {
    if (initialSelectedId) setSelectedId(initialSelectedId);
  }, [initialSelectedId]);

  useEffect(() => {
    if (!renameTarget || !renameInputRef.current) return;
    renameInputRef.current.focus();
    renameInputRef.current.select();
  }, [renameTarget]);

  const selected = useMemo(() => {
    if (scripts.length === 0) return null;
    return scripts.find((script) => script.id === selectedId) ?? scripts[0];
  }, [scripts, selectedId]);
  const selectedIsRenaming = Boolean(selected && renameTarget?.id === selected.id);

  function selectScript(scriptId) {
    if (actionPending !== null) return;
    if (renameTarget && renameTarget.id !== scriptId) {
      setRenameTarget(null);
      setRenameName("");
      setRenameInvalid(false);
    }
    setSelectedId(scriptId);
  }

  async function handleApprove() {
    setActionPending("approve");
    try {
      await AiJudgeService.approveScript(classId, selected.id);
      toast.success("檢查腳本已核准");
      fetchScripts();
      onScriptApproved?.();
    } catch (err) {
      toast.error(err?.message ?? "核准失敗");
    } finally {
      setActionPending(null);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setActionPending("delete");
    try {
      await AiJudgeService.deleteScript(classId, deleteTarget.id);
      toast.success("檢查腳本已刪除");
      setSelectedId(null);
      setDeleteTarget(null);
      setScripts((current) => current.filter((script) => script.id !== deleteTarget.id));
    } catch (err) {
      toast.error(err?.message ?? "刪除失敗");
    } finally {
      setActionPending(null);
    }
  }

  function openRename(script) {
    if (!script || actionPending !== null) return;
    setRenameTarget(script);
    setRenameName(script.name ?? "");
    setRenameInvalid(false);
  }

  function cancelRename() {
    if (actionPending === "rename") return;
    setRenameTarget(null);
    setRenameName("");
    setRenameInvalid(false);
  }

  async function handleRename(event) {
    event?.preventDefault?.();
    const nextName = renameName.trim();
    const target = renameTarget;
    if (!target || actionPending === "rename") return;
    if (!nextName) {
      setRenameInvalid(true);
      focusInvalidField(renameInputRef.current);
      return;
    }
    if (nextName === String(target.name ?? "").trim()) {
      cancelRename();
      return;
    }
    setActionPending("rename");
    try {
      const updated = await AiJudgeService.renameScript(classId, target.id, nextName);
      toast.success("檢查腳本已重新命名");
      setScripts((current) =>
        current.map((script) => (script.id === updated.id ? updated : script)),
      );
      setRenameTarget(null);
      setRenameName("");
      setRenameInvalid(false);
    } catch (err) {
      toast.error(err?.message ?? "重新命名失敗");
    } finally {
      setActionPending(null);
    }
  }

  return (
    <div className={styles.tabBody}>
      {loading ? (
        <LoadingState text="載入腳本中..." />
      ) : error ? (
        <div className={styles.card}>
          <div className={styles.cardHead}>
            <span className={styles.dangerText}>載入檢查腳本失敗，請稍後再試。</span>
            <button type="button" className={styles.btnSecondary} onClick={fetchScripts}>
              重新載入
            </button>
          </div>
        </div>
      ) : scripts.length === 0 ? (
        <div className={styles.card}>
          <p className={styles.mutedText}>
            尚未建立檢查腳本。請先建立或上傳資料文件，完成檢查表調整後再製作檢查腳本。
          </p>
        </div>
      ) : (
        <div className={styles.scriptsGrid}>
          <div className={styles.scriptList}>
            {scripts.map((script) => (
              <button
                key={script.id}
                type="button"
                className={`${styles.scriptItem} ${selected?.id === script.id ? styles.scriptItemActive : ""}`}
                onClick={() => selectScript(script.id)}
                disabled={actionPending !== null}
              >
                <span className={styles.scriptItemHead}>
                  <span className={styles.scriptName}>{script.name}</span>
                  <span className={`${styles.badge} ${scriptStatusBadgeClass(script.status)}`}>
                    {SCRIPT_STATUS_LABELS[script.status] ?? script.status}
                  </span>
                </span>
                <span className={styles.fileMeta}>
                  {getTemplateLabel(script.template_key)} · {formatDateTime(script.updated_at)}
                </span>
              </button>
            ))}
          </div>

          {selected && (
            <div className={styles.card}>
              <div className={styles.cardHead}>
                {selectedIsRenaming ? (
                  <form
                    className={styles.scriptRenameForm}
                    aria-label={`重新命名「${selected.name}」`}
                    onSubmit={handleRename}
                    onClick={(event) => event.stopPropagation()}
                  >
                    <MIcon name="security" size={18} />
                    <label className={styles.srOnly} htmlFor={`script-name-${selected.id}`}>
                      腳本名稱
                    </label>
                    <input
                      id={`script-name-${selected.id}`}
                      ref={renameInputRef}
                      className={`${styles.scriptRenameInput} ${renameInvalid ? styles.fieldInvalid : ""}`}
                      // eslint-disable-next-line jsx-a11y/no-autofocus
                      autoFocus
                      type="text"
                      value={renameName}
                      maxLength={255}
                      disabled={actionPending === "rename"}
                      aria-label={`重新命名「${selected.name}」`}
                      aria-invalid={renameInvalid}
                      aria-describedby={renameInvalid ? `script-name-error-${selected.id}` : undefined}
                      title="按 Enter 儲存，Esc 取消"
                      onChange={(event) => {
                        setRenameName(event.target.value);
                        setRenameInvalid(false);
                      }}
                      onKeyDown={(event) => {
                        if (event.isComposing) return;
                        if (event.key === "Enter") {
                          event.preventDefault();
                          event.currentTarget.form?.requestSubmit();
                          return;
                        }
                        if (event.key === "Escape") {
                          event.preventDefault();
                          cancelRename();
                        }
                      }}
                    />
                    {renameInvalid && (
                      <span id={`script-name-error-${selected.id}`} className={styles.scriptRenameError} role="alert">
                        請輸入腳本名稱。
                      </span>
                    )}
                  </form>
                ) : (
                  <h4 className={styles.cardTitle}>
                    <MIcon name="security" size={18} />
                    {selected.name}
                  </h4>
                )}
                <div className={styles.sectionActions}>
                  {selected.status === "reviewed" && (
                    <button
                      type="button"
                      className={styles.btnPrimary}
                      onClick={handleApprove}
                      disabled={actionPending !== null || selectedIsRenaming}
                    >
                      <MIcon name="check_circle" size={16} />
                      {actionPending === "approve" ? "核准中..." : "核准"}
                    </button>
                  )}
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    onClick={() => (selectedIsRenaming ? cancelRename() : openRename(selected))}
                    disabled={actionPending !== null}
                  >
                    {actionPending === "rename" ? (
                      <Spinner size={16} />
                    ) : (
                      <MIcon name={selectedIsRenaming ? "close" : "edit"} size={16} />
                    )}
                    {actionPending === "rename" ? "儲存中..." : selectedIsRenaming ? "取消" : "重新命名"}
                  </button>
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    onClick={() => setDeleteTarget(selected)}
                    disabled={actionPending !== null || selectedIsRenaming}
                  >
                    <MIcon name="delete" size={16} />
                    刪除腳本
                  </button>
                </div>
              </div>

              <div className={styles.reviewGrid}>
                <ReviewPanel title="規則檢查（靜態）" result={selected.policy_check_result_json} />
                <ReviewPanel title="AI 檢查" result={selected.ai_review_result_json} />
              </div>

              <RetrySummary script={selected} />

              <pre className={styles.codeBlock}>{selected.script_content}</pre>
            </div>
          )}
        </div>
      )}

      {deleteScriptDialog.open && (
        <ConfirmModal
          title="確認刪除檢查腳本？"
          description={`你即將永久刪除「${deleteScriptDialog.item.name}」。刪除後無法再查看或核准。`}
          closing={deleteScriptDialog.closing}
          onClose={() => {
            if (actionPending !== "delete") setDeleteTarget(null);
          }}
          actions={
            <>
              <button
                type="button"
                className={styles.btnSecondary}
                disabled={actionPending === "delete"}
                onClick={() => setDeleteTarget(null)}
              >
                取消
              </button>
              <button
                type="button"
                className={styles.btnDanger}
                disabled={actionPending === "delete"}
                onClick={handleDelete}
              >
                {actionPending === "delete" ? "刪除中..." : "確認刪除"}
              </button>
            </>
          }
        />
      )}

    </div>
  );
}

/* ── 執行結果／核查共用的結果顯示元件 ───────────────────── */

function runIsTerminal(status) {
  return status === "completed" || status === "failed" || status === "cancelled";
}

const RUN_STATUS = {
  completed: { label: "已完成", className: styles.badge_success },
  running: { label: "執行中", className: styles.badge_info },
  failed: { label: "失敗", className: styles.badge_danger },
  cancelled: { label: "已取消", className: styles.badge_muted },
  pending: { label: "等待中", className: styles.badge_muted },
};

function StatusBadge({ map, status }) {
  const info = map[status] ?? { label: status ?? "—", className: styles.badge_muted };
  return <span className={`${styles.badge} ${info.className}`}>{info.label}</span>;
}

const CHECK_STATUS_META = {
  pass: { icon: "check_circle", label: "通過", className: styles.checkIconPass },
  fail: { icon: "cancel", label: "未通過", className: styles.checkIconFail },
  warning: { icon: "warning", label: "需注意", className: styles.checkIconWarn },
  unknown: { icon: "help", label: "待導師核查", className: styles.checkIconWarn },
  collected: { icon: "visibility", label: "待導師核查", className: styles.checkIconWarn },
  skipped: { icon: "remove_circle_outline", label: "略過", className: styles.checkIconSkip },
};

function checkStatusMeta(status) {
  return CHECK_STATUS_META[status] ?? {
    icon: "help",
    label: "未判定",
    className: styles.checkIconSkip,
  };
}

function parseCheckRaw(raw) {
  if (typeof raw !== "string" || !raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed;
    }
  } catch {
    // raw 不一定是 JSON（契約允許普通字串），fallback 顯示原文
  }
  return null;
}

function ReturnCodeBadge({ returncode }) {
  if (returncode === null || returncode === undefined) {
    return <span className={`${styles.cmdBadge} ${styles.cmdBadgeError}`}>執行例外</span>;
  }
  const ok = returncode === 0;
  return (
    <span className={`${styles.cmdBadge} ${ok ? styles.cmdBadgeOk : styles.cmdBadgeError}`}>
      returncode {returncode}{ok ? " ✓" : " ✗"}
    </span>
  );
}

function CommandOutput({ label, text, isError = false }) {
  const content = typeof text === "string" ? text : String(text ?? "");
  if (!content) return null;
  return (
    <div className={styles.cmdBlock}>
      <span className={styles.cmdLabel}>{label}</span>
      <pre className={isError ? styles.cmdStderr : styles.cmdStdout}>{content}</pre>
    </div>
  );
}

function CommandLog({ raw, fallbackText }) {
  const parsed = parseCheckRaw(raw);
  if (!parsed) {
    if (!raw && !fallbackText) return null;
    return (
      <div className={styles.cmdLog}>
        {raw ? <pre className={styles.cmdStdout}>{raw}</pre> : null}
        {fallbackText ? <pre className={styles.cmdStderr}>{fallbackText}</pre> : null}
      </div>
    );
  }
  const empty = !parsed.stdout && !parsed.stderr && parsed.returncode == null;
  return (
    <div className={styles.cmdLog}>
      <div className={styles.cmdHead}>
        <span className={styles.cmdLabel}>指令輸出</span>
        <ReturnCodeBadge returncode={parsed.returncode} />
      </div>
      {empty ? <span className={styles.cmdEmpty}>（無輸出）</span> : null}
      <CommandOutput label="stdout" text={parsed.stdout} />
      <CommandOutput label="stderr" text={parsed.stderr} isError />
      {Array.isArray(parsed.errors) && parsed.errors.length > 0 && (
        <CommandOutput label="errors" text={parsed.errors.join("\n")} isError />
      )}
    </div>
  );
}

function CheckResultsTable({ checks }) {
  const [expanded, setExpanded] = useState(() => new Set());
  const toggle = (id) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  return (
    <div className={styles.checkTable}>
      <div className={`${styles.checkRow} ${styles.checkRowHead}`}>
        <span className={styles.checkToggleCol} />
        <span className={styles.checkIconCol} />
        <span>檢查項目</span>
        <span>摘要</span>
      </div>
      {checks.map((check, index) => {
        const id = check?.id ?? `check-${index}`;
        const meta = checkStatusMeta(check?.status);
        const detailId = `${id}-${index}`;
        const hasDetail = Boolean(
          check?.evidence || check?.raw || (Array.isArray(check?.errors) && check.errors.length),
        );
        const isOpen = hasDetail && expanded.has(detailId);
        return (
          <div key={detailId} className={styles.checkItem}>
            <button
              type="button"
              className={`${styles.checkRow} ${styles.checkRowBtn}`}
              onClick={() => hasDetail && toggle(detailId)}
              disabled={!hasDetail}
              aria-expanded={hasDetail ? isOpen : undefined}
            >
              <span className={styles.checkToggleCol}>
                {hasDetail && (
                  <MIcon name={isOpen ? "expand_less" : "expand_more"} size={16} />
                )}
              </span>
              <span className={`${styles.checkIconCol} ${meta.className}`}>
                <MIcon name={meta.icon} size={16} />
              </span>
              <span className={styles.checkTitle}>{check?.title ?? check?.id ?? "收集項目"}</span>
              <span className={styles.checkEvidence}>{check?.evidence || "—"}</span>
            </button>
            {isOpen && (
              <div className={styles.checkDetail}>
                {check?.evidence && <p>{check.evidence}</p>}
                <CommandLog
                  raw={check?.raw}
                  fallbackText={Array.isArray(check?.errors) ? check.errors.join("\n") : ""}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ── Tab 2：導師核查 ────────────────────────────────────── */

const TEACHER_REVIEW_STATUSES = new Set(["warning", "unknown", "collected"]);

function targetChecks(target) {
  const checks = target?.parsed_result?.checks;
  return Array.isArray(checks) ? checks : [];
}

function targetTeacherReview(target) {
  const review = target?.teacher_review;
  return review && typeof review === "object"
    ? {
        feedback: typeof review.feedback === "string" ? review.feedback : "",
        decisions: review.decisions && typeof review.decisions === "object"
          ? review.decisions
          : {},
      }
    : { feedback: "", decisions: {} };
}

export function getTargetReviewSummary(target) {
  if (!target) return { kind: "missing", label: "尚未執行", pending: 0, reviewable: 0 };
  if (target.status === "failed" || target.validation?.valid === false) {
    return { kind: "failed", label: "執行失敗", pending: 0, reviewable: 0 };
  }
  const reviewable = targetChecks(target).filter((check) => (
    TEACHER_REVIEW_STATUSES.has(check?.status)
  ));
  const decisions = targetTeacherReview(target).decisions;
  const pending = reviewable.filter((check) => !decisions[check?.id]).length;
  if (pending > 0) {
    return {
      kind: "pending",
      label: `待核查 ${pending} 項`,
      pending,
      reviewable: reviewable.length,
    };
  }
  if (reviewable.length > 0) {
    return { kind: "reviewed", label: "核查完成", pending: 0, reviewable: reviewable.length };
  }
  if (targetTeacherReview(target).feedback) {
    return { kind: "reviewed", label: "已留言", pending: 0, reviewable: 0 };
  }
  return { kind: "automatic", label: "AI 已判定", pending: 0, reviewable: 0 };
}

function reviewDraft(target) {
  const review = targetTeacherReview(target);
  return { feedback: review.feedback, decisions: { ...review.decisions } };
}

function reviewBadgeClass(kind) {
  if (kind === "pending") return styles.badge_info;
  if (kind === "failed") return styles.badge_danger;
  if (kind === "reviewed") return styles.badge_success;
  return styles.badge_muted;
}

function ReviewCheckRow({ check, decision, onDecide }) {
  const meta = checkStatusMeta(check?.status);
  const reviewable = TEACHER_REVIEW_STATUSES.has(check?.status);
  return (
    <div className={styles.reviewCheck}>
      <span className={`${styles.reviewCheckIcon} ${meta.className}`}><MIcon name={meta.icon} size={17} /></span>
      <div className={styles.reviewCheckContent}>
        <div><strong>{check?.title ?? check?.id ?? "收集項目"}</strong><span>{meta.label}</span></div>
        <p>{check?.evidence || "沒有摘要"}</p>
        {(check?.raw || (Array.isArray(check?.errors) && check.errors.length > 0)) && (
          <details><summary>查看原始證據</summary><CommandLog raw={check?.raw} fallbackText={(check?.errors ?? []).join("\n")} /></details>
        )}
      </div>
      {reviewable && (
        <div className={styles.reviewDecision} aria-label={`${check?.title ?? check?.id}人工判定`}>
          <button type="button" className={decision === "pass" ? styles.reviewPassActive : ""} aria-pressed={decision === "pass"} onClick={() => onDecide("pass")}><MIcon name="check" size={16} />通過</button>
          <button type="button" className={decision === "fail" ? styles.reviewFailActive : ""} aria-pressed={decision === "fail"} onClick={() => onDecide("fail")}><MIcon name="close" size={16} />未通過</button>
        </div>
      )}
    </div>
  );
}

function reviewRowUser(row) {
  return row?.target?.user ?? row?.member ?? {};
}

function reviewStudentNumber(row) {
  const user = reviewRowUser(row);
  const email = String(user.email ?? "");
  return String(
    user.student_number
    ?? user.student_no
    ?? user.account
    ?? email.split("@")[0]
    ?? "",
  );
}

export function sortTeacherReviewRows(rows, sortMode = "pending") {
  const collator = new Intl.Collator("zh-Hant", { numeric: true, sensitivity: "base" });
  const byAccount = (left, right) => collator.compare(
    reviewStudentNumber(left),
    reviewStudentNumber(right),
  );

  return [...rows].sort((left, right) => {
    if (sortMode === "student-number") return byAccount(left, right);

    const rank = { pending: 0, failed: 1, reviewed: 2, automatic: 3, missing: 4 };
    const statusDelta = rank[getTargetReviewSummary(left.target).kind]
      - rank[getTargetReviewSummary(right.target).kind];
    return statusDelta || byAccount(left, right);
  });
}

/* ── 以學生為單位的執行總覽（導師核查） ──
 * v1 為純前端聚合：輸入沿用 buildBatchReviewRows / buildLegacyReviewRows
 * 產出的 per-machine rows，按 studentId 分組；儲存仍以 machine row.key
 *（runId + vmid / student_id|node_key|vmid）為單位，不新增後端 API。
 */

function studentGroupKey(row, index = 0) {
  const id = row?.studentId ?? row?.member?.user_id ?? row?.member?.email ?? row?.target?.user?.id;
  if (id !== undefined && id !== null && String(id).trim() !== "") return String(id);
  return `__row-${index}`;
}

function studentDisplayUser(machines) {
  for (const row of machines) {
    const user = reviewRowUser(row);
    if (user?.full_name || user?.email) return user;
  }
  return machines[0]?.member ?? {};
}

/** 學生層 check 有效計數：已存導師判定覆蓋 raw status，未判定才計待確認。 */
export function aggregateStudentCheckTotals(machines) {
  const totals = { pass: 0, fail: 0, pending: 0, skipped: 0, total: 0 };
  for (const row of Array.isArray(machines) ? machines : []) {
    const savedDecisions = targetTeacherReview(row?.target).decisions ?? {};
    for (const check of targetChecks(row?.target)) {
      const decision = savedDecisions[check?.id];
      if (decision === "pass") {
        totals.pass += 1;
        totals.total += 1;
        continue;
      }
      if (decision === "fail") {
        totals.fail += 1;
        totals.total += 1;
        continue;
      }
      const status = check?.status;
      if (status === "pass") totals.pass += 1;
      else if (status === "fail") totals.fail += 1;
      else if (TEACHER_REVIEW_STATUSES.has(status)) totals.pending += 1;
      else if (status === "skipped") totals.skipped += 1;
      totals.total += 1;
    }
  }
  return totals;
}

/** 學生層狀態：取所屬機器中最緊急者（pending > failed > reviewed > automatic > missing）。 */
export function getStudentOverviewStatus(machines) {
  const list = Array.isArray(machines) ? machines : [];
  if (list.length === 0) return { kind: "missing", label: "尚未執行", pending: 0, reviewable: 0 };
  const summaries = list.map((row) => getTargetReviewSummary(row?.target));
  const pending = summaries.reduce((sum, item) => sum + (item.pending || 0), 0);
  const reviewable = summaries.reduce((sum, item) => sum + (item.reviewable || 0), 0);
  const kinds = new Set(summaries.map((item) => item.kind));
  if (kinds.has("pending")) return { kind: "pending", label: `待確認 ${pending} 項`, pending, reviewable };
  if (kinds.has("failed")) return { kind: "failed", label: "部分機器執行失敗", pending: 0, reviewable };
  if (kinds.has("reviewed") && ![...kinds].some((kind) => kind === "automatic" || kind === "missing")) {
    return { kind: "reviewed", label: "核查完成", pending: 0, reviewable };
  }
  if (kinds.has("reviewed")) return { kind: "reviewed", label: "核查完成", pending: 0, reviewable };
  if (kinds.has("automatic") && kinds.size === 1) return { kind: "automatic", label: "AI 已判定", pending: 0, reviewable };
  if (kinds.has("missing") && kinds.size === 1) return { kind: "missing", label: "尚未執行", pending: 0, reviewable };
  return { kind: "automatic", label: "AI 已判定", pending: 0, reviewable };
}

export function buildStudentOverviewRows(machineRows) {
  const groups = new Map();
  (Array.isArray(machineRows) ? machineRows : []).forEach((row, index) => {
    const key = studentGroupKey(row, index);
    if (!groups.has(key)) groups.set(key, { studentId: key, machines: [] });
    groups.get(key).machines.push(row);
  });
  return [...groups.values()].map((group) => {
    const user = studentDisplayUser(group.machines);
    const totals = aggregateStudentCheckTotals(group.machines);
    const status = getStudentOverviewStatus(group.machines);
    return { ...group, user, totals, status };
  });
}

function studentOverviewNumber(student) {
  const user = student?.user ?? {};
  const email = String(user.email ?? "");
  return String(
    user.student_number
    ?? user.student_no
    ?? user.account
    ?? email.split("@")[0]
    ?? student?.studentId
    ?? "",
  );
}

export function sortStudentOverviewRows(students, sortMode = "pending") {
  const collator = new Intl.Collator("zh-Hant", { numeric: true, sensitivity: "base" });
  const byAccount = (left, right) => collator.compare(
    studentOverviewNumber(left),
    studentOverviewNumber(right),
  );
  return [...students].sort((left, right) => {
    if (sortMode === "student-number") return byAccount(left, right);
    const rank = { pending: 0, failed: 1, reviewed: 2, automatic: 3, missing: 4 };
    const statusDelta = (rank[left?.status?.kind] ?? 9) - (rank[right?.status?.kind] ?? 9);
    if (statusDelta !== 0) return statusDelta;
    const pendingDelta = (right?.status?.pending ?? 0) - (left?.status?.pending ?? 0);
    return pendingDelta || byAccount(left, right);
  });
}

/* ── 檢查點腳本集（run batch）投影 → 核查資料 ── */

function machineNodeReference(nodeKey, machineNodes = []) {
  const key = String(nodeKey ?? "").trim();
  const node = Array.isArray(machineNodes)
    ? machineNodes.find((entry) => String(entry?.node_key ?? "").trim() === key)
    : null;
  const sortOrder = Number(node?.sort_order);
  const displayLabel = node?.display_label
    ?? (Number.isFinite(sortOrder) ? `P${sortOrder + 1}` : null);
  const name = node?.name ?? node?.node_name ?? null;
  return {
    key,
    display: [displayLabel, name].filter(Boolean).join(" · ") || key || "未指定節點",
    displayLabel,
    name,
  };
}

function batchMachineDisplayName(nodeKey, machineNodes = [], fallbackLabel = null) {
  const reference = machineNodeReference(nodeKey, machineNodes);
  if (reference.displayLabel) {
    return reference.name
      ? `${reference.displayLabel} · ${reference.name}`
      : reference.displayLabel;
  }
  return fallbackLabel ?? null;
}

function batchNodeItems(node) {
  return Array.isArray(node?.items) ? node.items : [];
}

function batchItemChecks(item) {
  return Array.isArray(item?.checks) ? item.checks : [];
}

function batchNodeChecks(node) {
  const checks = [];
  for (const item of batchNodeItems(node)) {
    checks.push(...batchItemChecks(item));
  }
  return checks;
}

/** 把批次投影 node 轉成與 legacy run target 相容的核查顯示物件。 */
export function buildBatchReviewTarget(node, member = null) {
  const checks = batchNodeChecks(node);
  return {
    vmid: node?.vmid ?? member?.vmid ?? null,
    status: node?.execution_status,
    reason_code: node?.reason_code ?? null,
    user: member ?? {},
    teacher_review: node?.teacher_review ?? undefined,
    parsed_result: { checks },
  };
}

export function mergeNodeTeacherReview(batch, row, teacherReview) {
  const students = Array.isArray(batch?.students) ? batch.students : [];
  return {
    ...batch,
    students: students.map((student) => {
      if (String(student?.student_id ?? "") !== String(row.studentId ?? "")) return student;
      return {
        ...student,
        nodes: (Array.isArray(student.nodes) ? student.nodes : []).map((node) => (
          String(node?.node_key ?? "") === String(row.nodeKey ?? "")
            ? { ...node, teacher_review: teacherReview ?? undefined }
            : node
        )),
      };
    }),
  };
}

function batchRowKey(studentId, nodeKey, vmid) {
  return `${String(studentId ?? "")}|${String(nodeKey ?? "")}|${String(vmid ?? "x")}`;
}

function draftsFromBatch(batch) {
  const nextDrafts = {};
  for (const student of Array.isArray(batch?.students) ? batch.students : []) {
    for (const node of Array.isArray(student?.nodes) ? student.nodes : []) {
      nextDrafts[batchRowKey(student?.student_id, node?.node_key, node?.vmid)] =
        reviewDraftFromTeacherReview(node?.teacher_review);
    }
  }
  return nextDrafts;
}

function reviewDraftFromTeacherReview(review) {
  return {
    feedback: typeof review?.feedback === "string" ? review.feedback : "",
    decisions: review?.decisions && typeof review.decisions === "object"
      ? { ...review.decisions }
      : {},
  };
}

export function buildBatchReviewRows(batch, members = []) {
  const memberByVmidNode = new Map();
  const memberByVmid = new Map();
  for (const member of Array.isArray(members) ? members : []) {
    if (member?.vmid == null) continue;
    const vmid = String(member.vmid);
    memberByVmidNode.set(`${vmid}|${String(member?.node_key ?? "")}`, member);
    if (!memberByVmid.has(vmid)) memberByVmid.set(vmid, member);
  }
  const rows = [];
  for (const student of Array.isArray(batch?.students) ? batch.students : []) {
    for (const node of Array.isArray(student?.nodes) ? student.nodes : []) {
      const nodeKey = String(node?.node_key ?? "");
      const member = memberByVmidNode.get(`${String(node?.vmid ?? "")}|${nodeKey}`)
        ?? memberByVmid.get(String(node?.vmid ?? ""))
        ?? null;
      const vmid = node?.vmid ?? member?.vmid ?? null;
      rows.push({
        key: batchRowKey(student?.student_id, nodeKey, vmid),
        member: member ?? { user_id: student?.student_id ?? null },
        target: buildBatchReviewTarget(node, member),
        node,
        runId: node?.run_id ?? null,
        vmid,
        studentId: student?.student_id ?? null,
        nodeKey,
        items: batchNodeItems(node),
        unmappedChecks: Array.isArray(node?.unmapped_checks) ? node.unmapped_checks : [],
      });
    }
  }
  return rows;
}

export function buildLegacyReviewRows(run, members = []) {
  const targets = run?.target_results_json?.targets ?? [];
  const targetsByVmid = new Map(targets.map((target) => [String(target.vmid), target]));
  const matchedVmids = new Set();
  const memberRows = (Array.isArray(members) ? members : []).map((member) => {
    const target = targetsByVmid.get(String(member.vmid));
    if (target) matchedVmids.add(String(target.vmid));
    return {
      key: String(member.vmid ?? member.user_id ?? member.email),
      member,
      target: target ?? null,
      runId: run?.id ?? null,
      vmid: member.vmid ?? target?.vmid ?? null,
      items: null,
    };
  });
  const unmatched = targets
    .filter((target) => !matchedVmids.has(String(target.vmid)))
    .map((target) => ({
      key: String(target.vmid ?? target.user?.user_id ?? target.user?.email),
      member: target.user ?? {},
      target,
      runId: run?.id ?? null,
      vmid: target.vmid ?? null,
      items: null,
    }));
  return [...memberRows, ...unmatched];
}

/* 單台機器的核查內容（檢查點 → checks、回饋、儲存）；由學生總覽展開後逐台渲染。 */
function MachineReviewDetail({
  row,
  machineNodes = [],
  draft,
  saved,
  isDirty,
  saving,
  onToggleDecision,
  onUpdateFeedback,
  onSave,
}) {
  const { target } = row;
  const checks = targetChecks(target);
  const nodeLabel = row.nodeKey
    ? batchMachineDisplayName(row.nodeKey, machineNodes, row.node?.display_label) ?? row.nodeKey
    : null;
  if (!target) {
    return <div className={styles.reviewNoResult}>這台機器不在最近一次執行範圍內，尚無 AI 檢查結果。</div>;
  }
  return (
    <>
      <div className={styles.reviewMachineHead}>
        <strong>{nodeLabel ?? `VMID ${row.vmid ?? "—"}`}</strong>
        {row.vmid ? <small>{`VMID ${row.vmid}`}</small> : null}
        <span className={`${styles.badge} ${reviewBadgeClass(getTargetReviewSummary(target).kind)}`}>
          {getTargetReviewSummary(target).label}
        </span>
      </div>
      {row.items ? (
        <div className={styles.reviewCheckList}>
          {row.items.length === 0 && (
            <p className={styles.mutedText}>此機器沒有回傳可顯示的檢查點。</p>
          )}
          {row.items.map((item, itemIndex) => {
            const itemMeta = checkStatusMeta(item?.status);
            const itemChecks = Array.isArray(item?.checks) ? item.checks : [];
            const peerText = item?.peer_node_key
              ? `觀察 ${item?.peer_display_label ?? item.peer_node_key}`
              : null;
            return (
              <div
                className={styles.reviewCheckGroup}
                key={`${row.key}-item-${item?.rubric_item_id ?? itemIndex}`}
              >
                <div className={styles.reviewCheckGroupHead}>
                  <span className={`${styles.reviewCheckIcon} ${itemMeta.className}`}>
                    <MIcon name={itemMeta.icon} size={17} />
                  </span>
                  <div className={styles.reviewCheckGroupTitle}>
                    <strong>{item?.title ?? item?.rubric_item_id ?? "檢查點"}</strong>
                    <span>{itemMeta.label}{peerText ? ` · ${peerText}` : ""}</span>
                  </div>
                </div>
                {itemChecks.length === 0 ? (
                  <p className={styles.mutedText}>
                    {item?.reason_code === "peer_unavailable"
                      ? "對應的受控機器目前無法連線，此檢查點暫時無法自動判定。"
                      : "此檢查點沒有回傳可顯示的結果。"}
                  </p>
                ) : itemChecks.map((check, checkIndex) => (
                  <ReviewCheckRow
                    key={`${item?.rubric_item_id ?? "item"}-${check?.id ?? "check"}-${checkIndex}`}
                    check={check}
                    decision={draft.decisions[check?.id]}
                    onDecide={(value) => onToggleDecision(row.key, check?.id, value)}
                  />
                ))}
              </div>
            );
          })}
          {(row.unmappedChecks ?? []).length > 0 && (
            <details className={styles.judgeDetails}>
              <summary>未對應檢查點</summary>
              <CheckResultsTable checks={row.unmappedChecks} />
            </details>
          )}
        </div>
      ) : (
        <div className={styles.reviewCheckList}>
          {checks.length === 0 ? <p className={styles.mutedText}>腳本沒有回傳可顯示的檢查項目。</p> : checks.map((check, index) => (
            <ReviewCheckRow
              key={`${check?.id ?? "check"}-${index}`}
              check={check}
              decision={draft.decisions[check?.id]}
              onDecide={(value) => onToggleDecision(row.key, check?.id, value)}
            />
          ))}
        </div>
      )}

      <label className={styles.reviewFeedbackField}>
        <span>給學生的本週回饋 <small>選填</small></span>
        <textarea
          value={draft.feedback}
          maxLength={4000}
          rows={3}
          placeholder="例如：服務已能啟動，接下來請補上錯誤處理並重新確認日誌。"
          onChange={(event) => onUpdateFeedback(row.key, event.target.value)}
        />
        <small>{draft.feedback.length} / 4000</small>
      </label>
      <div className={styles.reviewSaveRow}>
        <span>{isDirty ? "有尚未儲存的變更" : saved.feedback || Object.keys(saved.decisions).length ? `上次儲存：${target.teacher_review?.updated_at ? formatDateTime(target.teacher_review.updated_at) : "已儲存"}` : "可只判定、不留言；也可以只留言。"}</span>
        <button type="button" className={styles.btnPrimary} disabled={!isDirty || saving} onClick={() => onSave(row)}>{saving ? <><Spinner size={15} />儲存中…</> : <><MIcon name="save" size={16} />儲存核查</>}</button>
      </div>
    </>
  );
}

export function TeacherReviewTab({ classId, sessionId, members, machineNodes = [] }) {
  const toast = useToast();
  const [reviewState, setReviewState] = useState(null); // { mode: "batch", batch } | { mode: "run", run }
  const [scriptSets, setScriptSets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [expandedKey, setExpandedKey] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [savingKey, setSavingKey] = useState(null);
  const [sortMode, setSortMode] = useState("pending");
  const [runOnceOpen, setRunOnceOpen] = useState(false);
  const [creatingBatch, setCreatingBatch] = useState(false);
  const [activeBatch, setActiveBatch] = useState(null);
  const [selectedSetId, setSelectedSetId] = useState(null);
  const loadRequestRef = useRef(0);
  const runOnceDialog = useDialogPresence(runOnceOpen);
  const batchStatusRef = useRef(null);

  const loadReview = useCallback(async () => {
    const requestId = ++loadRequestRef.current;
    setLoading(true);
    setLoadError("");
    try {
      const [sets, runs] = await Promise.all([
        AiJudgeService.listSessionScriptSets(classId, sessionId).catch(() => []),
        AiJudgeService.listSessionRuns(classId, sessionId),
      ]);
      if (loadRequestRef.current !== requestId) return;
      setScriptSets(Array.isArray(sets) ? sets : []);
      const orderedRuns = Array.isArray(runs) ? runs : [];
      const latest = orderedRuns[0] ?? null;
      if (latest?.run_batch_id) {
        const batch = await AiJudgeService.getSessionRunBatch(
          classId,
          sessionId,
          latest.run_batch_id,
        );
        if (loadRequestRef.current !== requestId) return;
        setReviewState({ mode: "batch", batch });
        setDrafts(draftsFromBatch(batch));
        return;
      }
      const preferred = orderedRuns.find((item) => item.status === "completed") ?? latest;
      if (!preferred) {
        setReviewState(null);
        setDrafts({});
        return;
      }
      const detail = await AiJudgeService.getSessionRun(classId, sessionId, preferred.id);
      if (loadRequestRef.current !== requestId) return;
      setReviewState({ mode: "run", run: detail });
      const nextDrafts = {};
      for (const target of detail?.target_results_json?.targets ?? []) {
        nextDrafts[String(target.vmid)] = reviewDraft(target);
      }
      setDrafts(nextDrafts);
    } catch (error) {
      if (loadRequestRef.current === requestId) {
        setLoadError(error?.message ?? "無法載入導師核查資料。");
      }
    } finally {
      if (loadRequestRef.current === requestId) setLoading(false);
    }
  }, [classId, sessionId]);

  useEffect(() => {
    setReviewState(null);
    setScriptSets([]);
    setExpandedKey(null);
    setActiveBatch(null);
    setSelectedSetId(null);
    batchStatusRef.current = null;
    loadReview();
    return () => {
      loadRequestRef.current += 1;
    };
  }, [loadReview]);

  /* 「一次執行」輪詢：整批到終態後重新載入核查資料 */
  useEffect(() => {
    if (!activeBatch?.run_batch_id || runIsTerminal(activeBatch.status)) return undefined;
    let cancelled = false;
    let timer = null;

    async function poll() {
      try {
        const next = await AiJudgeService.getSessionRunBatch(
          classId,
          sessionId,
          activeBatch.run_batch_id,
        );
        if (cancelled) return;
        setActiveBatch(next);
        if (!runIsTerminal(next.status)) timer = setTimeout(poll, 2000);
      } catch {
        if (!cancelled) timer = setTimeout(poll, 5000);
      }
    }

    timer = setTimeout(poll, 1500);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [classId, sessionId, activeBatch?.run_batch_id, activeBatch?.status]);

  useEffect(() => {
    const previous = batchStatusRef.current;
    batchStatusRef.current = activeBatch?.status ?? null;
    if (
      activeBatch
      && previous
      && previous !== activeBatch.status
      && runIsTerminal(activeBatch.status)
    ) {
      setActiveBatch(null);
      loadReview();
    }
  }, [activeBatch, loadReview]);

  const approvedSets = useMemo(
    () => scriptSets.filter((set) => set?.status === "approved"),
    [scriptSets],
  );
  const selectedSet = useMemo(
    () => approvedSets.find((set) => set.artifact_set_id === selectedSetId)
      ?? approvedSets[0]
      ?? null,
    [approvedSets, selectedSetId],
  );
  const batchRunning = Boolean(activeBatch && !runIsTerminal(activeBatch.status));

  const machineRows = useMemo(() => {
    if (!reviewState) return [];
    return reviewState.mode === "batch"
      ? buildBatchReviewRows(reviewState.batch, members)
      : buildLegacyReviewRows(reviewState.run, members);
  }, [reviewState, members]);

  /* 以學生為單位的總覽：machine rows 按 studentId 分組，排序以學生狀態為準。 */
  const studentRows = useMemo(
    () => sortStudentOverviewRows(buildStudentOverviewRows(machineRows), sortMode),
    [machineRows, sortMode],
  );

  const summary = useMemo(() => studentRows.reduce((counts, student) => {
    counts.total += 1;
    if (student.status.kind === "pending") counts.pending += 1;
    if (student.status.kind === "reviewed") counts.reviewed += 1;
    if (student.status.kind === "automatic") counts.automatic += 1;
    if (student.status.kind === "missing" || student.status.kind === "failed") counts.unavailable += 1;
    return counts;
  }, { total: 0, pending: 0, reviewed: 0, automatic: 0, unavailable: 0 }), [studentRows]);

  function updateDraft(key, updater) {
    setDrafts((current) => ({
      ...current,
      [key]: updater(current[key] ?? { feedback: "", decisions: {} }),
    }));
  }

  function toggleDecision(key, checkId, decision) {
    updateDraft(key, (current) => {
      const decisions = { ...current.decisions };
      if (decisions[checkId] === decision) delete decisions[checkId];
      else decisions[checkId] = decision;
      return { ...current, decisions };
    });
  }

  async function saveRow(row) {
    const draft = drafts[row.key] ?? reviewDraft(row.target);
    setSavingKey(row.key);
    try {
      const updated = await AiJudgeService.updateTargetReview(
        classId,
        sessionId,
        row.runId,
        row.vmid,
        draft,
      );
      const savedTarget = (updated?.target_results_json?.targets ?? [])
        .find((item) => String(item?.vmid) === String(row.vmid));
      if (reviewState?.mode === "batch") {
        const savedReview = savedTarget?.teacher_review;
        setReviewState((current) => (
          current?.mode === "batch"
            ? { mode: "batch", batch: mergeNodeTeacherReview(current.batch, row, savedReview) }
            : current
        ));
        setDrafts((current) => ({
          ...current,
          [row.key]: reviewDraftFromTeacherReview(savedReview),
        }));
      } else if (reviewState?.mode === "run") {
        setReviewState({ mode: "run", run: updated });
        setDrafts((current) => ({ ...current, [row.key]: reviewDraft(savedTarget) }));
      }
      toast.success("導師核查已儲存。");
    } catch (error) {
      toast.error(error?.message ?? "導師核查儲存失敗。");
    } finally {
      setSavingKey(null);
    }
  }

  async function handleRunOnce() {
    if (!selectedSet || creatingBatch || batchRunning) return;
    setCreatingBatch(true);
    try {
      const next = await AiJudgeService.createSessionScriptSetRun(
        classId,
        sessionId,
        selectedSet.artifact_set_id,
      );
      batchStatusRef.current = next?.status ?? null;
      setActiveBatch(next);
      setRunOnceOpen(false);
      setSelectedSetId(null);
      toast.success(`已建立整批執行（${next?.summary?.targets ?? 0} 台機器）`);
    } catch (error) {
      toast.error(error?.message ?? "建立整批執行失敗");
    } finally {
      setCreatingBatch(false);
    }
  }

  function renderRunOnceDialog() {
    if (!runOnceDialog.open) return null;
    const children = Array.isArray(selectedSet?.children) ? selectedSet.children : [];
    return (
      <div
        className={`${styles.modalOverlay} ${runOnceDialog.closing ? styles.modalOverlayOut : ""}`}
        onMouseDown={() => setRunOnceOpen(false)}
      >
        <div className={styles.modal} onMouseDown={(event) => event.stopPropagation()}>
          <div className={styles.modalHeader}>
            <div>
              <h2>一次執行整組檢查點</h2>
              <p>後端會把每個檢查點腳本送到每位學生對應的邏輯機器；目標機器必須正在運行且已登記 SSH 金鑰。</p>
            </div>
            <button
              type="button"
              className={styles.dialogClose}
              onClick={() => setRunOnceOpen(false)}
              aria-label="關閉"
            >
              <MIcon name="close" size={18} />
            </button>
          </div>

          {approvedSets.length > 1 && (
            <label className={styles.field}>
              <span>選擇腳本集</span>
              <select
                value={selectedSet?.artifact_set_id ?? ""}
                onChange={(event) => setSelectedSetId(event.target.value)}
              >
                {approvedSets.map((set) => (
                  <option key={set.artifact_set_id} value={set.artifact_set_id}>
                    revision {set.source_analysis_revision ?? "—"} · {set.children?.length ?? 0} 台機器
                  </option>
                ))}
              </select>
            </label>
          )}

          <div className={styles.vmidBox}>
            <span className={styles.fieldLabel}>執行範圍（{children.length} 個邏輯機器）</span>
            <div className={styles.chipRow}>
              {children.map((child) => (
                <span key={child.id} className={styles.chip}>
                  {batchMachineDisplayName(child.target_node_key, machineNodes, child.name) ?? "未指定節點"}
                </span>
              ))}
            </div>
          </div>

          <div className={styles.modalActions}>
            <button
              type="button"
              className={styles.btnSecondary}
              onClick={() => setRunOnceOpen(false)}
              disabled={creatingBatch}
            >
              取消
            </button>
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={handleRunOnce}
              disabled={creatingBatch || !selectedSet}
            >
              {creatingBatch ? "建立中..." : "確認執行"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (loading) return <LoadingState text="正在整理學生檢查結果…" />;
  if (loadError) return <div className={styles.noticeDanger}>{loadError}</div>;
  if (!reviewState) {
    return (
      <div className={styles.tabBody}>
        <div className={`${styles.card} ${styles.reviewEmpty}`}>
          <MIcon name="rate_review" size={30} />
          <h4>還沒有可核查的結果</h4>
          {approvedSets.length > 0 ? (
            <>
              <p>整組檢查點腳本已就緒；可以立即一次執行，後端會把每份腳本分散到每位學生的對應機器。</p>
              <button
                type="button"
                className={styles.btnPrimary}
                onClick={() => setRunOnceOpen(true)}
                disabled={creatingBatch}
              >
                {creatingBatch ? <Spinner size={15} /> : <MIcon name="bolt" size={16} />}
                一次執行整組檢查點
              </button>
            </>
          ) : (
            <p>尚未建立可執行的檢查腳本集。請先在「檢查設定」製作腳本並通過審查。</p>
          )}
        </div>
        {renderRunOnceDialog()}
      </div>
    );
  }

  return (
    <div className={styles.tabBody}>
      <div className={`${styles.card} ${styles.reviewOverview}`}>
        <div className={styles.reviewOverviewHead}>
          <h4 className={styles.cardTitle}><MIcon name="rate_review" size={19} />導師核查</h4>
          <div className={styles.sectionActions}>
            {batchRunning && (
              <span className={styles.mutedText}>
                <Spinner size={14} />
                執行中 {activeBatch.summary?.completed ?? 0} / {activeBatch.summary?.targets ?? 0} 台…
              </span>
            )}
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={() => setRunOnceOpen(true)}
              disabled={creatingBatch || batchRunning || approvedSets.length === 0}
              title={approvedSets.length === 0
                ? "此檢查還沒有已通過審查的腳本集"
                : "把整組檢查點腳本分散到對應機器執行"}
            >
              {creatingBatch ? <Spinner size={15} /> : <MIcon name="bolt" size={16} />}
              一次執行
            </button>
          </div>
        </div>
        <div className={styles.reviewMetrics} aria-label="核查進度">
          <span><strong>{summary.pending}</strong><small>待核查</small></span>
          <span><strong>{summary.reviewed}</strong><small>已核查／留言</small></span>
          <span><strong>{summary.automatic}</strong><small>AI 已判定</small></span>
          <span><strong>{summary.total}</strong><small>學生總數</small></span>
        </div>
        {activeBatch && (
          <div className={styles.runOnceProgress} aria-live="polite">
            {(activeBatch.nodes ?? []).map((node) => (
              <div className={styles.runOnceProgressLine} key={node.run_id}>
                <span>{batchMachineDisplayName(node.target_node_key, machineNodes, node.display_label) ?? "—"}</span>
                <StatusBadge map={RUN_STATUS} status={node.status} />
                <small>完成 {node.progress_json?.done ?? 0} / {node.progress_json?.total ?? 0}</small>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className={styles.reviewListSection}>
        <div className={styles.reviewListToolbar}>
          <label className={styles.reviewSort}>
            <MIcon name="sort" size={16} />
            <span>排序</span>
            <select value={sortMode} onChange={(event) => setSortMode(event.target.value)}>
              <option value="pending">待處理優先</option>
              <option value="student-number">學號／帳號</option>
            </select>
          </label>
        </div>
        <div className={styles.reviewStudentList}>
          {studentRows.map((student) => {
          const user = student.user ?? {};
          const isOpen = expandedKey === student.studentId;
          const { totals } = student;
          return (
            <article className={`${styles.reviewStudent} ${isOpen ? styles.reviewStudentOpen : ""}`} key={student.studentId}>
              <button
                type="button"
                className={styles.reviewStudentToggle}
                onClick={() => setExpandedKey(isOpen ? null : student.studentId)}
                aria-expanded={isOpen}
              >
                <span className={styles.reviewStudentIdentity}>
                  <span className={styles.reviewAvatar}><MIcon name="person" size={18} /></span>
                  <span>
                    <strong>{user.full_name ?? "未命名學生"}</strong>
                    <small>
                      {user.email ?? ""}
                      {` · ${student.machines.length} 台機器`}
                    </small>
                  </span>
                </span>
                <span className={styles.reviewAiCounts} aria-label="學生執行總覽">
                  <><em className={styles.reviewCountPass}>{totals.pass} 通過</em><em className={styles.reviewCountFail}>{totals.fail} 未通過</em><em className={styles.reviewCountPending}>{totals.pending} 待確認</em></>
                </span>
                <span className={`${styles.badge} ${reviewBadgeClass(student.status.kind)}`}>{student.status.label}</span>
                <MIcon name={isOpen ? "expand_less" : "expand_more"} size={20} />
              </button>

              {isOpen && (
                <div className={styles.reviewStudentBody}>
                  {student.machines.map((row) => {
                    const draft = drafts[row.key] ?? reviewDraft(row.target);
                    const saved = targetTeacherReview(row.target);
                    const isDirty = Boolean(row.target) && (
                      draft.feedback !== saved.feedback
                      || JSON.stringify(draft.decisions) !== JSON.stringify(saved.decisions)
                    );
                    return (
                      <section className={styles.reviewMachineCard} key={row.key} aria-label={row.nodeKey ?? `VMID ${row.vmid ?? ""}`}>
                        <MachineReviewDetail
                          row={row}
                          machineNodes={machineNodes}
                          draft={draft}
                          saved={saved}
                          isDirty={isDirty}
                          saving={savingKey === row.key}
                          onToggleDecision={toggleDecision}
                          onUpdateFeedback={(key, feedback) => updateDraft(key, (current) => ({ ...current, feedback }))}
                          onSave={saveRow}
                        />
                      </section>
                    );
                  })}
                </div>
              )}
            </article>
          );
          })}
        </div>
      </div>
      {renderRunOnceDialog()}
    </div>
  );
}

/* ── 導師工作區 ─────────────────────────────────────────── */

const TEACHER_JUDGE_TABS = [
  { key: "rubrics", label: "檢查設定", icon: "description" },
  { key: "review", label: "導師核查", icon: "rate_review" },
  { key: "scripts", label: "腳本總覽", icon: "terminal" },
];

function TeacherWorkspacePanel({ classId, members, weeks = [], machineNodes = [] }) {
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const requestedSessionId = searchParams.get("check");
  const [activeTab, setActiveTab] = useState("rubrics");
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [focusedScriptId, setFocusedScriptId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const createDialogPresence = useDialogPresence(createDialogOpen);
  const [creatingCheck, setCreatingCheck] = useState(false);
  const [createCheckError, setCreateCheckError] = useState("");
  const createCheckRequestRef = useRef(0);
  const [openMenuId, setOpenMenuId] = useState(null);
  const [sessionMenuPosition, setSessionMenuPosition] = useState(null);
  // 選單離場動畫：關閉時保留最後的目標與位置 130ms
  const sessionMenuPos = useDialogPresence(sessionMenuPosition, 130);
  const [busySessionIds, setBusySessionIds] = useState(() => new Set());
  const [renameTarget, setRenameTarget] = useState(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [renameInvalid, setRenameInvalid] = useState(false);
  const [moveWeekTarget, setMoveWeekTarget] = useState(null);
  const [moveWeekId, setMoveWeekId] = useState("");
  const renameInputRef = useRef(null);
  const requestVersionRef = useRef(0);
  const classIdRef = useRef(classId);
  const closeSessionMenu = useCallback(() => {
    setOpenMenuId(null);
    setSessionMenuPosition(null);
  }, []);

  const activeSession = useMemo(
    () => sessions.find((item) => item.id === activeSessionId) ?? null,
    [activeSessionId, sessions],
  );
  const openSessionMenuItem = useMemo(
    () => sessions.find((item) => item.id === openMenuId) ?? null,
    [openMenuId, sessions],
  );
  const sessionMenuItemKeep = useDialogPresence(openSessionMenuItem, 130);

  const loadSessions = useCallback(async () => {
    const requestVersion = ++requestVersionRef.current;
    const requestClassId = classId;
    setLoading(true);
    try {
      const rows = await AiJudgeService.listSessions(classId);
      if (requestVersion !== requestVersionRef.current || classIdRef.current !== requestClassId) return;
      setSessions(rows);
      setActiveSessionId((current) => {
        const existing = resolveActiveSessionId(current, rows);
        if (existing) return existing;
        return resolveActiveSessionId(requestedSessionId, rows);
      });
    } catch (error) {
      if (requestVersion === requestVersionRef.current && classIdRef.current === requestClassId) {
        setSessions([]);
        setActiveSessionId(null);
        toast.error(error?.message ?? "載入檢查失敗");
      }
    } finally {
      if (requestVersion === requestVersionRef.current && classIdRef.current === requestClassId) setLoading(false);
    }
  }, [classId, requestedSessionId, toast]);

  useEffect(() => {
    classIdRef.current = classId;
    createCheckRequestRef.current += 1;
    setCreateDialogOpen(false);
    setCreatingCheck(false);
    setCreateCheckError("");
    setRenameTarget(null);
    setRenameTitle("");
    setActiveSessionId(null);
    closeSessionMenu();
    loadSessions();
    return () => { requestVersionRef.current += 1; };
  }, [closeSessionMenu, loadSessions]);

  useEffect(() => {
    setFocusedScriptId(null);
    setRenameTarget(null);
    setRenameTitle("");
    closeSessionMenu();
  }, [activeSessionId, closeSessionMenu]);

  useEffect(() => {
    if (!openMenuId) return undefined;
    const menuId = `check-menu-${openMenuId}`;
    function updateMenuPosition() {
      const trigger = document.querySelector(`[aria-controls="${menuId}"]`);
      if (!(trigger instanceof HTMLElement)) return;
      setSessionMenuPosition(getSessionMenuPosition(trigger.getBoundingClientRect()));
    }
    updateMenuPosition();
    const focusTimer = window.setTimeout(() => {
      document.getElementById(menuId)?.querySelector('[role="menuitem"]:not(:disabled)')?.focus();
    }, 0);
    function closeMenuOnOutsideClick(event) {
      const target = event.target;
      if (target instanceof Element && (target.closest(`#${menuId}`) || target.closest(`[aria-controls="${menuId}"]`))) return;
      closeSessionMenu();
    }
    function navigateMenu(event) {
      if (event.key === "Escape") {
        closeSessionMenu();
        return;
      }
      if (!event.key || !["ArrowDown", "ArrowUp"].includes(event.key)) return;
      const menu = document.getElementById(menuId);
      const items = menu ? [...menu.querySelectorAll('[role="menuitem"]:not(:disabled)')] : [];
      const currentIndex = items.indexOf(document.activeElement);
      if (!items.length) return;
      event.preventDefault();
      const nextIndex = event.key === "ArrowDown"
        ? (currentIndex + 1) % items.length
        : (currentIndex - 1 + items.length) % items.length;
      items[nextIndex].focus();
    }
    document.addEventListener("mousedown", closeMenuOnOutsideClick);
    document.addEventListener("keydown", navigateMenu);
    window.addEventListener("resize", updateMenuPosition);
    window.addEventListener("scroll", updateMenuPosition, true);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("mousedown", closeMenuOnOutsideClick);
      document.removeEventListener("keydown", navigateMenu);
      window.removeEventListener("resize", updateMenuPosition);
      window.removeEventListener("scroll", updateMenuPosition, true);
      const active = document.activeElement;
      if (active instanceof HTMLElement && active.closest(`#${menuId}`)) {
        document.querySelector(`[aria-controls="${menuId}"]`)?.focus();
      }
    };
  }, [closeSessionMenu, openMenuId]);

  function updateSessionInList(updated) {
    if (classIdRef.current !== classId) return;
    setSessions((current) => current.map((item) => item.id === updated.id ? updated : item));
  }

  function openCreateCheckDialog() {
    if (creatingCheck) return;
    setCreateCheckError("");
    setCreateDialogOpen(true);
  }

  function closeCreateCheckDialog() {
    if (creatingCheck) return;
    setCreateDialogOpen(false);
    setCreateCheckError("");
  }

  async function handleCreateCheck(title) {
    const nextTitle = String(title ?? "").trim();
    if (!nextTitle || creatingCheck) return;
    const requestClassId = classId;
    const requestId = ++createCheckRequestRef.current;
    setCreatingCheck(true);
    setCreateCheckError("");
    try {
      const created = await AiJudgeService.createBlankSession(classId, {
        title: nextTitle,
        rubricName: nextTitle,
      });
      if (requestId !== createCheckRequestRef.current || classIdRef.current !== requestClassId) return;
      handleCreated(created);
    } catch (error) {
      if (requestId === createCheckRequestRef.current && classIdRef.current === requestClassId) {
        setCreateCheckError(error?.message ?? "建立空白檢查失敗，請稍後再試。");
      }
    } finally {
      if (requestId === createCheckRequestRef.current && classIdRef.current === requestClassId) {
        setCreatingCheck(false);
      }
    }
  }

  function handleCreated(created) {
    if (classIdRef.current !== classId) return;
    setCreateDialogOpen(false);
    setCreateCheckError("");
    setSessions((current) => [created, ...current.filter((item) => item.id !== created.id)]);
    setActiveSessionId(created.id);
    setActiveTab("rubrics");
    toast.success(`已建立「${created.title}」`);
  }

  async function runSessionAction(item, action) {
    if (!item || busySessionIds.has(item.id)) return;
    const requestClassId = classId;
    setBusySessionIds((current) => new Set(current).add(item.id));
    closeSessionMenu();
    try {
      const updated = await action(item);
      if (classIdRef.current !== requestClassId) return null;
      if (updated) {
        if (updated.status === "deleted") {
          setSessions((current) => current.filter((entry) => entry.id !== item.id));
          if (item.id === activeSessionId) setActiveSessionId(null);
        } else {
          updateSessionInList(updated);
        }
      }
      return updated;
    } catch (error) {
      if (classIdRef.current === requestClassId) {
        toast.error(error?.message ?? "檢查操作失敗");
      }
      return null;
    } finally {
      setBusySessionIds((current) => {
        const next = new Set(current);
        next.delete(item.id);
        return next;
      });
    }
  }

  async function pinSession(item) {
    await runSessionAction(item, (entry) => AiJudgeService.updateSession(classId, entry.id, { is_pinned: !entry.pinned_at }));
    loadSessions();
  }

  async function forkSession(item) {
    const copy = await runSessionAction(item, (entry) => AiJudgeService.forkSession(classId, entry.id));
    if (!copy) return;
    setSessions((current) => [copy, ...current.filter((entry) => entry.id !== copy.id)]);
    setActiveSessionId(copy.id);
    setActiveTab("rubrics");
    toast.success(`已建立「${copy.title}」，可開始調整檢查表。`);
  }

  async function renameSession(event) {
    event.preventDefault();
    const nextTitle = renameTitle.trim();
    if (!renameTarget || busySessionIds.has(renameTarget.id)) return;
    if (!nextTitle) {
      setRenameInvalid(true);
      focusInvalidField(renameInputRef.current);
      return;
    }
    const target = renameTarget;
    if (String(target.title ?? "").trim() === nextTitle) {
      setRenameTarget(null);
      setRenameTitle("");
      return;
    }
    const updated = await runSessionAction(target, (entry) => (
      AiJudgeService.updateSession(classId, entry.id, { title: nextTitle })
    ));
    if (updated) {
      setRenameTarget(null);
      setRenameTitle("");
    }
  }

  async function moveSessionToWeek(event) {
    event.preventDefault();
    const target = moveWeekTarget;
    if (!target || !moveWeekId || busySessionIds.has(target.id)) return;
    const updated = await runSessionAction(target, (entry) => (
      AiJudgeService.updateSession(classId, entry.id, {
        teaching_class_week_id: moveWeekId,
      })
    ));
    if (updated) {
      setMoveWeekTarget(null);
      setMoveWeekId("");
      toast.success(`已將「${target.title}」移到正確週次。`);
    }
  }

  async function deleteSession(item) {
    const deleted = await runSessionAction(item, async (entry) => {
      await AiJudgeService.deleteSession(classId, entry.id);
      return { ...entry, status: "deleted" };
    });
    if (deleted) toast.success(`「${item.title}」及其檢查資料已刪除。`);
  }

  function cancelRename() {
    setRenameTarget(null);
    setRenameTitle("");
    setRenameInvalid(false);
  }

  function toggleSessionMenu(event, sessionId) {
    event.stopPropagation();
    if (openMenuId === sessionId) {
      closeSessionMenu();
      return;
    }
    setSessionMenuPosition(getSessionMenuPosition(event.currentTarget.getBoundingClientRect()));
    setOpenMenuId(sessionId);
  }

  function renderSessionMenu(item) {
    const menuPos = sessionMenuPos.item;
    if (!item || !menuPos) return null;
    const busy = busySessionIds.has(item.id);
    return (
      <div
        id={`check-menu-${item.id}`}
        className={`${styles.sessionMenu} ${sessionMenuPos.closing ? styles.sessionMenuOut : ""}`}
        role="menu"
        aria-label={`「${item.title}」更多功能`}
        style={{ top: `${menuPos.top}px`, left: `${menuPos.left}px` }}
      >
        <button type="button" role="menuitem" disabled={busy} onClick={() => { setRenameTarget(item); setRenameTitle(item.title); setRenameInvalid(false); closeSessionMenu(); }}><MIcon name="edit" size={16} />重新命名</button>
        <button type="button" role="menuitem" disabled={busy} onClick={() => { setMoveWeekTarget(item); setMoveWeekId(item.teaching_class_week_id ?? ""); closeSessionMenu(); }}><MIcon name="calendar_month" size={16} />調整週次</button>
        <button type="button" role="menuitem" disabled={busy} onClick={() => pinSession(item)}><MIcon name="push_pin" filled={Boolean(item.pinned_at)} size={16} />{item.pinned_at ? "取消釘選" : "釘選"}</button>
        <button type="button" role="menuitem" disabled={busy} onClick={() => forkSession(item)}><MIcon name="fork_right" size={16} />重構</button>
        <span className={styles.menuSeparator} />
        <button type="button" role="menuitem" className={styles.menuDanger} disabled={busy} onClick={() => deleteSession(item)}><MIcon name="delete" size={16} />刪除</button>
      </div>
    );
  }

  const sessionSidebarInner = (
    <>
      <button type="button" className={`${styles.btnPrimary} ${styles.newCheckButton}`} onClick={openCreateCheckDialog}><MIcon name="add" size={17} />新增檢查</button>
      <div className={styles.sessionList} role="list">
        {loading ? <p className={styles.mutedText}>載入中…</p> : sessions.length === 0 ? <div className={styles.sidebarEmpty}><MIcon name="checklist" size={24} /><p>尚未建立檢查。新增後會開啟空白檢查表，再與 AI 討論並調整。</p></div> : sessions.map((item) => {
          const selected = item.id === activeSessionId;
           const busy = busySessionIds.has(item.id);
               const linkedWeek = weeks.find((week) => String(week.id) === String(item.teaching_class_week_id));
               const renaming = renameTarget?.id === item.id;
               return (
                 <div key={item.id} className={`${styles.sessionRow} ${selected ? styles.sessionRowActive : ""} ${renaming ? styles.sessionRowRenaming : ""}`} role="listitem">
                   {renaming ? (
                     <form className={styles.sessionRenameForm} onSubmit={renameSession} onClick={(event) => event.stopPropagation()}>
                       <input
                         ref={renameInputRef}
                         className={`${styles.sessionRenameInput} ${renameInvalid ? styles.fieldInvalid : ""}`}
                         autoFocus
                         value={renameTitle}
                         maxLength={255}
                         aria-label={`重新命名「${item.title}」`}
                         title="按 Enter 儲存，Esc 取消"
                         onChange={(event) => { setRenameTitle(event.target.value); setRenameInvalid(false); }}
                         onKeyDown={(event) => {
                           if (event.key === "Escape") {
                             event.preventDefault();
                             cancelRename();
                           }
                         }}
                       />
                     </form>
                   ) : (
                     <button type="button" className={selected ? styles.sessionItemActive : styles.sessionItem} aria-current={selected ? "true" : undefined} onClick={() => { setCreateDialogOpen(false); setActiveSessionId(item.id); closeSessionMenu(); }}>
                       <SessionTitle title={item.title}>{item.title}</SessionTitle>
                       <small className={styles.sessionWeekLabel}>{linkedWeek ? `第 ${linkedWeek.week ?? linkedWeek.week_number} 週 · ${linkedWeek.title}` : "尚未指定週次"}</small>
                     </button>
                   )}
                   <div className={styles.sessionRowActions}>
                     {renaming ? <button type="button" className={styles.iconBtn} aria-label="取消重新命名" title="取消" onClick={cancelRename}><MIcon name="close" size={17} /></button> : <>
                       <button type="button" className={`${styles.iconBtn} ${item.pinned_at ? styles.pinActive : ""}`} aria-label={item.pinned_at ? `取消釘選「${item.title}」` : `釘選「${item.title}」`} aria-pressed={Boolean(item.pinned_at)} title={item.pinned_at ? "取消釘選" : "釘選"} disabled={busy} onClick={(event) => { event.stopPropagation(); pinSession(item); }}><MIcon name="push_pin" filled={Boolean(item.pinned_at)} size={17} /></button>
                       <button type="button" className={styles.iconBtn} aria-label={`更多「${item.title}」功能`} title="更多功能" aria-haspopup="menu" aria-expanded={openMenuId === item.id} aria-controls={`check-menu-${item.id}`} disabled={busy} onClick={(event) => toggleSessionMenu(event, item.id)}><MIcon name="more_vert" size={18} /></button>
                     </>}
                   </div>
                 </div>
               );
            })}
          </div>
    </>
  );

  const subTabsBar = (
    <div className={styles.subTabs} role="tablist" aria-label="檢查工作頁籤">{TEACHER_JUDGE_TABS.map((tab) => <button key={tab.key} type="button" role="tab" aria-selected={activeTab === tab.key} className={activeTab === tab.key ? styles.subTabActive : styles.subTab} onClick={() => setActiveTab(tab.key)}><MIcon name={tab.icon} size={16} />{tab.label}</button>)}</div>
  );

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeading}>
        <h2 className={styles.panelTitle}><MIcon name="checklist" size={20} />AI 檢查</h2>
      </div>

      {activeSession ? (
        activeTab === "rubrics" ? (
          <section className={styles.sessionMainFull} aria-label="檢查設定工作區">
            <RubricsTab key={activeSession.id} classId={classId} judgeSession={activeSession} onSessionUpdated={updateSessionInList} sidebar={sessionSidebarInner} tabsBar={subTabsBar} onScriptCreated={(artifact) => { loadSessions(); const destination = getScriptCreationDestination(artifact); setFocusedScriptId(destination === "scripts" ? (artifact?.id ?? null) : null); setActiveTab(destination); }} />
          </section>
        ) : (
          <section className={styles.sessionMainFull} aria-label="檢查工作區">
            <div className={styles.checkWorkspaceTwo}>
              <aside className={`${styles.card} ${styles.checkSessionCol}`} aria-label="檢查清單">
                {sessionSidebarInner}
              </aside>
              <div className={styles.checkContentCol}>
                <div className={`${styles.card} ${styles.checkTabsCard}`}>{subTabsBar}</div>
                {activeTab === "scripts" && <ScriptsTab classId={classId} sessionId={activeSession.id} initialSelectedId={focusedScriptId} onScriptApproved={() => setActiveTab("review")} />}
                {activeTab === "review" && <TeacherReviewTab classId={classId} sessionId={activeSession.id} members={members} machineNodes={machineNodes} />}
              </div>
            </div>
          </section>
        )
      ) : (
      <div className={styles.sessionWorkspace}>
        <aside className={styles.sessionSidebar} aria-label="檢查清單">
          {sessionSidebarInner}
        </aside>

        <section className={styles.sessionMain}>
          <div className={styles.card}>
            <div className={styles.mainEmpty}>
              <MIcon name="checklist" size={30} />
              <p>請從左側選擇一項檢查，或新增檢查。</p>
              <button type="button" className={styles.btnPrimary} onClick={openCreateCheckDialog}>新增檢查</button>
            </div>
          </div>
        </section>
      </div>
      )}

      {typeof document !== "undefined" && sessionMenuItemKeep.open && sessionMenuPos.item && createPortal(renderSessionMenu(sessionMenuItemKeep.item), document.body)}

      {createDialogPresence.open && (
        <CreateCheckDialog
          closing={createDialogPresence.closing}
          busy={creatingCheck}
          error={createCheckError}
          onClose={closeCreateCheckDialog}
          onSubmit={handleCreateCheck}
        />
      )}
      {typeof document !== "undefined" && moveWeekTarget && createPortal(
        <div className={styles.modalOverlay} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setMoveWeekTarget(null); }}>
          <form className={styles.modal} onSubmit={moveSessionToWeek}>
            <div className={styles.modalHeader}>
              <div>
                <h2>調整檢查週次</h2>
                <p>「{moveWeekTarget.title}」只會出現在所選週次，學生端不會再混到其他週。</p>
              </div>
              <button type="button" className={styles.dialogClose} aria-label="關閉" onClick={() => setMoveWeekTarget(null)}><MIcon name="close" size={18} /></button>
            </div>
            <label className={styles.dialogField}>
              <span>所屬週任務</span>
              <select value={moveWeekId} onChange={(event) => setMoveWeekId(event.target.value)} autoFocus>
                <option value="" disabled>請選擇週任務</option>
                {weeks.filter((week) => week.title?.trim()).map((week) => <option key={week.id} value={week.id}>第 {week.week ?? week.week_number} 週 · {week.title}</option>)}
              </select>
            </label>
            <div className={styles.modalActions}>
              <button type="button" className={styles.btnSecondary} onClick={() => setMoveWeekTarget(null)}>取消</button>
              <button type="submit" className={styles.btnPrimary} disabled={!moveWeekId || busySessionIds.has(moveWeekTarget.id)}>儲存週次</button>
            </div>
          </form>
        </div>,
        document.body,
      )}

    </div>
  );
}

export default function AiJudgePanel({ classId, members, weeks = [], machineNodes = [] }) {
  return <TeacherWorkspacePanel classId={classId} members={members} weeks={weeks} machineNodes={machineNodes} />;
}
