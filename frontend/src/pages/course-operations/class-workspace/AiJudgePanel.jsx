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

function hasCompleteParameterizedStep(step, judgementMode = "ai") {
  const parameters = step?.parameters ?? {};
  const hasArgv = Array.isArray(parameters.argv)
    && parameters.argv.length > 0
    && parameters.argv.every((part) => typeof part === "string" && part.trim());
  const hasTimeout = Number.isInteger(parameters.timeout_seconds)
    && parameters.timeout_seconds >= 1
    && parameters.timeout_seconds <= 300;
  const hasSuccessCriteria = judgementMode === "teacher"
    || (typeof parameters.success_criteria === "string"
      && parameters.success_criteria.trim());
  if (step?.command_key === "python.run_entrypoint") {
    return Boolean(typeof parameters.cwd === "string"
      && parameters.cwd.trim()
      && hasArgv
      && hasTimeout
      && hasSuccessCriteria);
  }
  if (step?.command_key === "system.run_command") {
    return Boolean(hasArgv && hasTimeout && hasSuccessCriteria);
  }
  return true;
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
        !hasCompleteParameterizedStep(step, item.judgement_mode ?? "ai")
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
    description: item.description ?? "",
    checked: Boolean(item.checked),
    detectable: item.detectable ?? "manual",
    judgement_mode: item.judgement_mode ?? "ai",
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
  unsupported: { label: "無法自動取證", className: styles.detBadge_manual },
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
                          <small><em>{proposalOperationLabel(item)}</em>{item.description || "AI 建議新增或調整此檢查項目"}</small>
                        </span>
                      </label>
                    );
                  }
                  const info = ITEMWISE_STATUS_INFO[result.status] ?? ITEMWISE_STATUS_INFO.analysis_error;
                  const gaps = Array.isArray(result.missing_information) ? result.missing_information.filter(Boolean) : [];
                  const reason = gaps.length ? gaps.join("、") : (result.detail || "");
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
                        <small><em>{proposalOperationLabel(item)}</em>{item.description || "AI 建議新增或調整此檢查項目"}</small>
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
  const detectableInfo = needsReview
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
          <label className={styles.tableField}>
            <span className={styles.srOnly}>第 {index + 1} 項檢查條件</span>
            <textarea
              value={item.description}
              onChange={(event) => onChange({ ...item, description: event.target.value })}
              placeholder="寫下學生需要符合的條件"
              rows={2}
              disabled={disabled}
            />
          </label>
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
                        : "請補充完整的服務名稱、程式位置、連接埠、取證範圍或判定條件。"}</p>
                    </div>
                  )}
                  {item.detection_method && (
                    <div className={styles.detectItem}>
                      <span>檢測方式</span>
                      <p>{item.detection_method}</p>
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
                      <div className={styles.chipRow}>
                        {checkSteps.map((step) => (
                          <span key={`${step.template_key}-${step.command_key}`} className={styles.chip}>
                            {getTemplateLabel(step.template_key)} /{" "}
                            {step.command_label ?? step.command_key}
                            <code>{step.command_key}</code>
                          </span>
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
            <th scope="col">檢查條件</th>
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
            className={styles.iconBtn}
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

function RubricsTab({ classId, judgeSession, onSessionUpdated, onScriptCreated, sidebar = null, tabsBar = null }) {
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
        return [
          ...baseMessages,
          response.user_message,
          response.assistant_message,
        ].filter(shouldDisplayChatMessage);
      });
      setPendingAttachments([]);
      const proposal = buildProposalDiff(analysis?.items ?? [], response.rubric_proposal);
      const itemResults = response.assistant_message?.metadata_json?.item_results;
      setPendingItemResults(Array.isArray(itemResults) && itemResults.length ? itemResults : null);
      setPendingProposal(proposal.length ? proposal : null);
      setSelectedProposalIds(new Set(proposal.map((item, index) => item.id ?? `proposal-${index}`)));
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
      toast.error(err?.message ?? "對話失敗");
      setMessages(messages);
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
    const { items: nextItems, evaluatedIds } = applyProposalOperations(
      analysis?.items ?? [],
      pendingProposal,
      selectedProposalIds,
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
      if (!Array.isArray(response.rubric_proposal)) {
        throw new Error("AI 未回傳完整檢查項目列表，尚未變更目前檢查表");
      }
      const proposal = buildProposalDiff(analysis.items ?? [], response.rubric_proposal);
      const { items: candidateItems } = applyProposalOperations(analysis.items ?? [], proposal);
      const candidateAnalysis = {
        ...applyItems(analysis, candidateItems),
        detectability_needs_review: false,
        pending_review_item_ids: [],
      };
      const blocker = getScriptCreationBlocker({
        analysis: candidateAnalysis,
        pendingProposal: null,
        pendingReviewIds: new Set(),
      });
      if (blocker) {
        setPendingProposal(proposal.length ? proposal : null);
        setSelectedProposalIds(new Set(proposal.map((item, index) => item.id ?? `proposal-${index}`)));
        setPendingProposalMeta(proposal.length ? { baseRevision } : null);
        setPendingProposalIsRefine(Boolean(proposal.length));
        const message = `${blocker}。請確認問題項目後再試一次。`;
        setScriptGenerationNotice({ status: "error", message });
        toast.error(message);
        return;
      }
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
      const artifact = await AiJudgeService.createSessionScript(
        classId,
        judgeSession.id,
        savedRevision,
      );
      if (artifact.status === "approved") {
        const message = "檢查腳本已通過靜態與 AI 檢查，可開始執行。";
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
    } catch (err) {
      const message = err?.message ?? "儲存並製作檢查腳本失敗";
      setScriptGenerationNotice({
        status: "error",
        message: `${message}。目前檢查表已保留，可再次按「儲存並製作」重試。`,
      });
      toast.error(message);
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
  return artifact?.status === "approved" ? "execution" : "scripts";
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

function RetrySummary({ script }) {
  const summary = script?.policy_check_result_json?.retry_summary;
  const attempts = Array.isArray(script?.policy_check_result_json?.review_attempts)
    ? script.policy_check_result_json.review_attempts
    : [];
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
      {attempts.length > 0 && (
        <ul className={styles.reviewIssues}>
          {attempts.slice(-3).map((attempt, index) => {
            const issues = [
              ...(Array.isArray(attempt?.safety_issues) ? attempt.safety_issues : []),
              ...(Array.isArray(attempt?.quality_issues) ? attempt.quality_issues : []),
              ...(Array.isArray(attempt?.ai_review_issues) ? attempt.ai_review_issues : []),
              ...(Array.isArray(attempt?.generation_issues) ? attempt.generation_issues : []),
            ].filter(Boolean);
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

/* ── Tab 2：執行結果 ────────────────────────────────────── */

const REASON_LABELS = {
  success: "成功",
  not_running: "未運行",
  missing_ip: "缺少 IP",
  missing_ssh_key: "缺少 SSH 金鑰",
  owner_mismatch: "資源擁有者不一致",
  missing_db_resource: "找不到對應資源",
  invalid_resource_type: "類型不可執行",
  python_missing: "機器缺少腳本執行環境",
  execution_nonzero: "腳本執行失敗",
  result_too_large: "結果過大",
  invalid_json: "JSON 格式錯誤",
  executor_error: "執行器錯誤",
};

function reasonLabel(reasonCode) {
  if (!reasonCode) return null;
  return REASON_LABELS[reasonCode] ?? reasonCode;
}

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

const TARGET_STATUS = {
  completed: { label: "完成", className: styles.badge_success },
  running: { label: "執行中", className: styles.badge_info },
  failed: { label: "失敗", className: styles.badge_danger },
  queued: { label: "排隊中", className: styles.badge_muted },
};

function StatusBadge({ map, status }) {
  const info = map[status] ?? { label: status ?? "—", className: styles.badge_muted };
  return <span className={`${styles.badge} ${info.className}`}>{info.label}</span>;
}

function AiJudgementBadge({ result }) {
  if (!result) return <span className={`${styles.badge} ${styles.badge_muted}`}>等待回收</span>;
  if (result.validation?.valid === false) {
    return <span className={`${styles.badge} ${styles.badge_danger}`}>JSON 格式錯誤</span>;
  }
  const judgement = result.ai_judgement;
  if (!judgement) return <span className={`${styles.badge} ${styles.badge_muted}`}>分析中</span>;
  if (judgement.status === "completed") {
    if (judgement.requires_teacher_review) {
      return <span className={`${styles.badge} ${styles.badge_info}`}>待導師核查</span>;
    }
    return <span className={`${styles.badge} ${styles.badge_success}`}>已核對</span>;
  }
  if (judgement.status === "failed") {
    return <span className={`${styles.badge} ${styles.badge_danger}`}>AI 核對失敗</span>;
  }
  if (judgement.status === "skipped") {
    return <span className={`${styles.badge} ${styles.badge_muted}`}>略過</span>;
  }
  return <span className={`${styles.badge} ${styles.badge_info}`}>分析中</span>;
}

function aiJudgementSummary(result) {
  if (!result) return null;
  if (result.validation?.valid === false) {
    return result.validation.error ?? "JSON 驗證未通過，未進入 AI 核對。";
  }
  const judgement = result.ai_judgement;
  if (!judgement) return "AI 核對尚未完成。";
  return judgement.error ?? judgement.summary ?? null;
}

function JudgementItemBadge({ item }) {
  let info = { label: "未判定", className: styles.badge_muted };
  if (item?.judgement_mode === "teacher") {
    info = { label: "待導師核查", className: styles.badge_info };
  } else if (item?.status === "pass") {
    info = { label: "通過", className: styles.badge_success };
  } else if (item?.status === "fail") {
    info = { label: "未通過", className: styles.badge_danger };
  } else if (item?.status === "warning") {
    info = { label: "需注意", className: styles.badge_info };
  }
  return <span className={`${styles.badge} ${info.className}`}>{info.label}</span>;
}

function formatUsage(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return "--";
  return `${Math.round(value)}%`;
}

function ExecutionTab({ classId, sessionId, members }) {
  const toast = useToast();
  const [selectedVmids, setSelectedVmids] = useState([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const runDialog = useDialogPresence(dialogOpen);
  const [selectedScriptId, setSelectedScriptId] = useState(null);
  const [creatingRun, setCreatingRun] = useState(false);
  const [activeRunRef, setActiveRunRef] = useState(null); // { scriptId, runId }
  const [activeRun, setActiveRun] = useState(null);
  const [scripts, setScripts] = useState([]);
  const [runHistory, setRunHistory] = useState([]);

  useEffect(() => {
    AiJudgeService.listScripts(classId, sessionId)
      .then(setScripts)
      .catch(() => {});
  }, [classId, sessionId]);

  useEffect(() => {
    let cancelled = false;
    setActiveRun(null);
    setActiveRunRef(null);
    setRunHistory([]);
    if (!sessionId) return undefined;
    AiJudgeService.listSessionRuns(classId, sessionId)
      .then(async (runs) => {
        if (cancelled) return;
        setRunHistory(runs);
        const latest = runs[0];
        if (latest) {
          const detail = await AiJudgeService.getSessionRun(classId, sessionId, latest.id);
          if (cancelled) return;
          setActiveRun(detail);
          if (!runIsTerminal(latest.status)) {
            setActiveRunRef({ scriptId: latest.artifact_id, runId: latest.id });
          }
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [classId, sessionId]);

  /* 執行任務輪詢：每 2 秒直到終態；失敗放慢到 5 秒重試 */
  useEffect(() => {
    if (!activeRunRef) return undefined;
    let cancelled = false;
    let timer = null;

    async function poll() {
      try {
        const run = await AiJudgeService.getScriptRun(
          classId,
          activeRunRef.scriptId,
          activeRunRef.runId,
        );
        if (cancelled) return;
        setActiveRun(run);
        if (!runIsTerminal(run.status)) timer = setTimeout(poll, 2000);
      } catch {
        if (!cancelled) timer = setTimeout(poll, 5000);
      }
    }

    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [classId, activeRunRef]);

  const approvedScripts = useMemo(
    () => scripts.filter((script) => script.status === "approved"),
    [scripts],
  );
  const effectiveScriptId = selectedScriptId ?? approvedScripts[0]?.id ?? "";
  const effectiveScript = approvedScripts.find((script) => script.id === effectiveScriptId);

  const runningMembers = members.filter(
    (member) =>
      member.vmid &&
      member.vm_status === "running" &&
      (member.vm_type === "qemu" || member.vm_type === "lxc"),
  );
  const selectedSet = new Set(selectedVmids);

  const progressTargets = activeRun?.progress_json?.targets ?? [];
  const resultTargets = activeRun?.target_results_json?.targets ?? [];
  const resultByVmid = new Map(resultTargets.map((result) => [result.vmid, result]));

  function toggleVmid(vmid, checked) {
    setSelectedVmids((current) =>
      checked ? Array.from(new Set([...current, vmid])) : current.filter((item) => item !== vmid),
    );
  }

  async function handleCreateRun() {
    setCreatingRun(true);
    try {
      const run = sessionId
        ? await AiJudgeService.createSessionRun(
            classId,
            sessionId,
            effectiveScriptId,
            selectedVmids,
          )
        : await AiJudgeService.createScriptRun(classId, effectiveScriptId, selectedVmids);
      toast.success(
        `已建立腳本執行任務（${run.progress_json?.total ?? selectedVmids.length} 台）`,
      );
      setActiveRun(run);
      setRunHistory((current) => [run, ...current.filter((item) => item.id !== run.id)]);
      setActiveRunRef({ scriptId: effectiveScriptId, runId: run.id });
      setDialogOpen(false);
      setSelectedScriptId(null);
      setSelectedVmids([]);
    } catch (err) {
      toast.error(err?.message ?? "建立執行任務失敗");
    } finally {
      setCreatingRun(false);
    }
  }

  return (
    <div className={styles.tabBody}>
      <div className={styles.execToolbar}>
        <span className={styles.mutedText}>
          可執行 {runningMembers.length} / 全部 {members.length} 台，已選{" "}
          <strong>{selectedVmids.length}</strong> 台
        </span>
        <div className={styles.sectionActions}>
          <button
            type="button"
            className={styles.btnSecondary}
            onClick={() => setSelectedVmids(runningMembers.map((m) => m.vmid).filter(Boolean))}
            disabled={runningMembers.length === 0}
          >
            選取運行中
          </button>
          <button
            type="button"
            className={styles.btnSecondary}
            onClick={() => setSelectedVmids([])}
            disabled={selectedVmids.length === 0}
          >
            清除
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            onClick={() => setDialogOpen(true)}
            disabled={selectedVmids.length === 0 || approvedScripts.length === 0}
          >
            <MIcon name="play_circle_outline" size={16} />
            執行腳本
          </button>
        </div>
      </div>

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.checkCol} />
              <th>機器編號</th>
              <th>成員</th>
              <th>類型</th>
              <th>狀態</th>
              <th>資源摘要</th>
            </tr>
          </thead>
          <tbody>
            {runningMembers.length === 0 ? (
              <tr>
                <td colSpan={6} className={styles.tableEmpty}>
                  目前沒有可執行的運行中 VM/LXC。
                </td>
              </tr>
            ) : (
              runningMembers.map((member) => (
                <tr key={member.user_id}>
                  <td>
                    <input
                      type="checkbox"
                      className={styles.checkbox}
                      checked={selectedSet.has(member.vmid)}
                      onChange={(e) => toggleVmid(member.vmid, e.target.checked)}
                    />
                  </td>
                  <td className={styles.monoCell}>{member.vmid ?? "-"}</td>
                  <td>
                    <div>{member.full_name ?? "-"}</div>
                    <div className={styles.fileMeta}>{member.email}</div>
                  </td>
                  <td className={styles.typeCell}>{member.vm_type ? (member.vm_type === "lxc" ? "LXC" : "VM") : "-"}</td>
                  <td>
                    <span className={`${styles.badge} ${styles.badge_success}`}>運行中</span>
                  </td>
                  <td className={styles.fileMeta}>
                    CPU {formatUsage(member.vm_cpu_usage_pct)} · RAM{" "}
                    {formatUsage(member.vm_ram_usage_pct)} · 碟{" "}
                    {formatUsage(member.vm_disk_usage_pct)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {activeRun && (
        <div className={styles.card}>
          <div className={styles.cardHead}>
            <div>
              <h4 className={styles.cardTitle}>
                最近一次執行結果
                <StatusBadge map={RUN_STATUS} status={activeRun.status} />
              </h4>
              <p className={styles.fileMeta}>
                進度 {activeRun.progress_json?.done ?? 0} /{" "}
                {activeRun.progress_json?.total ?? progressTargets.length} 台
              </p>
            </div>
            {!runIsTerminal(activeRun.status) && (
              <span className={styles.mutedText}>
                <Spinner size={14} /> 更新中...
              </span>
            )}
          </div>

          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>編號</th>
                  <th>成員</th>
                  <th>來源節點</th>
                  <th>執行狀態</th>
                  <th>系統核對／導師核查</th>
                </tr>
              </thead>
              <tbody>
                {progressTargets.map((target) => {
                  const result = resultByVmid.get(target.vmid);
                  const user = result?.user ?? target.user;
                  const proxmoxNode = result?.proxmox_node ?? target.proxmox_node;
                  const resourceType = result?.resource_type ?? target.resource_type;
                  const targetReason = reasonLabel(result?.reason_code ?? target.reason_code);
                  const summary = aiJudgementSummary(result);
                  const summaryIsError =
                    result?.validation?.valid === false ||
                    result?.ai_judgement?.status === "failed";
                  return (
                    <tr key={target.vmid}>
                      <td className={styles.monoCell}>{target.name ?? target.vmid}</td>
                      <td>
                        <div>{user?.full_name ?? "-"}</div>
                        {user?.email && <div className={styles.fileMeta}>{user.email}</div>}
                      </td>
                      <td>
                        <div className={styles.monoCell}>{proxmoxNode ?? "-"}</div>
                        <div className={`${styles.fileMeta} ${styles.typeCell}`}>
                          {resourceType ? (resourceType === "lxc" ? "LXC" : "VM") : "-"}
                        </div>
                      </td>
                      <td>
                        <StatusBadge map={TARGET_STATUS} status={target.status} />
                        {targetReason && targetReason !== "成功" && (
                          <div className={styles.fileMeta}>{targetReason}</div>
                        )}
                      </td>
                      <td>
                        <AiJudgementBadge result={result} />
                        {result ? (
                          <details className={styles.judgeDetails}>
                            <summary>查看檢查結果說明</summary>
                            {summary && (
                              <p className={summaryIsError ? styles.dangerText : styles.mutedText}>
                                {summary}
                              </p>
                            )}
                            {(result.ai_judgement?.item_judgements ?? []).map((item, index) => (
                              <div key={`${item.item_id ?? "item"}-${index}`} className={styles.judgeItem}>
                                <div className={styles.judgeItemHead}>
                                  <span>{item.title ?? item.item_id ?? "檢查項目"}</span>
                                  <JudgementItemBadge item={item} />
                                </div>
                                {item.comment && <p>{item.comment}</p>}
                              </div>
                            ))}
                            {(result.parsed_result?.checks ?? []).length > 0 && (
                              <div className={styles.judgeItem}>
                                <div className={styles.judgeItemHead}>
                                  <span>腳本收集證據</span>
                                </div>
                                {(result.parsed_result.checks ?? []).map((check, index) => (
                                  <div key={`${check.id ?? "check"}-${index}`}>
                                    <strong>{check.title ?? check.id ?? "收集項目"}</strong>
                                    {check.evidence && <p>{check.evidence}</p>}
                                    {check.raw && <pre>{check.raw}</pre>}
                                  </div>
                                ))}
                              </div>
                            )}
                          </details>
                        ) : (
                          <div className={styles.fileMeta}>等待回收</div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {sessionId && runHistory.length > 0 && (
        <div className={styles.card}>
          <h4 className={styles.cardTitle}>歷次執行</h4>
          <div className={styles.runHistory}>
            {runHistory.map((run) => (
              <button
                key={run.id}
                type="button"
                className={styles.runHistoryItem}
                onClick={async () => {
                  try {
                    const detail = await AiJudgeService.getSessionRun(
                      classId,
                      sessionId,
                      run.id,
                    );
                    setActiveRun(detail);
                    setActiveRunRef(
                      runIsTerminal(run.status)
                        ? null
                        : { scriptId: run.artifact_id, runId: run.id },
                    );
                  } catch (err) {
                    toast.error(err?.message ?? "載入執行結果失敗");
                  }
                }}
              >
                <span>{formatDateTime(run.created_at)}</span>
                <StatusBadge map={RUN_STATUS} status={run.status} />
              </button>
            ))}
          </div>
        </div>
      )}

      {runDialog.open && (
        <div
          className={`${styles.modalOverlay} ${runDialog.closing ? styles.modalOverlayOut : ""}`}
          onMouseDown={() => setDialogOpen(false)}
        >
          <div className={styles.modal} onMouseDown={(e) => e.stopPropagation()}>
            <div className={styles.modalHeader}>
              <div>
                <h2>確認執行腳本</h2>
                <p>後端會在送出時再次確認這些 VM/LXC 仍屬於此班級且正在運行。</p>
              </div>
              <button
                type="button"
                className={styles.iconBtn}
                onClick={() => setDialogOpen(false)}
                aria-label="關閉"
              >
                <MIcon name="close" size={18} />
              </button>
            </div>

            <label className={styles.field}>
              <span>選擇腳本</span>
              <select
                value={effectiveScriptId}
                onChange={(e) => setSelectedScriptId(e.target.value)}
              >
                {approvedScripts.map((script) => (
                  <option key={script.id} value={script.id}>
                    {script.name}
                  </option>
                ))}
              </select>
              {approvedScripts.length === 0 && (
                <span className={styles.fileMeta}>
                  目前沒有已通過自動檢查的腳本，請先完成檢查腳本產生。
                </span>
              )}
            </label>

            <div className={styles.vmidBox}>
              <span className={styles.fieldLabel}>執行機器（{selectedVmids.length} 台）</span>
              <div className={styles.chipRow}>
                {selectedVmids.map((vmid) => (
                  <span key={vmid} className={styles.chip}>
                    {vmid}
                  </span>
                ))}
              </div>
            </div>

            {effectiveScript && (
              <p className={styles.fileMeta}>
                即將使用：{effectiveScript.name}（
                {getTemplateLabel(effectiveScript.template_key)}）
              </p>
            )}

            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={() => setDialogOpen(false)}
                disabled={creatingRun}
              >
                取消
              </button>
              <button
                type="button"
                className={styles.btnPrimary}
                onClick={handleCreateRun}
                disabled={creatingRun || selectedVmids.length === 0 || !effectiveScriptId}
              >
                {creatingRun ? "建立中..." : "確認執行"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── 導師工作區 ─────────────────────────────────────────── */

const TEACHER_JUDGE_TABS = [
  { key: "rubrics", label: "檢查設定", icon: "description" },
  { key: "execution", label: "執行結果", icon: "play_circle_outline" },
  { key: "scripts", label: "腳本總覽", icon: "terminal" },
];

function TeacherWorkspacePanel({ classId, members, weeks = [] }) {
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
        <p className={styles.panelDesc}>建立檢查表、準備檢查腳本，並查看班級機器的執行結果。</p>
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
                {activeTab === "scripts" && <ScriptsTab classId={classId} sessionId={activeSession.id} initialSelectedId={focusedScriptId} onScriptApproved={() => setActiveTab("execution")} />}
                {activeTab === "execution" && <ExecutionTab classId={classId} sessionId={activeSession.id} members={members} />}
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
              <button type="button" className={styles.iconBtn} aria-label="關閉" onClick={() => setMoveWeekTarget(null)}><MIcon name="close" size={18} /></button>
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

export default function AiJudgePanel({ classId, members, weeks = [] }) {
  return <TeacherWorkspacePanel classId={classId} members={members} weeks={weeks} />;
}
