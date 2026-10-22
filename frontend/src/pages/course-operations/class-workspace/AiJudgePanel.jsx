import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import i18n from "../../../i18n";
import styles from "./AiJudgePanel.module.scss";
import { DataTableCells, DataTableHead } from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import LoadingState, { LoadingSpinner } from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import OctoAvatar from "../../../components/OctoPet/OctoAvatar";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { useToast } from "../../../hooks/useToast";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useUnsavedChanges, useUnsavedChangesGuard } from "../../../contexts/UnsavedChangesContext";
import useDialogPresence from "../../../hooks/useDialogPresence";
import useFileDrop from "../../../hooks/useFileDrop";
import { useQueryErrorEffect, useQueryView } from "../../../hooks/useQueryLoadState";
import {
  getActiveScriptSet,
  getProcessingSessionMessage,
  isTerminalRunStatus,
  keepNewerRubricFiles,
  useAiJudgeCacheUpdater,
  useAiJudgeFilesQuery,
  useAiJudgeReviewQuery,
  useAiJudgeRunBatchQuery,
  useAiJudgeSessionMessagesQuery,
  useAiJudgeSessionsQuery,
} from "../../../queries/aiJudge";
import FileDropOverlay from "../../../components/FileDropzone/FileDropOverlay";
import { downloadBlob } from "../../../services/api";
import { formatDateTime } from "../../../utils/formatDate";
import { joinClauses, joinList } from "../../../utils/joinList";
import { createRubricAnalysisAutosave } from "./rubricAnalysisAutosave";
import {
  AiJudgeService,
  RUBRIC_POLISH_PROMPT,
  getTemplateLabel,
  shouldDisplayChatMessage,
} from "../../../services/aiJudge";

/* 生效腳本集與檢查表清單的合併規則跟著查詢模組走（核查讀取、清單快取都用同一套），這裡沿用原本的匯出 */
export { getActiveScriptSet, keepNewerRubricFiles };

/* 沒有資料時的穩定空陣列：每次 render 給新的 [] 會讓依賴它的 memo／effect 一直重跑 */
const NO_RUBRIC_FILES = Object.freeze([]);
const NO_SESSION_MESSAGES = Object.freeze([]);
const NO_JUDGE_SESSIONS = Object.freeze([]);
const NO_SCRIPT_SETS = Object.freeze([]);

/* 元件外的純函式（狀態文字、擋下原因等）用 i18n.t；元件內一律 useTranslation，切換語系才會重繪 */
function jt(key, options) {
  return i18n.t(`AiJudgePanel.${key}`, { ns: "teaching", ...options });
}

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
  return <MIcon name="autorenew" size={size} spin />;
}

const SCRIPT_GENERATION_PROGRESS = {
  saving: { title: "progressSavingTitle", message: "progressSavingMessage" },
  reviewing: { title: "progressReviewingTitle", message: "progressReviewingMessage" },
  queued: { title: "progressQueuedTitle", message: "progressQueuedMessage" },
  generating: { title: "progressGeneratingTitle", message: "progressGeneratingMessage" },
};

/** AI 回覆放進製作結果提示時的精簡版：去頭尾空白，超過 360 字截斷加刪節號 */
function compactAssistantSummary(assistantMessage) {
  const summary = typeof assistantMessage?.content === "string" ? assistantMessage.content.trim() : "";
  return summary.length > 360 ? `${summary.slice(0, 357)}…` : summary;
}

export function ScriptGenerationNotice({
  isCreatingScript = false,
  status = null,
  notice = null,
}) {
  const { t } = useTranslation("teaching");
  if (!isCreatingScript && !notice) return null;

  const workflowStatus = isCreatingScript ? (status || "queued") : notice.status;
  const isError = !isCreatingScript && notice.status === "error";
  const isSuccess = !isCreatingScript && notice.status === "success";
  const progress = SCRIPT_GENERATION_PROGRESS[workflowStatus]
    ?? SCRIPT_GENERATION_PROGRESS.generating;
  const title = isCreatingScript
    ? t(`AiJudgePanel.${progress.title}`)
    : isError
      ? t("AiJudgePanel.scriptBuildFailedTitle")
      : t("AiJudgePanel.scriptBuildDoneTitle");
  const message = isCreatingScript
    ? t("AiJudgePanel.progressKeepPage", { message: t(`AiJudgePanel.${progress.message}`) })
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

/** 檢查狀態固定收斂為三態：auto=綠；partial、manual 會擋下製作＝紅（警示一律紅）。 */
const DETECTABLE_INFO = {
  auto: { get label() { return jt("detectableAuto"); }, icon: "check_circle", className: styles.detBadge_auto },
  partial: { get label() { return jt("detectablePartial"); }, icon: "warning_amber", className: styles.detBadge_partial },
  manual: { get label() { return jt("detectableManual"); }, icon: "cancel", className: styles.detBadge_manual },
};
// 導師檢查是正常的判定方式，不是錯誤：用一般標記的藍色
const TEACHER_REVIEW_INFO = {
  get label() { return jt("detectableTeacher"); },
  icon: "person",
  className: styles.detBadge_teacher,
};

function getDetectableInfo(detectable) {
  return DETECTABLE_INFO[detectable] ?? DETECTABLE_INFO.manual;
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
    chips.push({ key: "argv", label: jt("chipCommand"), mono: true, parts: argv });
  }
  if (typeof parameters.path === "string" && parameters.path.trim()) {
    chips.push({ key: "path", label: jt("chipPath"), mono: true, parts: [parameters.path.trim()] });
  }
  if (typeof parameters.url === "string" && parameters.url.trim()) {
    chips.push({ key: "url", label: "URL", mono: true, parts: [parameters.url.trim()] });
  }
  if (collector?.type === "file_text") {
    const mode = parameters.read_mode ?? "full";
    const lineText = Number.isInteger(parameters.lines) ? jt("chipReadLines", { count: parameters.lines }) : "";
    chips.push({ key: "read_mode", label: jt("chipRead"), mono: false, parts: [`${mode}${lineText}`] });
  }
  if (Number.isInteger(parameters.timeout_seconds)
    && parameters.timeout_seconds >= 1) {
    chips.push({ key: "timeout_seconds", label: jt("chipTimeout"), mono: false, parts: [jt("chipSeconds", { count: parameters.timeout_seconds })] });
  }
  return chips;
}

const TARGET_TYPE_INFO = {
  file_text: { get label() { return jt("targetFileText"); }, icon: "description" },
  file_stat: { get label() { return jt("targetFile"); }, icon: "folder" },
  command: { get label() { return jt("chipCommand"); }, icon: "terminal" },
  localhost_http: { label: "HTTP", icon: "language" },
  peer_ping: { get label() { return jt("targetPeer"); }, icon: "hub" },
};

const ASSERTION_OPERATOR_LABELS = {
  eq: "=",
  ne: "≠",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
};

function formatCommandArguments(argv) {
  return argv
    .filter((part) => typeof part === "string" && part.trim())
    .map((part) => (/\s/.test(part) ? JSON.stringify(part) : part))
    .join(" ");
}

function formatAssertionValue(value) {
  if (typeof value === "string") return jt("quotedValue", { value });
  if (value === null || value === undefined) return jt("valueUnset");
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return jt("valueUnprintable");
  }
}

function getAssertionSummary(assertion) {
  if (!assertion || typeof assertion !== "object") return "";
  switch (assertion.type) {
    case "returncode_equals":
      return jt("assertReturnCode", { value: formatAssertionValue(assertion.expected) });
    case "text_equals":
      return jt("assertTextEquals", { value: formatAssertionValue(assertion.expected) });
    case "text_contains":
      return jt("assertTextContains", { value: formatAssertionValue(assertion.expected) });
    case "number_compare":
      return jt("assertNumber", {
        operator: ASSERTION_OPERATOR_LABELS[assertion.operator] ?? assertion.operator ?? jt("assertCompare"),
        value: formatAssertionValue(assertion.expected),
      });
    case "json_path_equals":
      return `${assertion.path || jt("assertJsonPath")} = ${formatAssertionValue(assertion.expected)}`;
    case "exists":
      return jt(assertion.expected ? "assertExistsYes" : "assertExistsNo");
    default:
      return assertion.type || jt("assertConfigured");
  }
}

function getStepTargetSummaries(step) {
  const collector = step?.collector ?? null;
  const parameters = collector ?? step?.parameters ?? {};
  const targets = [];
  const addTarget = (key, label, icon, value) => {
    if (typeof value !== "string" || !value.trim()) return;
    targets.push({ key: `${key}:${value.trim()}`, label, icon, value: value.trim() });
  };

  if (collector?.type === "file_text" && typeof parameters.path === "string") {
    addTarget("file_text", TARGET_TYPE_INFO.file_text.label, TARGET_TYPE_INFO.file_text.icon, parameters.path);
  } else if (collector?.type === "file_stat" && typeof parameters.path === "string") {
    addTarget("file_stat", TARGET_TYPE_INFO.file_stat.label, TARGET_TYPE_INFO.file_stat.icon, parameters.path);
  } else if (collector?.type === "command" && Array.isArray(parameters.argv)) {
    addTarget("command", TARGET_TYPE_INFO.command.label, TARGET_TYPE_INFO.command.icon, formatCommandArguments(parameters.argv));
  } else if (collector?.type === "localhost_http" && typeof parameters.url === "string") {
    addTarget("localhost_http", TARGET_TYPE_INFO.localhost_http.label, TARGET_TYPE_INFO.localhost_http.icon, parameters.url);
  } else if (collector?.type === "peer_ping") {
    addTarget("peer_ping", TARGET_TYPE_INFO.peer_ping.label, TARGET_TYPE_INFO.peer_ping.icon, jt("targetPeerObserved"));
  } else {
    if (Array.isArray(parameters.argv)) {
      addTarget("legacy-command", TARGET_TYPE_INFO.command.label, TARGET_TYPE_INFO.command.icon, formatCommandArguments(parameters.argv));
    }
    if (typeof parameters.path === "string") {
      addTarget("legacy-path", jt("chipPath"), TARGET_TYPE_INFO.file_stat.icon, parameters.path);
    }
    if (typeof parameters.url === "string") {
      addTarget("legacy-url", "URL", TARGET_TYPE_INFO.localhost_http.icon, parameters.url);
    }
  }

  return targets;
}

function getRubricTargetSummaries(item) {
  const seen = new Set();
  return (Array.isArray(item?.check_steps) ? item.check_steps : [])
    .flatMap((step) => getStepTargetSummaries(step))
    .filter((target) => {
      if (seen.has(target.key)) return false;
      seen.add(target.key);
      return true;
    });
}

/** 以班級 API 回傳的 machine_nodes 對照 rubric 的 logical target_node_key。 */
function getExecutionNodeSummary(item, machineNodes = []) {
  const targetKey = String(item?.target_node_key ?? "").trim();
  const hasExecutableSteps = Array.isArray(item?.check_steps) && item.check_steps.length > 0;
  if (!targetKey && !hasExecutableSteps) return null;

  const node = Array.isArray(machineNodes)
    ? machineNodes.find((entry) => String(entry?.node_key ?? "").trim() === targetKey)
    : null;
  if (!targetKey) {
    return { label: jt("nodeUnassigned"), detail: jt("nodeUnassignedDetail") };
  }
  if (!node) {
    return { label: jt("nodeNotFound"), detail: jt("nodeNotFoundDetail") };
  }

  const sortOrder = Number(node.sort_order);
  const displayLabel = node.display_label
    ?? (Number.isFinite(sortOrder) ? `P${sortOrder + 1}` : null);
  const name = String(node.name ?? node.node_name ?? "").trim();
  const label = [name || jt("nodeUnnamed"), displayLabel ? jt("nodeDisplayLabel", { label: displayLabel }) : ""].join("");
  const detail = [
    node.role ? jt("nodeRole", { value: node.role }) : "",
    node.resource_type ? jt("nodeType", { value: String(node.resource_type).toUpperCase() }) : "",
    node.template_name ? jt("nodeImage", { value: node.template_name }) : "",
  ].filter(Boolean).join(" · ");
  return { label, detail };
}

/** 提案列的唯讀指令預覽；以分號串接多個步驟的 argv。 */
function proposalCommandPreview(item) {
  const steps = Array.isArray(item?.check_steps) ? item.check_steps : [];
  return joinClauses(steps
    .map((step) => (Array.isArray((step?.collector ?? step?.parameters)?.argv)
      ? (step.collector ?? step.parameters).argv
        .filter((part) => typeof part === "string" && part.trim()).join(" ")
      : ""))
    .filter(Boolean));
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
  if (pendingProposal) return jt("blockerProposal");
  if (items.length === 0) return jt("blockerNoItems");
  const reviewIds = getRubricReviewItemIds(analysis, pendingReviewIds);
  if (reviewIds.size > 0) {
    return jt("blockerNeedsReview");
  }
  // 舊格式交由後端核對轉換；前端不再另寫一套指令／路徑就緒規則。
  if (items.some((item) => item.check_steps?.some((step) => (
    !step.collector || step.collector.cwd != null
  )))) {
    return jt("blockerNeedsReview");
  }
  const unsupportedCount = items.filter((item) => item.detectable === "manual").length;
  const missingCount = items.filter((item) => (
    item.detectable === "partial"
    || (item.detectable === "auto" && (
      !item.detection_method?.trim()
      || !Array.isArray(item.check_steps)
      || item.check_steps.length === 0
    ))
  )).length;
  if (missingCount || unsupportedCount) {
    const details = joinList([
      missingCount ? jt("blockerMissingCount", { count: missingCount }) : null,
      unsupportedCount ? jt("blockerManualCount", { count: unsupportedCount }) : null,
    ].filter(Boolean));
    return jt("blockerDetails", { details });
  }
  return null;
}

const RUBRIC_FILE_EXTENSION = /\.(?:md|txt|doc|docx|pdf)$/i;

/**
 * 檢查表的可讀名稱不應把匯入文件的副檔名帶進工作區標題；原始檔名仍
 * 保留在 `original_filename`，供衝突判斷與下載使用。
 */
export function getRubricDisplayName(file, fallback = jt("defaultRubricName")) {
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

/** 選到的週次還沒有檢查：一週只會有一份，按下去才建立（重複按也只會有一份）。 */
export function EmptyWeekHero({ week, onCreate, busy = false }) {
  const { t } = useTranslation("teaching");
  return (
    <EmptyState
      icon="checklist"
      title={t("AiJudgePanel.emptyWeekTitle", { week: week.week ?? week.week_number })}
      description={t("AiJudgePanel.emptyWeekDesc")}
      action={onCreate && (
        <button type="button" className={styles.btnPrimary} disabled={busy} onClick={onCreate}>
          {busy ? <Spinner size={15} /> : <MIcon name="add" size={16} />}
          {busy ? t("AiJudgePanel.creating") : t("AiJudgePanel.createWeekCheckBtn")}
        </button>
      )}
    />
  );
}

function proposalOperationLabel(item) {
  const operation = item.operation ?? item.action;
  if (operation === "delete" || operation === "remove") return jt("opDelete");
  if (operation === "update" || operation === "modify") return jt("opUpdate");
  if (operation === "add" || operation === "create") return jt("opAdd");
  return item.id ? jt("opUpdate") : jt("opAdd");
}

function detectionFields(item) {
  return {
    checked: Boolean(item.checked),
    detectable: item.detectable ?? "manual",
    judgement_mode: item.judgement_mode ?? "ai",
    target_node_key: item.target_node_key ?? null,
    peer_node_key: item.peer_node_key ?? null,
    detection_method: item.detection_method ?? null,
    fallback: item.fallback ?? null,
    missing_information: item.missing_information ?? [],
    check_steps: item.check_steps ?? [],
  };
}

/** AI 提案差異：名稱也算（AI 改名要讓老師看得到） */
function comparableItem(item) {
  return JSON.stringify({ title: item.title ?? "", ...detectionFields(item) });
}

/** 自動檢測支援：只改檢查點名稱不影響判斷，不用標成待更新 */
function detectionComparableItem(item) {
  return JSON.stringify(detectionFields(item));
}

/** 只比較會影響自動檢測支援判斷的檢查項目內容。 */
export function getRubricItemsValue(analysis) {
  const items = Array.isArray(analysis?.items) ? analysis.items : [];
  return JSON.stringify(items.map((item) => ({
    id: item.id ?? "",
    value: detectionComparableItem(item),
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
  const savedById = new Map(saved.filter((item) => item?.id).map((item) => [item.id, detectionComparableItem(item)]));
  const currentIds = new Set(current.filter((item) => item?.id).map((item) => item.id));
  const next = new Set(previousIds ?? []);

  current.forEach((item) => {
    if (!item?.id) return;
    const savedValue = savedById.get(item.id);
    if (savedValue === undefined || savedValue !== detectionComparableItem(item)) {
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

/** 只恢復最新一輪、仍符合目前來源版本且未被忽略的提案。 */
export function recoverSessionProposal(messages, sourceFileId, analysisRevision) {
  const latest = [...messages].reverse().find((message) => (
    message.role === "user" || message.role === "assistant"
  ));
  const metadata = latest?.metadata_json;
  if (!latest?.id || latest?.role !== "assistant" || metadata?.proposal_dismissed
    || metadata?.source_file_id !== sourceFileId
    || metadata?.analysis_revision !== analysisRevision
    || !Array.isArray(metadata?.rubric_proposal) || !metadata.rubric_proposal.length) return null;
  return { ...metadata, messageId: latest.id };
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
  needs_information: { get label() { return jt("detectablePartial"); }, className: styles.detBadge_partial },
  unsupported: { get label() { return jt("itemUnsupported"); }, className: styles.detBadge_manual },
  analysis_error: { get label() { return jt("itemAnalysisError"); }, className: styles.detBadge_manual },
};

export function ProposalPanel({ proposal, selectedIds, onToggle, onApply, onSkip, disabled, isRefine = false, itemResults = null }) {
  const { t } = useTranslation("teaching");
  const contentId = useId();
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    setExpanded(true);
  }, [proposal, itemResults]);

  if (!proposal?.length) return null;
  const results = Array.isArray(itemResults) && itemResults.length ? itemResults : null;
  const proposalById = new Map(proposal.map((item, index) => [item.id ?? `proposal-${index}`, item]));
  return (
    <section className={styles.proposalPreview} aria-label={t("AiJudgePanel.proposalAria")} aria-live="polite">
      <button
        type="button"
        className={styles.proposalToggle}
        aria-expanded={expanded}
        aria-controls={contentId}
        onClick={() => setExpanded((current) => !current)}
      >
        <span className={styles.proposalHeading}>
          <strong>{isRefine ? t("AiJudgePanel.proposalRefineTitle") : t("AiJudgePanel.proposalTitle")}</strong>
          <small>
            {t(isRefine ? "AiJudgePanel.proposalCountRefine" : "AiJudgePanel.proposalCountReady", { count: proposal.length, selected: selectedIds.size })}
          </small>
        </span>
        <span className={styles.proposalToggleAction}>
          {expanded ? t("AiJudgePanel.collapse") : t("AiJudgePanel.expand")}
          <MIcon name={expanded ? "expand_less" : "expand_more"} size={18} />
        </span>
      </button>
      {expanded && (
        <div id={contentId} className={styles.proposalContent}>
          <p className={styles.proposalDescription}>
            {isRefine ? t("AiJudgePanel.proposalRefineDesc") : t("AiJudgePanel.proposalDesc")}
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
                          <b>{result.source_label ? `${result.source_label}·` : ""}{item.title || t("AiJudgePanel.unnamedItem")}</b>
                          <small><em>{proposalOperationLabel(item)}</em>{t("AiJudgePanel.proposalItemHint")}</small>
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
                    ? joinList(gaps)
                    : (result.status === "unsupported" ? result.detail || "" : "");
                  return (
                    <div className={styles.proposalRow} key={`${result.source_index ?? index}-${result.title ?? ""}`}>
                      <span className={`${styles.detBadge} ${info.className}`}>
                        <MIcon name={result.status === "needs_information" ? "warning_amber" : "cancel"} size={16} aria-hidden="true" />
                        <span>{info.label}</span>
                      </span>
                      <span>
                        <b>{result.source_label ? `${result.source_label}·` : ""}{result.title || t("AiJudgePanel.unnamedItem")}</b>
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
                        <b>{item.title || t("AiJudgePanel.unnamedItem")}</b>
                        <small><em>{proposalOperationLabel(item)}</em>{t("AiJudgePanel.proposalItemHint")}</small>
                        {commandPreview && (
                          <code className={styles.proposalCommandPreview}>{commandPreview}</code>
                        )}
                      </span>
                    </label>
                  );
                })}
          </div>
          <div className={styles.proposalActions}>
            {(disabled || selectedIds.size === 0) && (
              <p className={styles.actionHint}>
                {disabled ? t("AiJudgePanel.applyBusyHint") : t("AiJudgePanel.applyNoneHint")}
              </p>
            )}
            <button type="button" className={styles.btnSecondary} disabled={disabled} onClick={onSkip}>{t("AiJudgePanel.ignoreBtn")}</button>
            <button type="button" className={styles.btnPrimary} disabled={disabled || selectedIds.size === 0} onClick={onApply}>{t("AiJudgePanel.applyBtn")}</button>
          </div>
        </div>
      )}
    </section>
  );
}

/* ── 可編輯檢查項目表格 ───────────────────────────────── */

function DetectabilityBadge({ detectable, judgementMode = "ai", needsReview = false }) {
  const { t } = useTranslation("teaching");
  const detectableInfo = needsReview || detectable === "partial"
    ? DETECTABLE_INFO.partial
    : judgementMode === "teacher"
      ? TEACHER_REVIEW_INFO
      : getDetectableInfo(detectable);
  return (
    <span
      className={`${styles.detBadge} ${detectableInfo.className}`}
      title={needsReview ? t("AiJudgePanel.needsReviewTitle") : detectableInfo.label}
    >
      <MIcon name={detectableInfo.icon} size={16} aria-hidden="true" />
      <span>{detectableInfo.label}</span>
      {needsReview && <em>{t("AiJudgePanel.needsReviewTag")}</em>}
    </span>
  );
}

/* ── 檢查表欄位（TanStack Table）──
   v9 的 header／cell 會被當元件掛載，欄位定義放模組層級才不會每次 render 整格重新掛載
   （標題 textarea 打字時不會失焦）。展開狀態在 RubricTableRow，經 rowState 交給各欄 */
const rubricColumnHelper = createColumnHelper();
const RUBRIC_TH_PROPS = Object.freeze({ scope: "col" });

function useRubricHeaders() {
  const { t } = useTranslation("teaching");
  return [
    <>#<span className={styles.srOnly}>{t("AiJudgePanel.thDetails")}</span></>,
    t("AiJudgePanel.thCheckpoint"),
    t("AiJudgePanel.thMethod"),
    t("AiJudgePanel.thAutoSupport"),
    <span className={styles.srOnly}>{t("AiJudgePanel.thActions")}</span>,
  ];
}

function RubricHeader({ index }) {
  return useRubricHeaders()[index];
}

function RubricIndexCell({ index, expanded, detailId, onToggle }) {
  const { t } = useTranslation("teaching");
  return (
    <div className={styles.rubricIndex}>
      <span className={styles.rubricNumber}>{index + 1}</span>
      <button
        type="button"
        className={styles.detailToggle}
        aria-expanded={expanded}
        aria-controls={detailId}
        aria-label={t(expanded ? "AiJudgePanel.collapseRowAria" : "AiJudgePanel.expandRowAria", { index: index + 1 })}
        title={expanded ? t("AiJudgePanel.collapseRowTitle") : t("AiJudgePanel.expandRowTitle")}
        onClick={onToggle}
      >
        <MIcon name={expanded ? "expand_less" : "expand_more"} size={17} aria-hidden="true" />
      </button>
    </div>
  );
}

function RubricTitleCell({ item, index, disabled, onChange }) {
  const { t } = useTranslation("teaching");
  return (
    <label className={styles.tableField}>
      <span className={styles.srOnly}>{t("AiJudgePanel.rowTitleLabel", { index: index + 1 })}</span>
      <textarea
        rows={2}
        className={styles.rubricTitleInput}
        value={item.title}
        onChange={(event) => onChange({ ...item, title: event.target.value })}
        placeholder={t("AiJudgePanel.rowTitlePlaceholder")}
        disabled={disabled}
      />
    </label>
  );
}

function RubricMethodCell({ item, index }) {
  const { t } = useTranslation("teaching");
  const targetSummaries = getRubricTargetSummaries(item);
  return (
    <div className={styles.tableField}>
      <span className={styles.srOnly}>{t("AiJudgePanel.rowMethodLabel", { index: index + 1 })}</span>
      <p className={`${styles.rubricMethodText} ${!item.detection_method ? styles.rubricMethodTextEmpty : ""}`}>
        {item.detection_method || t("AiJudgePanel.noMethod")}
      </p>
      {targetSummaries.length > 0 && (
        <div className={styles.rubricTargetSummary} aria-label={t("AiJudgePanel.targetsLabel")}>
          <div className={styles.rubricTargetItems}>
            {targetSummaries.map((target) => (
              <span
                key={target.key}
                className={styles.rubricTargetItem}
                title={t("AiJudgePanel.labelValue", { label: target.label, value: target.value })}
              >
                <MIcon name={target.icon} size={14} aria-hidden="true" />
                <span>{target.label}</span>
                <code>{target.value}</code>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function RubricDeleteCell({ item, index, disabled, onDelete }) {
  const { t } = useTranslation("teaching");
  return (
    <div className={styles.tableActions}>
      <button
        type="button"
        className={styles.rubricDeleteBtn}
        title={t("AiJudgePanel.deleteItemTitle")}
        aria-label={t("AiJudgePanel.deleteItemAria", { index: index + 1, title: item.title || t("AiJudgePanel.unnamedItem") })}
        onClick={onDelete}
        disabled={disabled}
      >
        <MIcon name="delete" size={16} />
      </button>
    </div>
  );
}

const rubricColumns = rubricColumnHelper.columns([
  /* 序號和展開鈕同一格：省下一整欄給檢查點與檢測方式 */
  rubricColumnHelper.display({
    id: "index",
    header: () => <RubricHeader index={0} />,
    cell: ({ rowState }) => (
      <RubricIndexCell
        index={rowState.index}
        expanded={rowState.expanded}
        detailId={rowState.detailId}
        onToggle={rowState.onToggle}
      />
    ),
    meta: { thProps: RUBRIC_TH_PROPS, tdClassName: styles.rubricDetailToggleCell },
  }),
  rubricColumnHelper.accessor("title", {
    header: () => <RubricHeader index={1} />,
    cell: ({ row, rowState }) => (
      <RubricTitleCell item={row.original} index={rowState.index} disabled={rowState.disabled} onChange={rowState.onChange} />
    ),
    meta: { thProps: RUBRIC_TH_PROPS, tdClassName: styles.rubricTitleCell },
  }),
  /* data-label 是窄螢幕卡片版的欄名；寫成函式，DataTableCells 切換語系重繪時才會拿到新語系的文字 */
  rubricColumnHelper.accessor("detection_method", {
    header: () => <RubricHeader index={2} />,
    cell: ({ row, rowState }) => <RubricMethodCell item={row.original} index={rowState.index} />,
    meta: {
      thProps: RUBRIC_TH_PROPS,
      tdClassName: styles.rubricDescriptionCell,
      tdProps: () => ({ "data-label": jt("thMethod") }),
    },
  }),
  rubricColumnHelper.accessor("detectable", {
    header: () => <RubricHeader index={3} />,
    cell: ({ row, rowState }) => (
      <DetectabilityBadge
        detectable={row.original.detectable}
        judgementMode={row.original.judgement_mode}
        needsReview={rowState.needsReview}
      />
    ),
    meta: { thProps: RUBRIC_TH_PROPS, tdClassName: styles.rubricDetectabilityCell },
  }),
  rubricColumnHelper.display({
    id: "actions",
    header: () => <RubricHeader index={4} />,
    cell: ({ row, rowState }) => (
      <RubricDeleteCell item={row.original} index={rowState.index} disabled={rowState.disabled} onDelete={rowState.onDelete} />
    ),
    meta: { thProps: RUBRIC_TH_PROPS, tdClassName: styles.rubricActionsCell },
  }),
]);

/* row 是 TanStack 的列，item 是它的原始資料（一併傳進來，編譯後的元件才會跟著資料重繪） */
function RubricTableRow({ row, item, index, onChange, onDelete, disabled, needsReview, machineNodes = [] }) {
  const { t } = useTranslation("teaching");
  const [expanded, setExpanded] = useState(false);
  const checkSteps = item.check_steps ?? [];
  const executionNode = getExecutionNodeSummary(item, machineNodes);
  const detailId = `rubric-detail-${index}`;
  const missingInformation = Array.isArray(item.missing_information)
    ? item.missing_information.filter(Boolean)
    : [];
  const hasDetails = Boolean(
    item.detection_method || item.fallback || checkSteps.length || missingInformation.length,
  );
  const rowState = {
    index,
    expanded,
    detailId,
    onToggle: () => setExpanded((current) => !current),
    onChange,
    onDelete,
    disabled,
    needsReview,
  };

  return (
    <>
      <tr className={`${styles.rubricTableRow} ${expanded ? styles.rubricTableRowExpanded : ""}`}>
        <DataTableCells row={row} rowState={rowState} />
      </tr>
      {expanded && (
        <tr className={styles.rubricDetailRow}>
          <td id={detailId} colSpan={rubricColumns.length}>
            <div className={styles.rubricDetail}>
              <div className={styles.rubricDetailHead}>
                <div>
                  <strong>{t("AiJudgePanel.detailTitle")}</strong>
                  <span>{t("AiJudgePanel.detailDesc")}</span>
                </div>
              </div>
              {!hasDetails ? (
                <p className={styles.rubricDetailEmpty}>{t("AiJudgePanel.detailEmpty")}</p>
              ) : (
                <div className={styles.detectGrid}>
                  {item.detectable === "partial" && (
                    <div className={`${styles.detectItem} ${styles.detectItemWide}`}>
                      <span>{t("AiJudgePanel.detectablePartial")}</span>
                      <p>{missingInformation.length
                        ? joinList(missingInformation)
                        : t("AiJudgePanel.detailMissingFallback")}</p>
                    </div>
                  )}
                  {item.fallback && (
                    <div className={styles.detectItem}>
                      <span>{t("AiJudgePanel.detailFallback")}</span>
                      <p>{item.fallback}</p>
                    </div>
                  )}
                  {checkSteps.length > 0 && (
                    <div className={`${styles.detectItem} ${styles.detectItemWide}`}>
                      <span>{t("AiJudgePanel.detailSteps")}</span>
                      <div className={styles.executionContract} aria-label={t("AiJudgePanel.contractTitle")}>
                        <span className={styles.executionContractTitle}>
                          <MIcon name="rule" size={14} aria-hidden="true" />
                          {t("AiJudgePanel.contractTitle")}
                        </span>
                        {executionNode && (
                          <span className={styles.executionContractItem}>
                            <span>{t("AiJudgePanel.contractNode")}</span>
                            <strong>{executionNode.label}</strong>
                            {executionNode.detail && <small>{executionNode.detail}</small>}
                          </span>
                        )}
                        <span className={styles.executionContractItem}>
                          <span>{t("AiJudgePanel.contractJudgement")}</span>
                          <strong>{item.judgement_mode === "teacher" ? t("AiJudgePanel.judgeTeacher") : t("AiJudgePanel.judgeSystem")}</strong>
                        </span>
                      </div>
                      <div className={styles.stepPlanList}>
                        {checkSteps.map((step, stepIndex) => (
                          <div
                            key={`${step.template_key}-${step.command_key}-${stepIndex}`}
                            className={styles.stepPlanRow}
                          >
                            <span className={styles.chip}>
                              {step.collector
                                ? t("AiJudgePanel.managedCollector", { type: step.collector.type })
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
                            {getAssertionSummary(step.assertion) && (
                              <span className={styles.chip}>
                                <span className={styles.chipLabel}>{t("AiJudgePanel.passCondition")}</span>
                                <span className={styles.chipText}>{getAssertionSummary(step.assertion)}</span>
                              </span>
                            )}
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

export function RubricTable({ items, onChange, onDelete, disabled, needsReviewIds, machineNodes = [] }) {
  const { t } = useTranslation("teaching");
  const reviewIds = needsReviewIds instanceof Set
    ? needsReviewIds
    : new Set(Array.isArray(needsReviewIds) ? needsReviewIds : []);
  const table = useTable({
    features: baseFeatures,
    columns: rubricColumns,
    data: items,
    getRowId: (item, index) => String(item.id ?? index),
  });
  return (
    <div className={styles.rubricTableWrap}>
      {/* 表格外框自己組：<caption> 要排在 <thead> 前面，共用的 DataTable 沒有這個位置；表頭用 DataTableHead */}
      <table className={styles.rubricTable}>
        <caption className={styles.srOnly}>{t("AiJudgePanel.tableCaption")}</caption>
        <DataTableHead table={table} />
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <RubricTableRow
              key={row.id}
              row={row}
              item={row.original}
              index={row.index}
              onChange={(updated) => onChange(row.index, updated)}
              onDelete={() => onDelete(row.index)}
              disabled={disabled}
              needsReview={reviewIds.has(row.original.id)}
              machineNodes={machineNodes}
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
    if (call.resolved) return;
    if (call.status === "staged") {
      const label =
        call.operation === "update"
          ? jt("toolProposalUpdated")
          : jt("toolProposalCreated");
      lines.push({ icon: "check_circle", text: jt("labelValue", { label, value: call.title ?? "" }) });
    } else if (call.status === "rejected" || call.status === "error") {
      lines.push({
        icon: "cancel",
        text: jt("toolProposalRejected", { title: call.title ?? "" }),
      });
    } else if (call.status === "no_change") {
      lines.push({
        icon: "info",
        text: jt("toolProposalNoChange", { title: call.title ?? "" }),
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

/** 待送出的附件列：沒有附件時不畫；onRemove 沒給就不顯示移除鈕 */
function ChatAttachmentRail({ attachments, onRemove, disabled }) {
  const { t } = useTranslation("teaching");
  if (!attachments.length) return null;
  return (
    <div className={styles.chatAttachmentRail} aria-label={t("AiJudgePanel.pendingAttachmentsAria")}>
      {attachments.map((attachment) => (
        <div key={attachment.id} className={styles.chatAttachmentChip}>
          <MIcon name="description" size={15} />
          <span title={attachment.original_filename}>{attachment.original_filename}</span>
          <small>{attachment.status === "ready" ? t("AiJudgePanel.attachmentReady") : t("AiJudgePanel.attachmentProcessing")}</small>
          {onRemove && <button
            type="button"
            className={styles.chatAttachmentRemove}
            aria-label={t("AiJudgePanel.removeAttachmentAria", { name: attachment.original_filename })}
            disabled={disabled}
            onClick={() => onRemove(attachment)}
          >
            <MIcon name="close" size={14} />
          </button>}
        </div>
      ))}
    </div>
  );
}

export function ChatPanel({
  messages,
  onSendMessage,
  onClearMessages = () => {},
  isLoading,
  isClearing = false,
  disabled = false,
  onToggleSources,
  sourcesOpen = false,
  sourcesContent,
  pendingAttachments = [],
  onRemoveAttachment,
  onUploadFile,
  isUploading = false,
  loadingText = "",
  onDraftChange,
  title,
  subtitle,
}) {
  const { t } = useTranslation("teaching");
  const [input, setInput] = useState("");
  const fileInputRef = useRef(null);
  const textareaRef = useRef(null);
  const messagesEndRef = useRef(null);
  const visibleMessages = messages.filter(shouldDisplayChatMessage);
  const hasDraft = Boolean(input.trim());

  useEffect(() => {
    onDraftChange?.(hasDraft);
  }, [hasDraft, onDraftChange]);

  // 輸入框從一行開始，隨內容長高；到上限（.chatComposer textarea 的 max-height）就停，
  // 之後在框內捲動。「＋」與送出鈕才會跟第一行文字對齊
  const resizeComposer = useCallback(() => {
    const textarea = textareaRef.current;
    // 看不見的時候（所在分頁隱藏）量到的高度是 0，等看得到再量
    if (!textarea || textarea.offsetParent === null) return;
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  }, []);

  useLayoutEffect(() => {
    resizeComposer();
  }, [input, resizeComposer]);

  // 拖拉欄寬、縮放視窗會改變換行；從隱藏變成看得到也算寬度改變。寬度變了就重新量
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea || typeof ResizeObserver === "undefined") return undefined;
    let width = textarea.clientWidth;
    const observer = new ResizeObserver(() => {
      if (textarea.clientWidth === width) return;
      width = textarea.clientWidth;
      resizeComposer();
    });
    observer.observe(textarea);
    return () => observer.disconnect();
  }, [resizeComposer]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  async function send() {
    const content = input.trim();
    if ((!content && !pendingAttachments.length) || isLoading || isClearing || isUploading || disabled) return;
    setInput("");
    // 呼叫端明確回傳 false（尚未載入檢查表、自動儲存失敗等）代表訊息沒送出：把原文放回輸入框，不要默默吃掉。
    Promise.resolve(onSendMessage(content, false, pendingAttachments))
      .then((accepted) => {
        if (accepted === false) setInput((current) => current || content);
      })
      .catch(() => {});
  }

  function handleAttachmentInput(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) onUploadFile?.(file);
  }

  const canInteract = !(isLoading || isClearing || isUploading || disabled);
  const canSend = canInteract && (Boolean(input.trim()) || pendingAttachments.length > 0);
  // 整個對話區都能把文件拖進來；跟輸入框旁的＋一樣一次加一個
  const { dragging, dropProps } = useFileDrop(([file]) => onUploadFile?.(file), {
    disabled: !canInteract,
  });
  // 空對話時問候貼在輸入框上方（視線本來就在那裡），跟 AI 導覽同一種排法；
  // 有沒有訊息都用同一個圓角輸入框，不再切換兩套版面。
  const isEmpty = visibleMessages.length === 0 && !isLoading;

  const attachmentRail = (
    <ChatAttachmentRail attachments={pendingAttachments} onRemove={onRemoveAttachment} disabled={!canInteract} />
  );
  const sourcesPanel = sourcesOpen && sourcesContent && (
    <div id="ai-chat-data-sources" className={styles.chatSourcesPanel}>
      {sourcesContent}
    </div>
  );
  const showClear = !isEmpty;

  return (
    <div className={styles.chatPanel} {...(onUploadFile ? dropProps : {})}>
      {(title || onToggleSources || showClear) && (
        <div className={styles.chatHeader}>
          {title && (
            <>
              <span className={styles.chatHeaderAvatar}>
                <OctoAvatar />
              </span>
              <div className={styles.chatHeaderText}>
                <strong>{title}</strong>
                {subtitle && <span>{subtitle}</span>}
              </div>
            </>
          )}
          <div className={styles.chatHeaderActions}>
            {onToggleSources && (
              <button
                type="button"
                className={`${styles.chatHeaderBtn} ${sourcesOpen ? styles.chatHeaderBtnActive : ""}`}
                disabled={!canInteract}
                onClick={onToggleSources}
                aria-expanded={sourcesOpen}
                aria-controls="ai-chat-data-sources"
                aria-label={t("AiJudgePanel.sourcesBtn")}
                title={t("AiJudgePanel.sourcesBtn")}
              >
                <MIcon name="description" size={18} />
              </button>
            )}
            {showClear && (
              <button
                type="button"
                className={styles.chatHeaderBtn}
                disabled={!canInteract || messages.length === 0}
                onClick={onClearMessages}
                aria-label={t("AiJudgePanel.clearChatBtn")}
                title={t("AiJudgePanel.clearChatBtn")}
              >
                {isClearing ? <Spinner size={16} /> : <MIcon name="delete_sweep" size={18} />}
              </button>
            )}
          </div>
        </div>
      )}

      <div className={styles.chatMessages}>
        {isEmpty ? (
          <div className={styles.chatGreeting} data-chat-empty-hero="true">
            <h3>{t("AiJudgePanel.chatHeroTitle")}</h3>
            <p>{t("AiJudgePanel.chatHeroDesc")}</p>
          </div>
        ) : (
          visibleMessages.map((msg, i) => (
            <div
              key={`${msg.role}-${i}`}
              className={`${styles.chatMsgRow} ${msg.role === "user" ? styles.chatMsgRow_user : ""}`}
            >
              {/* 跟 AI 導覽同一套：助理有頭像、回覆不加框；使用者是靠右的實心氣泡、不放頭像 */}
              {msg.role === "assistant" && (
                <span className={styles.chatAvatar}>
                  <OctoAvatar />
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
                      <ul className={styles.chatToolCallList} aria-label={t("AiJudgePanel.toolResultsAria")}>
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
            </div>
          ))
        )}

        {isLoading && (
          <div className={styles.chatMsgRow}>
            <span className={styles.chatAvatar}>
              <OctoAvatar />
            </span>
            <div className={styles.chatThinking}>
              <span className={styles.chatPulse} />
              {loadingText || t("AiJudgePanel.chatThinking")}
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className={styles.chatComposerDock}>
        {sourcesPanel}
        {attachmentRail}
        <form
          className={styles.chatComposer}
          aria-label={t("AiJudgePanel.chatFormAria")}
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
                className={styles.chatComposerAttach}
                disabled={!canInteract}
                aria-label={t("AiJudgePanel.addAttachmentAria")}
                title={t("AiJudgePanel.attachFileTitle")}
                onClick={() => fileInputRef.current?.click()}
              >
                <MIcon name="add" size={20} />
              </button>
            </>
          )}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              // 注音、倉頡選字時按 Enter 是確認選字，不是送出
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={isEmpty ? t("AiJudgePanel.chatHeroPlaceholder") : t("AiJudgePanel.chatInputPlaceholder")}
            rows={1}
            aria-label={t("AiJudgePanel.chatInputAria")}
            disabled={!canInteract}
          />
          <button
            type="submit"
            className={styles.chatComposerSend}
            disabled={!canSend}
            aria-label={t("AiJudgePanel.sendAria")}
            title={t("AiJudgePanel.sendAria")}
          >
            <MIcon name="arrow_upward" size={18} />
          </button>
        </form>
      </div>
      {dragging && <FileDropOverlay label={t("AiJudgePanel.dropToAttach")} />}
    </div>
  );
}

export function SaveAndCreateAction({
  onClick,
  onBlocked,
  disabled = false,
  blocker = null,
  hint = null,
  isProcessing = false,
  status = null,
}) {
  const { t } = useTranslation("teaching");
  const label = {
    saving: t("AiJudgePanel.saveStatusSaving"),
    reviewing: t("AiJudgePanel.saveStatusReviewing"),
    queued: t("AiJudgePanel.saveStatusQueued"),
    generating: t("AiJudgePanel.saveStatusGenerating"),
  }[status] ?? t("AiJudgePanel.saveAndBuildBtn");
  return (
    <div className={styles.rubricActionBar} data-guide="ai-judge-save-build">
      <p className={hint || blocker ? styles.rubricActionHintBlocked : undefined}>
        {(hint || blocker) && <MIcon name="info" size={15} aria-hidden="true" />}
        {hint || blocker || t("AiJudgePanel.saveAndBuildHint")}
      </p>
      {/* 前置條件沒完成（blocker）時不設 disabled：停用的按鈕點不到也聚焦不到，
          使用者不知道為什麼不能按。改成外觀停用（aria-disabled），點了由 onBlocked 說明原因 */}
      <button
        type="button"
        className={`${styles.btnPrimary} ${isProcessing ? styles.btnPrimaryProcessing : ""}`}
        disabled={disabled || isProcessing}
        aria-disabled={blocker ? "true" : undefined}
        onClick={() => (blocker ? onBlocked?.(blocker) : onClick())}
        title={blocker || undefined}
        aria-busy={isProcessing}
        data-generation-status={status || undefined}
      >
        {isProcessing ? <Spinner size={14} /> : <MIcon name="code" size={14} />}
        {label}
      </button>
    </div>
  );
}

/* ── 工作區欄寬：週次欄、助理欄可以拖拉調整 ──────────────── */

const JUDGE_MAIN_MIN_WIDTH = 480;
const RESIZE_KEY_STEP = 16;

export const JUDGE_COLUMN_WIDTHS = {
  week: { cssVar: "--judge-week-w", storageKey: "aiJudge.weekColumnWidth", min: 180, max: 360 },
  chat: { cssVar: "--judge-chat-w", storageKey: "aiJudge.chatColumnWidth", min: 300, max: 600 },
};

/** 上次拖拉出來的欄寬；瀏覽器不給讀就用預設寬度 */
export function readStoredColumnWidths() {
  const style = {};
  for (const { cssVar, storageKey, min, max } of Object.values(JUDGE_COLUMN_WIDTHS)) {
    try {
      const value = Number(window.localStorage.getItem(storageKey));
      if (Number.isFinite(value) && value >= min && value <= max) style[cssVar] = `${value}px`;
    } catch {
      // 私密瀏覽或封鎖儲存：照預設寬度
    }
  }
  return style;
}

/**
 * 欄與欄之間的拖拉把手（就是那條分隔線）。拖拉、左右鍵調整寬度，雙擊恢復預設。
 * 寬度寫在工作區根節點的 CSS 變數上，中間的檢查項目至少保留 480px。
 */
function ColumnResizer({ column, side, label, measureSelector, otherSelector }) {
  const ref = useRef(null);
  const { cssVar, storageKey, min, max } = JUDGE_COLUMN_WIDTHS[column];

  const host = () => ref.current?.closest("[data-judge-workspace]") ?? null;
  const widthOf = (root, selector) => (
    selector ? root.querySelector(selector)?.getBoundingClientRect().width ?? 0 : 0
  );

  function limits(root) {
    const room = root.getBoundingClientRect().width - widthOf(root, otherSelector) - JUDGE_MAIN_MIN_WIDTH;
    return { low: min, high: Math.max(min, Math.min(max, room)) };
  }

  function apply(root, width) {
    const { low, high } = limits(root);
    const next = Math.round(Math.min(high, Math.max(low, width)));
    root.style.setProperty(cssVar, `${next}px`);
    ref.current?.setAttribute("aria-valuenow", String(next));
    return next;
  }

  function save(width) {
    try {
      window.localStorage.setItem(storageKey, String(width));
    } catch {
      // 存不了就只在這次畫面有效
    }
  }

  function onPointerDown(event) {
    const root = host();
    if (!root || event.button !== 0) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = widthOf(root, measureSelector);
    let latest = startWidth;
    const target = event.currentTarget;
    target.setPointerCapture?.(event.pointerId);
    root.dataset.resizing = "true";
    function onMove(moveEvent) {
      const delta = moveEvent.clientX - startX;
      latest = apply(root, side === "left" ? startWidth + delta : startWidth - delta);
    }
    function onUp() {
      target.removeEventListener("pointermove", onMove);
      target.removeEventListener("pointerup", onUp);
      target.removeEventListener("pointercancel", onUp);
      delete root.dataset.resizing;
      save(latest);
    }
    target.addEventListener("pointermove", onMove);
    target.addEventListener("pointerup", onUp);
    target.addEventListener("pointercancel", onUp);
  }

  function onKeyDown(event) {
    const root = host();
    if (!root || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const grow = (event.key === "ArrowRight") === (side === "left");
    const width = widthOf(root, measureSelector) + (grow ? RESIZE_KEY_STEP : -RESIZE_KEY_STEP);
    save(apply(root, width));
  }

  function reset() {
    host()?.style.removeProperty(cssVar);
    try {
      window.localStorage.removeItem(storageKey);
    } catch {
      // 沒存過也沒關係
    }
  }

  return (
    <div
      ref={ref}
      className={`${styles.columnResizer} ${side === "left" ? styles.columnResizerWeek : styles.columnResizerChat}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      title={label}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={reset}
    />
  );
}

/* ── 週次：一週一個檢查對話 ───────────────────────────── */

/** 週次在清單、下拉選單裡的名稱：「第 N 週 · 主題」，沒填主題時註明 */
/** 老師發布這週內容後學生才看得到作業，也才能檢查。 */
export function isWeekPublished(week) {
  return week?.status === "published" || week?.status === "completed";
}

export function weekOptionLabel(week) {
  return jt("weekOption", {
    week: week.week ?? week.week_number,
    title: String(week.title ?? "").trim() || jt("weekNoTopic"),
  });
}

/** 移動或複製檢查時選目標週次：只列還沒有檢查的週次 */
function WeekSelectField({ weeks, value, onChange, autoFocus = false }) {
  const { t } = useTranslation("teaching");
  const selectId = useId();
  if (weeks.length === 0) {
    return (
      <p className={styles.dialogHint}>
        <MIcon name="info" size={15} aria-hidden="true" />
        {t("AiJudgePanel.noFreeWeeksHint")}
      </p>
    );
  }
  return (
    <label className={styles.dialogField} htmlFor={selectId}>
      <span>{t("AiJudgePanel.weekLabel")}</span>
      <select
        id={selectId}
        className={styles.dialogSelect}
        value={value}
        autoFocus={autoFocus}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="" disabled>{t("AiJudgePanel.weekSelect")}</option>
        {weeks.map((week) => (
          <option key={week.id} value={week.id}>{weekOptionLabel(week)}</option>
        ))}
      </select>
    </label>
  );
}

/** 預設從第一週開始；沒有第一週時選編號最小的現有週次。 */
export function getDefaultWeekId(weeks) {
  return [...weeks].sort((a, b) => (a.week_number ?? 0) - (b.week_number ?? 0))[0]?.id ?? "";
}

/**
 * 打開 AI導師檢查時先選哪一週：網址指定的有效週次 > 第一週。
 */
export function resolveActiveWeekId(requestedId, weeks) {
  if (!Array.isArray(weeks) || weeks.length === 0) return null;
  if (requestedId && weeks.some((week) => String(week.id) === String(requestedId))) {
    return String(requestedId);
  }
  const fallback = getDefaultWeekId(weeks);
  return fallback ? String(fallback) : null;
}

function RubricSourceRail({ classId, file, onClose, embedded = false }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();

  async function download() {
    try {
      const blob = await AiJudgeService.downloadFile(classId, file.id);
      downloadBlob(blob, file.original_filename ?? `${getRubricDisplayName(file)}.pdf`);
    } catch (error) {
      toast.error(error?.message ?? t("AiJudgePanel.downloadSourceFailed"));
    }
  }

  if (!file || file.source_type === "created") return null;
  return (
    <aside className={`${styles.sourceRail} ${embedded ? styles.sourceRailEmbedded : ""}`} aria-label={t("AiJudgePanel.sourcesBtn")}>
      <div className={styles.sourceRailHead}>
        <div>
          <h3>{t("AiJudgePanel.sourcesBtn")}</h3>
          <p>{t("AiJudgePanel.sourcesDesc")}</p>
        </div>
        <div className={styles.sourceRailActions}>
          {onClose && <button type="button" className={styles.iconBtn} aria-label={t("AiJudgePanel.closeSourcesAria")} title={t("AiJudgePanel.closeBtn")} onClick={onClose}><MIcon name="close" size={18} /></button>}
        </div>
      </div>
      <div className={`${styles.sourceRow} ${styles.sourceRowSelected}`}>
        <div className={styles.sourceSelect} aria-current="true">
          <span className={styles.sourceIndicator} aria-hidden="true"><MIcon name="description" size={17} /></span>
          <span className={styles.sourceText}>
            <b>{getRubricDisplayName(file, t("AiJudgePanel.unnamedRubric"))}</b>
            <small>{joinList((file.environment_keys?.length ? file.environment_keys : [file.template_key]).map(getTemplateLabel))} · {t("AiJudgePanel.itemCount", { count: file.analysis_json?.items?.length ?? 0 })} · {formatDateTime(file.updated_at)}</small>
          </span>
        </div>
        <button type="button" className={styles.iconBtn} aria-label={t("AiJudgePanel.downloadAria", { name: getRubricDisplayName(file) })} title={t("AiJudgePanel.downloadOriginal")} onClick={download}><MIcon name="download" size={18} /></button>
      </div>
    </aside>
  );
}

/* ── Tab 1：檢查表 ──────────────────────────────────────── */

export function RubricsTab({
  classId,
  judgeSession,
  onSessionUpdated,
  onScriptCreated,
  onDirtyChange,
  machineNodes = [],
}) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  /* 沒有檢查項目時，空狀態的「詢問檢查表助理」把焦點帶到右側助理的輸入框 */
  const chatColRef = useRef(null);
  // 製作腳本要等很久：途中切到別的檢查（這個分頁已卸載）就不能再把上層帶去腳本／核查分頁
  const mountedRef = useRef(true);
  const [chatHasDraft, setChatHasDraft] = useState(false);
  const judgeCache = useAiJudgeCacheUpdater();
  const sessionId = judgeSession?.id ?? null;
  const selectedFileId = judgeSession?.selected_file_id ?? null;

  /* 班級的檢查表清單：背景每 30 秒刷新；自動儲存寫回的較新版本不會被較早送出的重抓蓋掉 */
  const filesQuery = useAiJudgeFilesQuery(classId);
  const files = filesQuery.data ?? NO_RUBRIC_FILES;
  const filesLoaded = filesQuery.data !== undefined;
  const refetchFiles = filesQuery.refetch;
  const filesRequestedRef = useRef(false);

  const [analysis, setAnalysis] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [pendingAttachments, setPendingAttachments] = useState([]);
  const [isSendingMessage, setIsChatting] = useState(false);
  /* 對話紀錄：查詢快取就是畫面上的對話（送出中的訊息、AI 回覆、忽略提案都寫進快取）。
     伺服器還在處理上一輪時每 30 秒背景重抓；自己傳送中暫停，免得重抓蓋掉剛送出的訊息 */
  const messagesQuery = useAiJudgeSessionMessagesQuery(classId, sessionId, selectedFileId, {
    paused: isSendingMessage,
  });
  const messages = messagesQuery.data ?? NO_SESSION_MESSAGES;
  const processingMessage = getProcessingSessionMessage(messages);
  const isServerProcessing = Boolean(processingMessage
    && processingMessage.metadata_json.processing_deadline * 1000 > Date.now());
  const isChatting = isSendingMessage || isServerProcessing;
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
  const analysisRevisionsRef = useRef(new Map());
  const recoveredProposalIdRef = useRef(null);
  const lastSavedValuesRef = useRef(new Map());
  const lastSavedItemsRef = useRef(new Map());
  const lastSavedNeedsReviewRef = useRef(new Map());
  const pendingReviewIdsByFileRef = useRef(new Map());
  const [pendingReviewIds, setPendingReviewIds] = useState(() => new Set());
  const autosaveRef = useRef(null);
  // 自動儲存的狀態：unsaved＝最近一次儲存失敗（409 以外），畫面上的修改還沒存到伺服器
  const [autosaveStatus, setAutosaveStatus] = useState({
    unsaved: false, saving: false, error: null, autoRetryScheduled: false,
  });
  const classIdRef = useRef(classId);
  const activeContextRef = useRef(null);
  activeContextRef.current = `${classId}:${judgeSession?.id}:${judgeSession?.selected_file_id}`;
  const toastRef = useRef(toast);
  const selectedSource = useMemo(
    () => files.find((file) => file.id === sourceFileId) ?? null,
    [files, sourceFileId],
  );
  classIdRef.current = classId;
  toastRef.current = toast;

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  /* 改寫畫面上的對話（寫進這個檢查的對話快取）；非同步回來才寫的呼叫端都先用 activeContextRef
     確認還是同一個檢查與檢查表，跟原本的 setMessages 一樣 */
  function setMessages(next) {
    judgeCache.setSessionMessages(classId, sessionId, selectedFileId, next);
  }

  // 進來（或換檢查表）時讀對話失敗才提示；處理中的背景重抓失敗維持靜默
  useQueryErrorEffect(messagesQuery, () => {
    if (messagesQuery.data === undefined) toast.error(t("AiJudgePanel.loadChatFailed"));
  });

  function clearPendingProposal() {
    setPendingProposal(null);
    setSelectedProposalIds(new Set());
    setPendingProposalMeta(null);
    setPendingProposalIsRefine(false);
    setPendingItemResults(null);
  }

  // 忽略後提案就不見了，要再請 AI 產生一次，先確認
  async function skipPendingProposal() {
    const context = activeContextRef.current;
    const ok = await confirm({
      title: t("AiJudgePanel.ignoreProposalTitle"),
      message: t("AiJudgePanel.ignoreProposalMessage"),
      confirmText: t("AiJudgePanel.ignoreBtn"),
    });
    if (!ok || !mountedRef.current || context !== activeContextRef.current) return;
    try {
      const messageId = pendingProposalMeta?.messageId;
      if (messageId) {
        await AiJudgeService.dismissSessionProposal(classId, judgeSession.id, messageId);
        if (!mountedRef.current || context !== activeContextRef.current) return;
        setMessages((current) => current.map((message) => message.id === messageId
          ? { ...message, metadata_json: { ...message.metadata_json, proposal_dismissed: true } }
          : message));
      }
      clearPendingProposal();
    } catch {
      if (mountedRef.current && context === activeContextRef.current) {
        toast.error(t("AiJudgePanel.ignoreProposalFailed"));
      }
    }
  }

  /* 送訊息、製作腳本完成或失敗後的同步：呼叫端要知道同步成不成功（決定提示文字），
     merge 時還要跟畫面上的訊息合併，所以直接讀一次再寫進對話快取，不經查詢的重抓 */
  async function refreshSessionMessages({ silent = false, replace = false } = {}) {
    if (!judgeSession?.id) return false;
    const context = activeContextRef.current;
    try {
      const rows = await AiJudgeService.listSessionMessages(classId, judgeSession.id);
      if (!mountedRef.current || context !== activeContextRef.current) return false;
      setMessages((current) => (replace ? rows : mergeSessionMessages(current, rows)));
      return true;
    } catch (err) {
      if (!silent) toast.error(err?.message ?? t("AiJudgePanel.loadChatFailed"));
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
        judgeCache.updateFile(classIdRef.current, updated);
      },
      onError(error, { conflict, announce }) {
        if (conflict) {
          clearPendingProposal();
          toastRef.current.error(jt("rubricConflict"));
          // 別人改過了：靜默重抓清單換成伺服器上的版本（失敗就等背景刷新）
          void judgeCache.invalidateFiles(classIdRef.current);
          return;
        }
        // 其他失敗：修改保留在畫面上並標成未儲存（見上方提示），自動重試的失敗不再逐次跳通知
        if (announce) toastRef.current.error(error?.message ?? jt("updateRubricFailed"));
      },
      onStatusChange: setAutosaveStatus,
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

  /* 進到這個檢查、換了檢查表時向伺服器重抓一次清單（第一次併入掛載時的讀取，不多打一次）；
     這幾次讀取失敗才提示，背景刷新失敗維持靜默 */
  useEffect(() => {
    const options = filesRequestedRef.current ? undefined : { cancelRefetch: false };
    filesRequestedRef.current = true;
    void refetchFiles(options).then((result) => {
      if (result.isError) toastRef.current.error(jt("loadSourcesFailed"));
    });
  }, [refetchFiles, classId, selectedFileId]);

  /* 換了檢查或檢查表：對話換成新的 key 重新讀取（見 useAiJudgeSessionMessagesQuery），
     這裡清掉跟舊對話綁在一起的附件與提案 */
  useEffect(() => {
    recoveredProposalIdRef.current = null;
    setPendingAttachments([]);
    clearPendingProposal();
  }, [classId, sessionId, selectedFileId]);

  useEffect(() => {
    function clearSelectedSourceState() {
      setAnalysis(null);
      setScriptGenerationNotice(null);
      setSourceFileId(null);
      setPendingReviewIds(new Set());
      clearPendingProposal();
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
    analysisRevisionsRef.current.set(file.id, file.analysis_revision);
    lastSavedValuesRef.current.set(file.id, getRubricItemsValue(file.analysis_json));
    lastSavedItemsRef.current.set(file.id, Array.isArray(file.analysis_json.items) ? file.analysis_json.items : []);
    lastSavedNeedsReviewRef.current.set(file.id, Boolean(file.analysis_json.detectability_needs_review));
    const savedReviewIds = getRubricReviewItemIds(file.analysis_json);
    pendingReviewIdsByFileRef.current.set(file.id, savedReviewIds);
    setPendingReviewIds(savedReviewIds);
  }, [files, filesLoaded, judgeSession?.selected_file_id, sourceFileId]);

  useEffect(() => {
    const revision = analysisRevisionsRef.current.get(sourceFileId);
    if (pendingProposalMeta && (
      pendingProposalMeta.baseRevision !== revision
      || pendingProposalMeta.sourceFileId !== sourceFileId
    )) clearPendingProposal();
    if (!analysis || !sourceFileId || isSendingMessage || isCreatingScript || autosaveRef.current?.isPending()) return;
    const restored = recoverSessionProposal(messages, sourceFileId, revision);
    if (!restored || restored.messageId === recoveredProposalIdRef.current) return;
    recoveredProposalIdRef.current = restored.messageId;
    // A live response already supplied these values; preserve checkbox choices.
    if (pendingProposalMeta?.messageId === restored.messageId) return;
    const proposal = buildProposalDiff(analysis.items ?? [], restored.rubric_proposal);
    setPendingProposal(proposal.length ? proposal : null);
    setPendingItemResults(restored.item_results ?? null);
    setSelectedProposalIds(getSelectableProposalIds(proposal, restored.item_results));
    setPendingProposalMeta(proposal.length ? {
      messageId: restored.messageId, sourceFileId, baseRevision: revision,
    } : null);
    setPendingProposalIsRefine(Boolean(restored.is_refine));
  }, [messages, analysis, sourceFileId, selectedSource?.analysis_revision, pendingProposalMeta, isSendingMessage, isCreatingScript]);

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
    if (!RUBRIC_FILE_EXTENSION.test(file.name ?? "")) {
      toast.error(t("AiJudgePanel.attachmentTypeError"));
      return false;
    }
    if (pendingAttachments.length >= 5) {
      toast.error(t("AiJudgePanel.attachmentLimit", { count: 5 }));
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
      toast.error(err?.message ?? t("AiJudgePanel.attachmentReadFailed"));
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
      toast.error(err?.message ?? t("AiJudgePanel.attachmentRemoveFailed"));
    }
  }

  // 未取得成功回覆時保留輸入草稿；後端是否已收到須以同步後的訊息紀錄確認。
  async function handleSendMessage(content, isRefine = false, attachments = []) {
    const context = activeContextRef.current;
    if (!judgeSession?.id) return false;
    if (!analysis) {
      toast.error(t("AiJudgePanel.rubricStillLoading"));
      return false;
    }
    // 檢查表還有沒存成的修改就不送給助理：助理看到的是伺服器上的版本，會跟畫面對不起來。
    // 409 時 autosave 已經用 toast 說明原因
    const autosave = autosaveRef.current;
    if (autosave && !(await autosave.flush({ quiet: true }))) {
      if (autosave.hasUnsaved() && mountedRef.current) toast.error(t("AiJudgePanel.unsavedBlocksChat"));
      return false;
    }
    if (!mountedRef.current || context !== activeContextRef.current) return false;
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
      if (!mountedRef.current || context !== activeContextRef.current) return true;
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
      setPendingProposalMeta(proposal.length ? {
        messageId: response.assistant_message?.id,
        sourceFileId,
        baseRevision: response.base_revision ?? analysisRevisionsRef.current.get(sourceFileId),
      } : null);
      setPendingProposalIsRefine(Boolean(proposal.length && isRefine));
      if (isRefine && !Array.isArray(response.rubric_proposal)) {
        toast.error(t("AiJudgePanel.refineIncomplete"));
      } else if (isRefine && !proposal.length) {
        const saved = await applyAnalysis(applyItems(analysis, analysis.items ?? []), {
          persist: true,
          immediate: true,
          detectabilityNeedsReview: false,
          reviewItemIds: [],
        });
        if (saved) {
          if (sourceFileId) pendingReviewIdsByFileRef.current.set(sourceFileId, new Set());
          setPendingReviewIds(new Set());
          toast.success(t("AiJudgePanel.refineNoChanges"));
        }
      }
      return true;
    } catch (err) {
      if (!mountedRef.current || context !== activeContextRef.current) return false;
      const message = err?.message ?? t("AiJudgePanel.chatFailed");
      const synced = await refreshSessionMessages({ silent: true, replace: true });
      if (!synced) {
        setMessages(messages);
        toast.error(`${message} ${t("AiJudgePanel.chatSyncUnknown")}`);
      } else {
        toast.error(message);
      }
      return false;
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
      toast.error(t("AiJudgePanel.rubricConflict"));
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
    // 409 才退回：其他失敗時 autosave 會保留套用後的內容並重試，畫面要跟著留住，提案也不能再套一次
    const keptUnsaved = !saved && Boolean(autosaveRef.current?.hasUnsaved());
    if (!saved && !keptUnsaved) {
      setAnalysis(previousAnalysis);
      return;
    }
    if (sourceFileId) pendingReviewIdsByFileRef.current.set(sourceFileId, pendingIdsAfterApply);
    setPendingReviewIds(pendingIdsAfterApply);
    clearPendingProposal();
    if (saved) toast.success(t("AiJudgePanel.proposalApplied"));
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

  // 刪除會立即自動儲存、無法復原，先確認
  async function handleItemDelete(index) {
    const target = analysis?.items?.[index];
    const ok = await confirm({
      title: t("AiJudgePanel.deleteItemConfirmTitle"),
      message: t("AiJudgePanel.deleteItemConfirmMessage", { title: target?.title || t("AiJudgePanel.itemOrdinal", { index: index + 1 }) }),
      confirmText: t("AiJudgePanel.opDelete"),
      danger: true,
    });
    if (!ok) return;
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
    const ok = await confirm({
      title: t("AiJudgePanel.clearChatConfirmTitle"),
      message: t("AiJudgePanel.clearChatConfirmMessage"),
      confirmText: t("AiJudgePanel.clearBtn"),
      danger: true,
    });
    if (!ok) return;
    setIsClearingMessages(true);
    try {
      if (judgeSession?.id) {
        const updated = await AiJudgeService.clearSessionMessages(classId, judgeSession.id);
        onSessionUpdated?.(updated);
      }
      setMessages([]);
      setPendingAttachments([]);
      clearPendingProposal();
      setScriptGenerationNotice(null);
      toast.success(t("AiJudgePanel.chatCleared"));
    } catch (err) {
      toast.error(err?.message ?? t("AiJudgePanel.clearChatFailed"));
    } finally {
      setIsClearingMessages(false);
    }
  }

  async function handleSaveAndCreate() {
    const context = activeContextRef.current;
    if (!judgeSession?.id || !sourceFileId || !analysis || isCreatingScript) return;
    setIsCreatingScript(true);
    /* 製作中 ScriptGenerationNotice 只看 status 顯示進度，不看 notice；
       notice 只在結束時（成功／失敗）寫入，所以進行中不必另外設定 */
    setScriptGenerationStatus("saving");
    try {
      const autosave = autosaveRef.current;
      if (autosave && !(await autosave.flush({ quiet: true }))) {
        if (!mountedRef.current || context !== activeContextRef.current) return;
        // 未儲存的修改再試一次仍存不進去：不製作，說明要先讓檢查表存好
        if (autosave.hasUnsaved()) {
          const message = t("AiJudgePanel.unsavedBlocksBuild");
          setScriptGenerationNotice({ status: "error", message });
          toast.error(message);
          return;
        }
        setScriptGenerationNotice({
          status: "error",
          message: t("AiJudgePanel.buildNotSaved"),
        });
        return;
      }
      if (!mountedRef.current || context !== activeContextRef.current) return;
      const baseRevision = analysisRevisionsRef.current.get(sourceFileId);
      setScriptGenerationStatus("reviewing");
      const response = await AiJudgeService.sendSessionMessage(
        classId,
        judgeSession.id,
        RUBRIC_POLISH_PROMPT,
        baseRevision,
        { isRefine: true },
      );
      if (!mountedRef.current || context !== activeContextRef.current) return;
      const assistantMessage = response?.assistant_message;
      setMessages((current) => mergeSessionMessages(
        current,
        [response?.user_message, assistantMessage].filter(Boolean),
      ).filter(shouldDisplayChatMessage));
      const assistantMetadata = assistantMessage?.metadata_json ?? {};
      if (!Array.isArray(response.rubric_proposal)) {
        const message = t("AiJudgePanel.buildReviewMalformed");
        setScriptGenerationNotice({ status: "error", message });
        toast.error(message);
        return;
      }
      const proposal = buildProposalDiff(analysis.items ?? [], response.rubric_proposal);
      const itemResults = assistantMetadata.item_results;
      const selectableIds = getSelectableProposalIds(proposal, itemResults);
      const hasSelectable = selectableIds.size > 0;
      if (assistantMetadata.script_ready !== true) {
        setPendingItemResults(
          hasSelectable && Array.isArray(itemResults) && itemResults.length ? itemResults : null,
        );
        setPendingProposal(hasSelectable ? proposal : null);
        setSelectedProposalIds(selectableIds);
        setPendingProposalMeta(hasSelectable ? {
          messageId: assistantMessage?.id, sourceFileId, baseRevision,
        } : null);
        setPendingProposalIsRefine(hasSelectable);
      }
      if (assistantMetadata.script_ready === false) {
        const message = compactAssistantSummary(assistantMessage) || (assistantMetadata.status === "unsupported"
          ? t("AiJudgePanel.buildUnsupported")
          : assistantMetadata.status === "analysis_error"
            ? t("AiJudgePanel.buildAnalysisError")
            : t("AiJudgePanel.buildNeedsInfo"));
        setScriptGenerationNotice({ status: "error", message });
        toast.error(message);
        return;
      }
      if (assistantMetadata.script_ready !== true) {
        const message = compactAssistantSummary(assistantMessage)
          || t("AiJudgePanel.buildNoSafetyState");
        setScriptGenerationNotice({ status: "error", message });
        toast.error(message);
        return;
      }
      if (
        (response.base_revision ?? baseRevision) !== baseRevision
        || analysisRevisionsRef.current.get(sourceFileId) !== baseRevision
      ) {
        throw new Error(t("AiJudgePanel.rubricConflict"));
      }
      if (selectableIds.size !== proposal.length) {
        throw new Error(t("AiJudgePanel.buildReviewMalformed"));
      }
      // 「製作腳本」包含套用本輪核對結果；保存同一份候選內容後，才以新版本製作腳本。
      const { items: candidateItems } = applyProposalOperations(
        analysis.items ?? [],
        proposal,
        selectableIds,
      );
      const candidateAnalysis = {
        ...applyItems(analysis, candidateItems),
        detectability_needs_review: false,
        pending_review_item_ids: [],
      };
      setScriptGenerationStatus("saving");
      const saved = await applyAnalysis(candidateAnalysis, {
        persist: true,
        immediate: true,
        detectabilityNeedsReview: false,
        reviewItemIds: [],
      });
      if (!saved) {
        throw new Error(t("AiJudgePanel.buildReviewNotSaved"));
      }
      if (!mountedRef.current || context !== activeContextRef.current) return;
      pendingReviewIdsByFileRef.current.set(sourceFileId, new Set());
      setPendingReviewIds(new Set());
      clearPendingProposal();
      const savedRevision = analysisRevisionsRef.current.get(sourceFileId);
      setScriptGenerationStatus("generating");
      const artifact = await AiJudgeService.createSessionScriptSet(
        classId,
        judgeSession.id,
        savedRevision,
      );
      if (!mountedRef.current || context !== activeContextRef.current) return;
      if (artifact.status === "approved") {
        const message = t("AiJudgePanel.buildApproved", { count: artifact.children?.length ?? 0 });
        setScriptGenerationNotice({ status: "success", message });
        toast.success(message);
        if (mountedRef.current) onScriptCreated?.(artifact);
      } else if (artifact.status === "review_failed") {
        const message = t("AiJudgePanel.buildReviewFailed");
        setScriptGenerationNotice({ status: "error", message });
        toast.error(message);
      } else {
        const message = t("AiJudgePanel.buildGenerated");
        setScriptGenerationNotice({ status: "success", message });
        toast.success(message);
        if (mountedRef.current) onScriptCreated?.(artifact);
      }
      const synced = await refreshSessionMessages({ silent: true });
      if (!synced) {
        const warning = t("AiJudgePanel.chatSyncUnknown");
        setScriptGenerationNotice((current) => current
          ? { ...current, message: `${current.message} ${warning}` }
          : { status: "error", message: warning });
        toast.error(warning);
      }
    } catch (err) {
      if (!mountedRef.current || context !== activeContextRef.current) return;
      const message = err?.message ?? t("AiJudgePanel.buildFailed");
      const synced = await refreshSessionMessages({ silent: true, replace: true });
      const syncNotice = synced ? "" : t("AiJudgePanel.chatSyncUnknown");
      setScriptGenerationNotice({
        status: "error",
        message: `${t("AiJudgePanel.buildFailedRetry", { message })}${syncNotice ? ` ${syncNotice}` : ""}`,
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
  // 提案未處理、還沒有項目時按不下去；其他條件（缺少資訊、待更新）按下去會先請 AI 重新核對
  const saveAndCreateBlocker = pendingProposal || items.length === 0 ? scriptCreationBlocker : null;
  const busyReason = isChatting
    ? t("AiJudgePanel.busyChatting")
    : isUploading
      ? t("AiJudgePanel.busyUploading")
      : isClearingMessages
        ? t("AiJudgePanel.busyClearing")
        : null;
  const saveAndCreateHint = isCreatingScript
    ? null
    : busyReason ?? (autosaveStatus.unsaved ? t("AiJudgePanel.unsavedBuildHint") : scriptCreationBlocker);

  async function retryUnsavedSave() {
    const autosave = autosaveRef.current;
    if (!autosave) return;
    if (await autosave.retry()) {
      if (mountedRef.current) toast.success(t("AiJudgePanel.unsavedRetrySucceeded"));
    }
  }

  // 尚未處理的提案、附件、草稿、沒存成的檢查表修改與進行中的操作仍需要離頁提醒。
  const dirty = Boolean(pendingProposal)
    || autosaveStatus.unsaved
    || pendingAttachments.length > 0
    || chatHasDraft
    || isChatting
    || isUploading
    || isCreatingScript;
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  return (
    <div className={styles.tabBody}>
      {processingMessage && !isSendingMessage && (
        <p role="status">{t(isServerProcessing
          ? "AiJudgePanel.chatStillProcessing" : "AiJudgePanel.chatOutcomeUnknown")}</p>
      )}
      {autosaveStatus.unsaved && (
        <div
          className={`${styles.noticeInfo} ${styles.noticeWorkflowError} ${styles.noticeWithAction}`}
          role="alert"
          data-autosave-status="unsaved"
        >
          <p>
            <strong>{t("AiJudgePanel.unsavedTitle")}</strong>{" "}
            {t("AiJudgePanel.unsavedMessage", {
              reason: autosaveStatus.error?.message ?? t("AiJudgePanel.updateRubricFailed"),
            })}
            {autosaveStatus.autoRetryScheduled && ` ${t("AiJudgePanel.unsavedAutoRetry")}`}
          </p>
          <button
            type="button"
            className={styles.btnSecondary}
            disabled={autosaveStatus.saving}
            onClick={retryUnsavedSave}
          >
            {autosaveStatus.saving ? <Spinner size={14} /> : <MIcon name="refresh" size={14} />}
            {t("AiJudgePanel.unsavedRetryBtn")}
          </button>
        </div>
      )}
      <ScriptGenerationNotice
        isCreatingScript={isCreatingScript}
        status={scriptGenerationStatus}
        notice={scriptGenerationNotice}
      />

      <div className={styles.rubricsGrid}>
        <ColumnResizer
          column="chat"
          side="right"
          label={t("AiJudgePanel.resizeChatAria")}
          measureSelector='[data-guide="ai-judge-assistant"]'
          otherSelector='[data-guide="ai-judge-sessions"]'
        />
        <div className={`${styles.checkWorkPane} ${styles.rubricTableCard} ${styles.checkRubricCard}`} data-guide="ai-judge-rubric-items">
          {!filesLoaded && !analysis ? (
            <LoadingState text={t("AiJudgePanel.loadingRubric")} />
          ) : items.length === 0 && !pendingProposal ? (
            <EmptyState
              icon="playlist_add"
              title={t("AiJudgePanel.noItemsTitle")}
              description={t("AiJudgePanel.noItemsDesc")}
              action={<button type="button" className={styles.btnSecondary} onClick={() => chatColRef.current?.querySelector("textarea")?.focus()}><MIcon name="fact_check" size={16} />{t("AiJudgePanel.askAssistantBtn")}</button>}
            />
          ) : (
            <>
              <div className={styles.checkRubricBody}>
                {pendingProposal && <ProposalPanel
                  proposal={pendingProposal}
                  selectedIds={selectedProposalIds}
                  onToggle={(id) => setSelectedProposalIds((current) => {
                    const next = new Set(current);
                    if (next.has(id)) next.delete(id); else next.add(id);
                    return next;
                  })}
                  onApply={applyPendingProposal}
                  onSkip={skipPendingProposal}
                  isRefine={pendingProposalIsRefine}
                  itemResults={pendingItemResults}
                  disabled={isChatting || isClearingMessages}
                />}
                {items.length > 0 && (
                  <RubricTable
                    items={items}
                    onChange={handleItemChange}
                    onDelete={handleItemDelete}
                    disabled={isChatting || isCreatingScript}
                    needsReviewIds={pendingReviewIds}
                    machineNodes={machineNodes}
                  />
                )}
              </div>
              <SaveAndCreateAction
                onClick={handleSaveAndCreate}
                onBlocked={(reason) => toast.info(reason)}
                disabled={isChatting || isClearingMessages || isUploading}
                blocker={saveAndCreateBlocker}
                hint={saveAndCreateHint}
                isProcessing={isCreatingScript}
                status={scriptGenerationStatus}
              />
            </>
          )}
        </div>

        <div ref={chatColRef} className={styles.checkChatCol} data-guide="ai-judge-assistant">
          <div className={styles.checkChatInner}>
            <ChatPanel
              title={t("AiJudgePanel.assistantTitle")}
              messages={messages}
              onSendMessage={handleSendMessage}
              onClearMessages={handleClearMessages}
              onDraftChange={setChatHasDraft}
              isLoading={isChatting}
              loadingText={isItemwiseAnalysis ? t("AiJudgePanel.itemwiseLoading") : ""}
              isClearing={isClearingMessages}
              disabled={isCreatingScript || !analysis}
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

/* 腳本製作完要去哪：通過就到導師核查執行；其餘留在任務設定，原因由製作通知顯示 */
export function getScriptCreationDestination(artifact) {
  return artifact?.status === "approved" ? "review" : "rubrics";
}

/* ── 執行結果／核查共用的結果顯示元件 ───────────────────── */

/* 整批／單次執行是否已結束：與核查讀取、進度輪詢（queries/aiJudge）同一套判斷 */
const runIsTerminal = isTerminalRunStatus;

function newRunBatchId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

const RUN_STATUS = {
  completed: { get label() { return jt("runCompleted"); }, className: styles.badge_success },
  running: { get label() { return jt("runRunning"); }, className: styles.badge_info },
  failed: { get label() { return jt("runFailed"); }, className: styles.badge_danger },
  cancelled: { get label() { return jt("runCancelled"); }, className: styles.badge_muted },
  pending: { get label() { return jt("runPending"); }, className: styles.badge_pending },
};

function StatusBadge({ map, status }) {
  const info = map[status] ?? { label: status ?? "—", className: styles.badge_muted };
  return <span className={`${styles.badge} ${info.className}`}>{info.label}</span>;
}

const CHECK_STATUS_META = {
  pass: { icon: "check_circle", get label() { return jt("checkPass"); }, className: styles.checkIconPass },
  fail: { icon: "cancel", get label() { return jt("checkFail"); }, className: styles.checkIconFail },
  warning: { icon: "warning", get label() { return jt("checkWarning"); }, className: styles.checkIconAttention },
  unknown: { icon: "help", get label() { return jt("checkTeacherReview"); }, className: styles.checkIconWarn },
  collected: { icon: "visibility", get label() { return jt("checkTeacherReview"); }, className: styles.checkIconWarn },
  skipped: { icon: "remove_circle_outline", get label() { return jt("checkSkipped"); }, className: styles.checkIconSkip },
};

function checkStatusMeta(status) {
  return CHECK_STATUS_META[status] ?? {
    icon: "help",
    label: jt("checkUndetermined"),
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

export function getCheckResultSummary(check) {
  const parsed = parseCheckRaw(check?.raw);
  if (!parsed) return check?.evidence || "—";
  if (typeof parsed.exists === "boolean") return jt(parsed.exists ? "fileExists" : "fileMissing");
  if (typeof parsed.error_message === "string" && parsed.error_message.trim()) {
    return parsed.error_message.trim();
  }
  if (parsed.returncode !== null && parsed.returncode !== undefined && parsed.returncode !== 0) {
    const prefix = check?.status === "fail" ? jt("commandCheckFailed") : jt("commandRunFailed");
    const detail = String(parsed.stderr || parsed.stdout || "").trim().split(/\r?\n/, 1)[0];
    return `${jt("commandExitCode", { prefix, code: parsed.returncode })}${detail ? jt("detailSuffix", { detail }) : ""}`;
  }
  if (parsed.error_code === "command_exception") {
    const detail = String(parsed.error || parsed.stderr || "").trim();
    return `${jt("commandCannotRun")}${detail ? jt("detailSuffix", { detail }) : ""}`;
  }
  return check?.evidence || "—";
}

function ReturnCodeBadge({ returncode }) {
  const { t } = useTranslation("teaching");
  if (returncode === null || returncode === undefined) {
    return <span className={`${styles.cmdBadge} ${styles.cmdBadgeError}`}>{t("AiJudgePanel.runException")}</span>;
  }
  const ok = returncode === 0;
  return (
    <span className={`${styles.cmdBadge} ${ok ? styles.cmdBadgeOk : styles.cmdBadgeError}`}>
      {t("AiJudgePanel.exitCode", { code: returncode })}{ok ? " ✓" : " ✗"}
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

export function CommandLog({ raw, fallbackText }) {
  const { t } = useTranslation("teaching");
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
  const argvText = Array.isArray(parsed.argv) ? JSON.stringify(parsed.argv) : "";
  if (!argvText && !["stdout", "stderr", "returncode"].some((key) => key in parsed)) {
    return <div className={styles.cmdLog}><pre className={styles.cmdStdout}>{JSON.stringify(parsed, null, 2)}</pre>{fallbackText ? <pre className={styles.cmdStderr}>{fallbackText}</pre> : null}</div>;
  }
  const hasCommandOutput = Boolean(parsed.stdout || parsed.stderr || parsed.error);
  const empty = !argvText && !hasCommandOutput && parsed.returncode == null;
  return (
    <div className={styles.cmdLog}>
      <div className={styles.cmdHead}>
        <span className={styles.cmdLabel}>{t("AiJudgePanel.commandOutput")}</span>
        <ReturnCodeBadge returncode={parsed.returncode} />
      </div>
      {empty ? <span className={styles.cmdEmpty}>{t("AiJudgePanel.noOutput")}</span> : null}
      <CommandOutput label={t("AiJudgePanel.chipCommand")} text={argvText} />
      {argvText && !hasCommandOutput ? (
        <span className={styles.cmdEmpty}>{t("AiJudgePanel.noCommandOutput")}</span>
      ) : null}
      <CommandOutput label={t("AiJudgePanel.outputStdout")} text={parsed.stdout} />
      <CommandOutput label={t("AiJudgePanel.outputStderr")} text={parsed.stderr} isError />
      <CommandOutput label={t("AiJudgePanel.outputError")} text={parsed.error} isError />
      {Array.isArray(parsed.errors) && parsed.errors.length > 0 && (
        <CommandOutput label={t("AiJudgePanel.outputErrors")} text={parsed.errors.join("\n")} isError />
      )}
    </div>
  );
}

function CheckResultsTable({ checks }) {
  const { t } = useTranslation("teaching");
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
        <span>{t("AiJudgePanel.itemsTitle")}</span>
        <span>{t("AiJudgePanel.summaryLabel")}</span>
      </div>
      {checks.map((check, index) => {
        const id = check?.id ?? `check-${index}`;
        const meta = checkStatusMeta(check?.status);
        const detailId = `${id}-${index}`;
        const hasDetail = Boolean(
          check?.evidence || check?.raw || (Array.isArray(check?.errors) && check.errors.length),
        );
        const isOpen = hasDetail && expanded.has(detailId);
        const summary = getCheckResultSummary(check);
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
              <span className={styles.checkTitle}>{check?.title ?? check?.id ?? t("AiJudgePanel.collectedItem")}</span>
              <span className={styles.checkEvidence}>{summary}</span>
            </button>
            {isOpen && (
              <div className={styles.checkDetail}>
                {summary !== "—" && <p>{summary}</p>}
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
const EXECUTION_BLOCKERS = {
  vm_powered_off: { label: "statusPoweredOff", message: "poweredOffHint" },
  vm_status_unavailable: { label: "statusPowerUnavailable", message: "powerUnavailableHint" },
};

function executionFailureKind(target) {
  const reason = target?.reason_code;
  if (["ssh_authentication_failed", "ssh_connection_failed", "ssh_connection_timeout", "missing_ip", "missing_ssh_key"].includes(reason)) return "Connection";
  if (["python_missing", "timeout_unavailable", "unsupported_os", "vm_powered_off", "vm_status_unavailable", "not_running", "preparation_failed"].includes(reason)) return "Environment";
  if (["download_failed", "upload_failed"].includes(reason)) return "Transfer";
  if (["missing_checks", "check_id_mismatch", "duplicate_check_ids"].includes(reason)) return "Incomplete";
  if (["invalid_json", "result_too_large", "output_too_large", "execution_nonzero"].includes(reason)) return "Script";
  if (reason === "execution_timeout") return "Timeout";
  return "Unknown";
}

export function ExecutionFailure({ target }) {
  const { t } = useTranslation("teaching");
  if (!target || (target.status !== "failed" && target.status !== "cancelled" && target.validation?.valid !== false)) return null;
  const kind = executionFailureKind(target);
  return (
    <div className={styles.reviewExecutionError} role="status">
      <strong>{t(`AiJudgePanel.execution${kind}`)}</strong>
      <p>{t(`AiJudgePanel.execution${kind}Hint`)}</p>
      <p>{t("AiJudgePanel.executionNotGraded")}</p>
      <details>
        <summary>{t("AiJudgePanel.executionEvidence")}</summary>
        <div className={styles.cmdLog}>
          <p>{t("AiJudgePanel.executionReason", { reason: target.reason_code || "unknown", stage: target.failure_stage || "unknown" })}</p>
          {target.exit_code != null && <p>{t("AiJudgePanel.exitCode", { code: target.exit_code })}</p>}
          <CommandOutput label={t("AiJudgePanel.outputStderr")} text={target.stderr_excerpt} isError />
          <CommandOutput label={t("AiJudgePanel.outputStdout")} text={target.stdout_excerpt} />
          <CommandOutput label={t("AiJudgePanel.executionValidation")} text={target.validation?.error} isError />
          {["missing_check_ids", "unexpected_check_ids", "duplicate_check_ids"].map((key) => (
            <CommandOutput key={key} label={t(`AiJudgePanel.${key}`)} text={Array.isArray(target.validation?.[key]) ? target.validation[key].join(", ") : ""} />
          ))}
          {!target.stdout_excerpt && !target.stderr_excerpt && !target.validation?.error && <p>{t("AiJudgePanel.executionNoEvidence")}</p>}
        </div>
      </details>
    </div>
  );
}

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
  if (!target) return { kind: "missing", label: jt("statusNotRun"), pending: 0, reviewable: 0 };
  if (target.status === "failed" || target.status === "cancelled" || target.validation?.valid === false) {
    return { kind: "failed", label: jt(EXECUTION_BLOCKERS[target.reason_code]?.label ?? "statusRunFailed"), pending: 0, reviewable: 0 };
  }
  const reviewable = targetChecks(target).filter((check) => (
    TEACHER_REVIEW_STATUSES.has(check?.status)
  ));
  const decisions = targetTeacherReview(target).decisions;
  const pending = reviewable.filter((check) => !decisions[check?.id]).length;
  if (pending > 0) {
    return {
      kind: "pending",
      label: jt("statusPendingReview", { count: pending }),
      pending,
      reviewable: reviewable.length,
    };
  }
  if (reviewable.length > 0) {
    return { kind: "reviewed", label: jt("statusReviewed"), pending: 0, reviewable: reviewable.length };
  }
  if (targetTeacherReview(target).feedback) {
    return { kind: "reviewed", label: jt("statusCommented"), pending: 0, reviewable: 0 };
  }
  return { kind: "automatic", label: jt("statusAutomatic"), pending: 0, reviewable: 0 };
}

/* 草稿跟已儲存的比：決定逐項比對，取消再勾回來只是 key 順序不同，不算改過 */
export function isReviewDraftDirty(draft, saved) {
  if (!draft) return false;
  if ((draft.feedback ?? "") !== (saved?.feedback ?? "")) return true;
  const draftDecisions = draft.decisions ?? {};
  const savedDecisions = saved?.decisions ?? {};
  const keys = Object.keys(draftDecisions);
  if (keys.length !== Object.keys(savedDecisions).length) return true;
  return keys.some((key) => draftDecisions[key] !== savedDecisions[key]);
}

function reviewBadgeClass(kind) {
  if (kind === "pending") return styles.badge_pending;
  if (kind === "failed") return styles.badge_danger;
  if (kind === "reviewed") return styles.badge_success;
  return styles.badge_muted;
}

/* 一個檢查項一列：狀態圖示、名稱＋摘要、需要老師判定時右邊是通過／未通過。
   原始輸出收在「查看原始證據」裡；待判定且還沒判的整列淡橘底，一眼找得到 */
function ReviewCheckRow({ check, decision, onDecide, canDecide = true }) {
  const { t } = useTranslation("teaching");
  const meta = checkStatusMeta(check?.status);
  const reviewable = canDecide && TEACHER_REVIEW_STATUSES.has(check?.status);
  const title = check?.title ?? check?.id ?? t("AiJudgePanel.collectedItem");
  const summary = getCheckResultSummary(check);
  const hasLog = Boolean(check?.raw || (Array.isArray(check?.errors) && check.errors.length > 0));
  return (
    <div className={`${styles.reviewCheck} ${reviewable && !decision ? styles.reviewCheckPending : ""}`}>
      <span className={`${styles.reviewCheckIcon} ${meta.className}`} role="img" aria-label={meta.label} title={meta.label}>
        <MIcon name={meta.icon} size={16} />
      </span>
      <div className={styles.reviewCheckContent}>
        <strong>{title}</strong>
        <p>{summary === "—" ? t("AiJudgePanel.noSummary") : summary}</p>
        {check?.status === "unknown" && parseCheckRaw(check?.raw)?.error_code && <p>{t("AiJudgePanel.collectionErrorHint")}</p>}
        {hasLog && (
          <details><summary>{t("AiJudgePanel.viewEvidence")}</summary><CommandLog raw={check?.raw} fallbackText={(check?.errors ?? []).join("\n")} /></details>
        )}
      </div>
      {reviewable && (
        <div className={styles.reviewDecision} aria-label={t("AiJudgePanel.manualDecisionAria", { title })}>
          <button type="button" className={decision === "pass" ? styles.reviewPassActive : ""} aria-pressed={decision === "pass"} onClick={() => onDecide("pass")}><MIcon name="check" size={15} />{t("AiJudgePanel.checkPass")}</button>
          <button type="button" className={decision === "fail" ? styles.reviewFailActive : ""} aria-pressed={decision === "fail"} onClick={() => onDecide("fail")}><MIcon name="close" size={15} />{t("AiJudgePanel.checkFail")}</button>
        </div>
      )}
    </div>
  );
}

/* 檢查點右邊的狀態標籤：還有待判定的先講待判定，其次未通過，全過就寫幾項都通過 */
export function checkpointBadge(item, decisions = {}) {
  const checks = Array.isArray(item?.checks) ? item.checks : [];
  if (item?.evidence_state === "unavailable") return { kind: "muted", label: jt("checkpointUnavailable") };
  if (item?.missing_check_ids?.length || item?.evidence_state === "incomplete" || checks.length === 0 && item?.status === "unknown") return { kind: "muted", label: jt("checkpointIncompleteLabel") };
  const pending = checks.filter((check) => TEACHER_REVIEW_STATUSES.has(check?.status) && !decisions[check?.id]).length;
  if (pending > 0) return { kind: "pending", label: jt("statusPendingReview", { count: pending }) };
  const failed = checks.filter((check) => (decisions[check?.id] ?? check?.status) === "fail").length;
  if (failed > 0 || item?.status === "fail") return { kind: "fail", label: jt("checkpointFailedCount", { count: Math.max(failed, 1) }) };
  if (checks.length > 0 && checks.every((check) => (decisions[check?.id] ?? check?.status) === "pass")) {
    return { kind: "pass", label: jt("checkpointPassedCount", { count: checks.length }) };
  }
  return { kind: "muted", label: checkStatusMeta(item?.status).label };
}

const CHECKPOINT_BADGE_CLASS = {
  pending: styles.badge_pending,
  fail: styles.badge_danger,
  pass: styles.badge_success,
  muted: styles.badge_muted,
};

/* 一個檢查點一組：標題列可收合，全部通過的預設收起來，老師只需要看未通過與待判定的 */
function ReviewCheckpointGroup({ item, decisions, onDecide, executionFailed = false }) {
  const { t } = useTranslation("teaching");
  const itemMeta = checkStatusMeta(item?.status);
  const checks = Array.isArray(item?.checks) ? item.checks : [];
  const missingCount = Array.isArray(item?.missing_check_ids) ? item.missing_check_ids.length : 0;
  const badge = checkpointBadge(item, decisions);
  const peerText = item?.peer_node_key
    ? t("AiJudgePanel.peerObserve", { name: item?.peer_display_label ?? item.peer_node_key })
    : null;
  return (
    <details className={styles.reviewCheckGroup} open={badge.kind !== "pass" || missingCount > 0}>
      <summary className={styles.reviewCheckGroupHead}>
        {/* 還有待判定的檢查項時，圖示跟右邊的「待核查」同色，不要一邊紅一邊橘 */}
        <span className={`${styles.reviewCheckIcon} ${badge.kind === "pending" ? styles.checkIconWarn : itemMeta.className}`} role="img" aria-label={itemMeta.label}>
          <MIcon name={itemMeta.icon} size={18} />
        </span>
        <span className={styles.reviewCheckGroupTitle}>
          <strong>{item?.title ?? item?.rubric_item_id ?? t("AiJudgePanel.thCheckpoint")}</strong>
          {peerText && <small>{peerText}</small>}
        </span>
        <span className={`${styles.badge} ${CHECKPOINT_BADGE_CLASS[badge.kind]}`}>{badge.label}</span>
        <span className={styles.reviewCheckGroupChevron}><MIcon name="expand_more" size={18} /></span>
      </summary>
      <div className={styles.reviewCheckGroupBody}>
        {missingCount > 0 && !executionFailed && (
          <p className={styles.reviewCheckNote}>{t("AiJudgePanel.checkpointIncomplete", { count: missingCount })}</p>
        )}
        {checks.length === 0 ? (
          <p className={styles.reviewCheckNote}>
            {executionFailed ? t("AiJudgePanel.checkpointExecutionFailed") : item?.reason_code === "peer_unavailable" ? t("AiJudgePanel.peerUnavailable") : t("AiJudgePanel.checkpointNoResult")}
          </p>
        ) : checks.map((check, checkIndex) => (
          <ReviewCheckRow
            key={`${item?.rubric_item_id ?? "item"}-${check?.id ?? "check"}-${checkIndex}`}
            check={check}
            decision={decisions[check?.id]}
            onDecide={(value) => onDecide(check?.id, value)}
            canDecide={!executionFailed && missingCount === 0 && item?.evidence_state !== "unavailable"}
          />
        ))}
      </div>
    </details>
  );
}

function reviewRowUser(row) {
  return row?.target?.user ?? row?.member ?? {};
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
    if (getTargetReviewSummary(row?.target).kind === "failed") continue;
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
  if (list.length === 0) return { kind: "missing", label: jt("statusNotRun"), pending: 0, reviewable: 0 };
  const summaries = list.map((row) => getTargetReviewSummary(row?.target));
  const pending = summaries.reduce((sum, item) => sum + (item.pending || 0), 0);
  const reviewable = summaries.reduce((sum, item) => sum + (item.reviewable || 0), 0);
  const kinds = new Set(summaries.map((item) => item.kind));
  if (kinds.has("pending")) return { kind: "pending", label: jt("statusPendingConfirm", { count: pending }), pending, reviewable };
  if (kinds.has("failed")) return { kind: "failed", label: jt("statusSomeFailed"), pending: 0, reviewable };
  if (kinds.has("reviewed")) return { kind: "reviewed", label: jt("statusReviewed"), pending: 0, reviewable };
  if (kinds.has("automatic") && kinds.size === 1) return { kind: "automatic", label: jt("statusAutomatic"), pending: 0, reviewable };
  if (kinds.has("missing") && kinds.size === 1) return { kind: "missing", label: jt("statusNotRun"), pending: 0, reviewable };
  return { kind: "automatic", label: jt("statusAutomatic"), pending: 0, reviewable };
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
    // split 一定回傳字串（沒有 email 時是空字串），要用 || 才會退到 studentId
    ?? (email.split("@")[0] || student?.studentId)
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
    // 回報完成但還沒檢查的學生是下一個要處理的，排在待確認之後
    const rankOf = (student) => (
      student?.status?.kind === "missing" && student?.report?.ready
        ? 0.5
        : rank[student?.status?.kind] ?? 9
    );
    const statusDelta = rankOf(left) - rankOf(right);
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
    display: [displayLabel, name].filter(Boolean).join(" · ") || key || jt("nodeNotSpecified"),
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
    ...Object.fromEntries(["exit_code", "failure_stage", "validation", "stdout_excerpt", "stderr_excerpt"].filter((key) => node?.[key] !== undefined).map((key) => [key, node[key]])),
    user: member ?? {},
    teacher_review: node?.teacher_review ?? undefined,
    timing: node?.timing,
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

/* 整份核查結果換上畫面時的草稿：批次模式舊到新套用，同一位學生以較新的那批為準（跟畫面上採用的那批一致） */
function draftsFromReviewState(state) {
  const nextDrafts = {};
  if (state?.mode === "batch") {
    for (const entry of [...state.batches].reverse()) Object.assign(nextDrafts, draftsFromBatch(entry));
  } else if (state?.mode === "run") {
    for (const target of state.run?.target_results_json?.targets ?? []) {
      nextDrafts[String(target.vmid)] = reviewDraftFromTeacherReview(target?.teacher_review);
    }
  }
  return nextDrafts;
}

export function buildBatchReviewRows(batch, members = []) {
  const runIdByNodeKey = new Map(
    (Array.isArray(batch?.nodes) ? batch.nodes : []).map((node) => [
      String(node?.target_node_key ?? node?.node_key ?? ""),
      node?.run_id ?? null,
    ]),
  );
  const memberByVmidNode = new Map();
  const memberByVmid = new Map();
  const memberByStudentNode = new Map();
  for (const member of Array.isArray(members) ? members : []) {
    if (member?.student_id != null) {
      memberByStudentNode.set(
        `${String(member.student_id)}|${String(member?.node_key ?? "")}`,
        member,
      );
    }
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
        ?? memberByStudentNode.get(`${String(student?.student_id ?? "")}|${nodeKey}`)
        ?? null;
      const vmid = node?.vmid ?? member?.vmid ?? null;
      rows.push({
        key: batchRowKey(student?.student_id, nodeKey, vmid),
        member: member ?? { user_id: student?.student_id ?? null },
        target: buildBatchReviewTarget(node, member),
        node,
        runId: node?.run_id ?? runIdByNodeKey.get(nodeKey) ?? null,
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

/**
 * 多批執行合成一份核查列：每位學生取「最近一次有檢查到他」的那一批。
 * 只檢查部分學生（例如已回報完成的人）之後，其他人的結果仍留在較舊的批次裡。
 * batches 由新到舊排列。
 */
export function buildMergedBatchReviewRows(batches, members = []) {
  const seen = new Set();
  const rows = [];
  for (const batch of Array.isArray(batches) ? batches : []) {
    const batchRows = buildBatchReviewRows(batch, members);
    const studentsInBatch = new Set();
    for (const row of batchRows) {
      const studentKey = String(row.studentId ?? row.key);
      if (seen.has(studentKey)) continue;
      studentsInBatch.add(studentKey);
      rows.push({ ...row, batchId: batch?.run_batch_id ?? null });
    }
    for (const key of studentsInBatch) seen.add(key);
  }
  return rows;
}

function reportSummary(report, itemCount) {
  if (!report) return null;
  const done = report.completed_item_ids?.length ?? 0;
  return {
    done,
    total: itemCount,
    ready: Boolean(report.ready_at),
    kind: report.ready_at ? "ready" : done > 0 ? "partial" : "none",
  };
}

/* 老師給這位學生這週的回覆（一週一則，再回覆覆蓋）；沒回覆是空字串 */
function replyOf(report) {
  return { text: String(report?.teacher_reply ?? ""), at: report?.teacher_reply_at ?? null };
}

/**
 * 學生總覽補上自評回報，並把還沒有任何檢查結果的學生也列進來（尚未檢查），
 * 老師才看得到全班當週誰回報完成、誰還沒動。
 */
export function attachStudentReports(students, progress) {
  const reports = Array.isArray(progress?.students) ? progress.students : [];
  const itemCount = Array.isArray(progress?.items) ? progress.items.length : 0;
  const reportByUser = new Map(reports.map((report) => [String(report.user_id), report]));
  const matched = new Set();
  const userIdOf = (student) => String(
    student?.user?.user_id
    ?? student?.machines?.find((row) => row?.member?.user_id)?.member?.user_id
    ?? "",
  );
  const withReports = (Array.isArray(students) ? students : []).map((student) => {
    const userId = userIdOf(student);
    const report = userId ? reportByUser.get(userId) : undefined;
    if (report) matched.add(userId);
    return {
      ...student,
      userId: userId || null,
      report: reportSummary(report, itemCount),
      reply: replyOf(report),
    };
  });
  const missing = reports
    .filter((report) => !matched.has(String(report.user_id)))
    .map((report) => ({
      studentId: `user:${report.user_id}`,
      userId: String(report.user_id),
      machines: [],
      user: { user_id: report.user_id, full_name: report.full_name, email: report.email },
      totals: aggregateStudentCheckTotals([]),
      status: getStudentOverviewStatus([]),
      report: reportSummary(report, itemCount),
      reply: replyOf(report),
    }));
  return [...withReports, ...missing];
}

export function buildLegacyReviewRows(run, members = []) {
  const targets = run?.target_results_json?.targets ?? [];
  const targetsByVmid = new Map(targets.map((target) => [String(target.vmid), target]));
  const targetsByStudentId = new Map(
    targets
      .filter((target) => target?.student_id != null)
      .map((target) => [String(target.student_id), target]),
  );
  const matchedTargets = new Set();
  const memberRows = (Array.isArray(members) ? members : []).map((member) => {
    const target = (member?.vmid != null
      ? targetsByVmid.get(String(member.vmid))
      : null)
      ?? (member?.student_id != null
        ? targetsByStudentId.get(String(member.student_id))
        : null)
      ?? null;
    if (target) matchedTargets.add(target);
    const studentId = target?.student_id ?? member?.student_id ?? null;
    return {
      key: String(member.vmid ?? studentId ?? member.user_id ?? member.email),
      member,
      target: target ?? null,
      runId: run?.id ?? null,
      vmid: member.vmid ?? target?.vmid ?? null,
      studentId,
      items: null,
    };
  });
  const unmatched = targets
    .filter((target) => !matchedTargets.has(target))
    .map((target) => ({
      key: String(
        target.vmid
        ?? target.student_id
        ?? target.user?.user_id
        ?? target.user?.id
        ?? target.user?.email,
      ),
      member: target.user ?? {},
      target,
      runId: run?.id ?? null,
      vmid: target.vmid ?? null,
      studentId: target.student_id ?? null,
      items: null,
    }));
  return [...memberRows, ...unmatched];
}

export function ExecutionTiming({ timing }) {
  const { t } = useTranslation("teaching");
  if (!timing) return null;
  const hasSeconds = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;
  // A missing field belongs to a single-run record; null means a batch is unfinished.
  const batchWait = timing.batch_wait_seconds === undefined ? 0 : timing.batch_wait_seconds;
  const metrics = [
    ["timingQueue", timing.queue_seconds],
    ["timingCollection", hasSeconds(timing.prepare_seconds) && hasSeconds(timing.script_seconds)
      ? timing.prepare_seconds + timing.script_seconds : null],
    ["timingDownload", timing.download_seconds],
    ["timingReady", hasSeconds(timing.download_seconds) && hasSeconds(timing.finalize_wait_seconds) && hasSeconds(batchWait)
      ? timing.download_seconds + timing.finalize_wait_seconds + batchWait : null],
    ["timingTotal", timing.total_seconds],
  ].filter(([, value]) => hasSeconds(value));
  if (metrics.length === 0) return null;
  return (
    <details className={styles.executionTiming}>
      <summary>{t("AiJudgePanel.timingTitle")}</summary>
      <dl>
        {metrics.map(([key, value]) => (
          <div key={key}>
            <dt>{t(`AiJudgePanel.${key}`)}</dt>
            <dd>{t("AiJudgePanel.timingSeconds", { value: value.toFixed(3) })}</dd>
          </div>
        ))}
      </dl>
      <p>{t("AiJudgePanel.timingHint")}</p>
    </details>
  );
}

/* 單台機器的檢查結果；判定與本週回覆由學生下方的儲存按鈕一起儲存。 */
function MachineReviewDetail({
  row,
  machineNodes = [],
  draft,
  onToggleDecision,
}) {
  const { t } = useTranslation("teaching");
  const { target } = row;
  const checks = targetChecks(target);
  const executionBlocker = target?.status === "failed" ? EXECUTION_BLOCKERS[target.reason_code] : null;
  const nodeLabel = row.nodeKey
    ? batchMachineDisplayName(row.nodeKey, machineNodes, row.node?.display_label) ?? row.nodeKey
    : null;
  if (!target) {
    return <div className={styles.reviewNoResult}>{t("AiJudgePanel.machineNotInRun")}</div>;
  }
  const summary = getTargetReviewSummary(target);
  const decide = (checkId, value) => onToggleDecision(row.key, checkId, value);
  return (
    <>
      <div className={styles.reviewMachineHead}>
        <strong>{nodeLabel ?? `VMID ${row.vmid ?? "—"}`}</strong>
        {row.vmid ? <small>{`VMID ${row.vmid}`}</small> : null}
        <span className={`${styles.badge} ${reviewBadgeClass(summary.kind)}`}>{summary.label}</span>
      </div>
      {/* 關機、狀態不明有自己的說明（下方），不要再疊一段「請檢查 Python、timeout」的主機環境提示 */}
      {!executionBlocker && <ExecutionFailure target={target} />}
      {executionBlocker ? (
        <p className={styles.mutedText}><strong>{t(`AiJudgePanel.${executionBlocker.message}`)}</strong></p>
      ) : row.items ? (
        <div className={styles.reviewCheckList}>
          {row.items.length === 0 && (
            <p className={styles.mutedText}>{t("AiJudgePanel.machineNoCheckpoints")}</p>
          )}
          {row.items.map((item, itemIndex) => (
            <ReviewCheckpointGroup
              key={`${row.key}-item-${item?.rubric_item_id ?? itemIndex}`}
              item={item}
              decisions={draft.decisions}
              onDecide={decide}
              executionFailed={summary.kind === "failed"}
            />
          ))}
          {(row.unmappedChecks ?? []).length > 0 && (
            <details className={styles.judgeDetails}>
              <summary>{t("AiJudgePanel.unmappedChecks")}</summary>
              <CheckResultsTable checks={row.unmappedChecks} />
            </details>
          )}
        </div>
      ) : (
        <div className={`${styles.reviewCheckList} ${styles.reviewCheckListFlat}`}>
          {checks.length === 0 ? <p className={styles.mutedText}>{t("AiJudgePanel.scriptNoChecks")}</p> : checks.map((check, index) => (
            <ReviewCheckRow
              key={`${check?.id ?? "check"}-${index}`}
              check={check}
              decision={draft.decisions[check?.id]}
              onDecide={(value) => decide(check?.id, value)}
              canDecide={summary.kind !== "failed"}
            />
          ))}
        </div>
      )}

      <div className={styles.reviewMachineFoot}>
        <ExecutionTiming timing={target.timing} />
      </div>
    </>
  );
}

/**
 * 執行異常與學生未通過分開呈現，避免把不完整證據當成成績。
 */
export function checkOutcome(machines) {
  const checked = (Array.isArray(machines) ? machines : []).filter((row) => row?.target);
  if (checked.length === 0) return { kind: "none", count: 0 };
  const totals = aggregateStudentCheckTotals(checked);
  const failedRuns = checked.filter((row) => getTargetReviewSummary(row.target).kind === "failed").length;
  if (failedRuns > 0) return { kind: "error", count: failedRuns };
  if (totals.fail > 0) return { kind: "fail", count: totals.fail };
  if (totals.pending > 0) return { kind: "pending", count: totals.pending };
  if (totals.pass > 0) return { kind: "pass", count: totals.pass };
  return { kind: "none", count: 0 };
}

const OUTCOME_BADGE_CLASS = {
  error: styles.badge_danger,
  pass: styles.badge_success,
  fail: styles.badge_danger,
  pending: styles.badge_pending,
  none: styles.badge_muted,
};

const REPLY_MAX_LENGTH = 4000;
// 快到上限才顯示字數，平常不佔位置
const REPLY_COUNTER_FROM = 3600;

const OUTCOME_CHIP_CLASS = {
  error: styles.machineChipFail,
  pass: styles.machineChipPass,
  fail: styles.machineChipFail,
  pending: styles.machineChipPending,
  none: "",
};

/** PVE 回報的狀態收成三種：開著、關著、不知道（查不到或還在建機）。 */
export function machinePowerState(vmStatus) {
  const status = String(vmStatus ?? "").toLowerCase();
  if (status === "running") return "on";
  if (["stopped", "paused", "suspended"].includes(status)) return "off";
  return "unknown";
}

const POWER_DOT_CLASS = {
  on: styles.powerDotOn,
  off: styles.powerDotOff,
  unknown: styles.powerDotUnknown,
};

/**
 * 這位學生在班上的每一台機器（還沒檢查過也列出來），帶上那台最近一次的檢查結果；
 * 檢查結果裡有、但名單上已經沒有的機器（例如 VM 已刪除）也保留。
 */
export function studentMachineChips(student, members = [], machineNodes = []) {
  const userId = String(student?.userId ?? "");
  const runRows = Array.isArray(student?.machines) ? student.machines : [];
  const rowByVmid = new Map(
    runRows.filter((row) => row?.vmid != null).map((row) => [String(row.vmid), row]),
  );
  const seen = new Set();
  const chip = (nodeKey, vmid, fallbackLabel, rows, vmStatus) => {
    const blocked = rows.find((row) => row?.target?.status === "failed" && EXECUTION_BLOCKERS[row.target.reason_code]);
    const label = batchMachineDisplayName(nodeKey, machineNodes, fallbackLabel)
      ?? nodeKey
      ?? (vmid != null ? `VMID ${vmid}` : "—");
    return {
      key: vmid != null ? `vm-${vmid}` : `node-${nodeKey ?? fallbackLabel}`,
      label: blocked ? `${getTargetReviewSummary(blocked.target).label} · ${label}` : label,
      vmid: vmid ?? null,
      outcome: checkOutcome(rows).kind,
      power: machinePowerState(vmStatus),
    };
  };
  const chips = (Array.isArray(members) ? members : [])
    .filter((member) => userId && String(member?.user_id ?? "") === userId)
    .map((member) => {
      const row = member.vmid != null ? rowByVmid.get(String(member.vmid)) : undefined;
      if (row) seen.add(row);
      return chip(member.node_key, member.vmid, member.display_label ?? member.node_name ?? null, row ? [row] : [], member.vm_status);
    });
  runRows
    .filter((row) => !seen.has(row))
    .forEach((row) => chips.push(chip(row.nodeKey, row.vmid, row.node?.display_label ?? null, [row])));
  return chips;
}

export function TeacherReviewTab({
  classId,
  sessionId,
  members,
  machineNodes = [],
  active = true,
  weekPublished = true,
  onDirtyChange,
  onGoToSettings,
  onGoToPublish,
  onRunStarted,
}) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  // { mode: "batch", batches }（新到舊；每位學生取最近一次有檢查到他的那批）| { mode: "run", run }
  const [reviewState, setReviewState] = useState(null);
  const [reviewSetId, setReviewSetId] = useState(null);
  const reviewSetIdRef = useRef(null);
  // 全班當週自評回報（誰回報完成、做到第幾項）；讀不到時總覽照樣只列有結果的學生
  const [progress, setProgress] = useState(null);
  // 每位學生這週回覆的草稿（user_id → 文字）；存好就拿掉
  const [replyDrafts, setReplyDrafts] = useState({});
  const [expandedKey, setExpandedKey] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [savingKey, setSavingKey] = useState(null);
  const savingStudentRef = useRef(false);
  const [sortMode, setSortMode] = useState("pending");
  const [runOnceOpen, setRunOnceOpen] = useState(false);
  const [creatingBatch, setCreatingBatch] = useState(false);
  // 接手追蹤的整批執行（建立回應、恢復、或載入時發現還在跑的那批）；畫面用的最新進度見下方 activeBatch
  const [trackedBatch, setActiveBatch] = useState(null);
  const [pendingBatch, setPendingBatch] = useState(null);
  const submissionRef = useRef(null);
  // 整批執行完成時老師還有沒存的核查：先不換結果（草稿會套到新結果上），存完再自動載入
  const [newResultsWaiting, setNewResultsWaiting] = useState(false);
  const runOnceDialog = useDialogPresence(runOnceOpen);
  const batchStatusRef = useRef(null);
  const hasDirtyDraftsRef = useRef(false);
  const wasActiveRef = useRef(active);
  // 這個檢查的核查結果已經第一次換上畫面（之後的讀取都是背景更新）
  const appliedReviewContextRef = useRef(null);
  const judgeCache = useAiJudgeCacheUpdater();

  /* 核查資料（腳本集、全班回報、最近的批次結果）：掛載時讀一次，之後只在下面幾種時機 refetch
     （切回這個分頁、整批跑完、存完草稿）。讀取失敗且畫面上還沒有資料時顯示錯誤畫面 */
  const reviewQuery = useAiJudgeReviewQuery(classId, sessionId);
  const { loading, error: loadError, retry: retryLoadReview } = useQueryView(reviewQuery);
  const reviewData = reviewQuery.data;
  const scriptSets = reviewData?.scriptSets ?? NO_SCRIPT_SETS;
  const refetchReview = reviewQuery.refetch;
  /* 背景更新：不蓋掉畫面；老師還有沒存的核查時只更新腳本集與回報，結果留到存完再換 */
  const loadReview = useCallback(() => {
    void refetchReview();
  }, [refetchReview]);

  /* 每讀到新的一份就換上畫面（layout effect：跟資料同一次繪製，不會先閃一下空狀態）。
     第一次一律換上；之後老師有沒存的核查時，只更新回報與進行中的批次，生效組換了才提示 */
  useLayoutEffect(() => {
    if (!reviewData) return;
    const context = `${classId}:${sessionId}`;
    const firstLoad = appliedReviewContextRef.current !== context;
    appliedReviewContextRef.current = context;
    setProgress(reviewData.progress);
    // 離開再回來時整批還在跑：接回進度輪詢，「一次執行」維持停用，避免重複執行
    if (reviewData.runningBatch) {
      batchStatusRef.current = reviewData.runningBatch.status;
      setActiveBatch(reviewData.runningBatch);
    }
    if (!firstLoad && hasDirtyDraftsRef.current) {
      if (reviewSetIdRef.current !== reviewData.activeSetId) setNewResultsWaiting(true);
      return;
    }
    reviewSetIdRef.current = reviewData.activeSetId;
    setReviewSetId(reviewData.activeSetId);
    setReviewState(reviewData.review);
    setDrafts(draftsFromReviewState(reviewData.review));
    setNewResultsWaiting(false);
  }, [classId, sessionId, reviewData]);

  useEffect(() => {
    setReviewState(null);
    setReviewSetId(null);
    reviewSetIdRef.current = null;
    setProgress(null);
    setReplyDrafts({});
    setExpandedKey(null);
    setActiveBatch(null);
    setPendingBatch(null);
    setCreatingBatch(false);
    submissionRef.current = null;
    setNewResultsWaiting(false);
    batchStatusRef.current = null;
    return () => {
      submissionRef.current = null;
    };
  }, [classId, sessionId]);

  /* 分頁一直掛著（草稿才不會消失），切回來時靜默更新：
     例如剛在腳本總覽核准腳本，這裡要能馬上一次執行 */
  useEffect(() => {
    if (active && !wasActiveRef.current) loadReview();
    wasActiveRef.current = active;
  }, [active, loadReview]);

  const acceptBatch = useCallback((batch) => {
    batchStatusRef.current = batch.status;
    setActiveBatch(runIsTerminal(batch.status) ? null : batch);
    setPendingBatch(null);
    setRunOnceOpen(false);
    onRunStarted?.();
    if (batch.status === "failed") toast.error(batch.error_message ?? t("AiJudgePanel.batchFailed"));
    if (runIsTerminal(batch.status)) loadReview();
  }, [loadReview, onRunStarted, t, toast]);

  // A lost acknowledgement is an unknown outcome. Query its stable ID before
  // allowing another run; an explicit retry keeps the same ID and scope.
  useEffect(() => {
    if (!pendingBatch || creatingBatch) return undefined;
    let cancelled = false;
    let timer;
    async function recover() {
      try {
        let batch;
        try {
          batch = await AiJudgeService.getSessionRunBatch(classId, sessionId, pendingBatch.runBatchId);
        } catch (error) {
          if (error?.status !== 404) throw error;
          const summaries = await AiJudgeService.listSessionRunBatches(classId, sessionId, 10);
          const running = summaries.find((entry) => !runIsTerminal(entry.status));
          if (running) {
            batch = await AiJudgeService.getSessionRunBatch(classId, sessionId, running.run_batch_id);
          } else if (pendingBatch.conflict) {
            if (!cancelled) {
              setPendingBatch(null);
              toast.error(pendingBatch.errorMessage ?? t("AiJudgePanel.batchCreateFailed"));
              loadReview();
            }
            return;
          }
        }
        if (cancelled) return;
        if (batch) {
          acceptBatch(batch);
          if (batch.status !== "failed") toast.info(t("AiJudgePanel.batchRecovered"));
          return;
        }
      } catch (error) {
        if (cancelled) return;
        if (error?.status === 401 || error?.status === 403 || error?.sessionChanged) {
          setPendingBatch(null);
          toast.error(error.message);
          return;
        }
      }
      if (!cancelled) timer = setTimeout(recover, 5000);
    }
    recover();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [classId, sessionId, pendingBatch, creatingBatch, acceptBatch, loadReview, t, toast]);

  /* 「一次執行」進度輪詢：接手的那批先照原樣顯示，1.5 秒後開始問，之後每 2 秒（失敗 5 秒後再試），
     到終態就停；畫面上的進度是最新一次的回應。整批到終態後下面的 effect 重新載入核查資料 */
  const pollBatchId = trackedBatch?.run_batch_id && !runIsTerminal(trackedBatch.status)
    ? trackedBatch.run_batch_id : null;
  const batchProgressQuery = useAiJudgeRunBatchQuery(classId, sessionId, pollBatchId, {
    initialData: pollBatchId ? trackedBatch : undefined,
  });
  const activeBatch = pollBatchId && batchProgressQuery.data ? batchProgressQuery.data : trackedBatch;

  useEffect(() => {
    const previous = batchStatusRef.current;
    batchStatusRef.current = activeBatch?.status ?? null;
    if (
      activeBatch
      && previous
      && previous !== activeBatch.status
      && runIsTerminal(activeBatch.status)
    ) {
      const failed = activeBatch.summary?.failed ?? 0;
      if (activeBatch.status === "failed") {
        toast.error(t("AiJudgePanel.batchFailed"));
      } else if (hasDirtyDraftsRef.current) {
        setNewResultsWaiting(true);
        toast.info(t("AiJudgePanel.batchDoneUnsaved"));
      } else if (failed > 0 || activeBatch.status === "completed_with_failures") {
        toast.error(failed > 0
          ? t("AiJudgePanel.batchDoneFailedCount", { count: failed })
          : t("AiJudgePanel.batchDoneFailed"));
      } else {
        toast.success(t("AiJudgePanel.batchDone"));
      }
      setActiveBatch(null);
      loadReview();
    }
  }, [activeBatch, loadReview, t, toast]);

  const activeSet = useMemo(
    () => getActiveScriptSet(scriptSets),
    [scriptSets],
  );
  const batchRunning = Boolean(pendingBatch || (activeBatch && !runIsTerminal(activeBatch.status)));

  const machineRows = useMemo(() => {
    if (!reviewState) return [];
    return reviewState.mode === "batch"
      ? buildMergedBatchReviewRows(reviewState.batches, members)
      : buildLegacyReviewRows(reviewState.run, members);
  }, [reviewState, members]);

  /* 以學生為單位的總覽：machine rows 按 studentId 分組，補上自評回報與還沒檢查過的學生，
     排序以學生狀態為準。 */
  const studentRows = useMemo(
    () => sortStudentOverviewRows(
      attachStudentReports(buildStudentOverviewRows(machineRows), progress),
      sortMode,
    ),
    [machineRows, progress, sortMode],
  );
  const readyUserIds = useMemo(
    () => studentRows.filter((student) => student.report?.ready && student.userId).map((student) => student.userId),
    [studentRows],
  );

  const dirtyCount = useMemo(() => machineRows.filter((row) => (
    Boolean(row.target) && isReviewDraftDirty(drafts[row.key], targetTeacherReview(row.target))
  )).length, [machineRows, drafts]);
  const savedReplyByUser = useMemo(() => new Map(
    (progress?.students ?? []).map((row) => [String(row.user_id), String(row.teacher_reply ?? "")]),
  ), [progress]);
  const dirtyReplyCount = Object.entries(replyDrafts)
    .filter(([userId, text]) => text !== (savedReplyByUser.get(userId) ?? "")).length;
  hasDirtyDraftsRef.current = dirtyCount + dirtyReplyCount > 0 || savingKey !== null;

  useEffect(() => {
    onDirtyChange?.(dirtyCount + dirtyReplyCount > 0);
  }, [dirtyCount, dirtyReplyCount, onDirtyChange]);

  async function saveReply(userId) {
    const text = replyDrafts[userId] ?? savedReplyByUser.get(userId) ?? "";
    const saved = await AiJudgeService.saveStudentReply(classId, sessionId, userId, text);
    setProgress((current) => current && {
      ...current,
      students: (current.students ?? []).map((row) => (
        String(row.user_id) === String(userId)
          ? { ...row, teacher_reply: saved?.teacher_reply ?? "", teacher_reply_at: saved?.teacher_reply_at ?? null }
          : row
      )),
    });
    setReplyDrafts((current) => {
      if (current[userId] !== text) return current;
      const next = { ...current };
      delete next[userId];
      return next;
    });
  }
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  useEffect(() => {
    if (newResultsWaiting && dirtyCount + dirtyReplyCount === 0 && savingKey === null) loadReview();
  }, [newResultsWaiting, dirtyCount, dirtyReplyCount, savingKey, loadReview]);

  async function discardDraftsAndLoad() {
    const ok = await confirm({
      title: t("AiJudgePanel.discardReviewsTitle"),
      message: t("AiJudgePanel.discardReviewsMessage", { count: dirtyCount + dirtyReplyCount }),
      confirmText: t("AiJudgePanel.discardAndLoad"),
      danger: true,
    });
    if (!ok) return;
    setReplyDrafts({});
    hasDirtyDraftsRef.current = false;
    loadReview();
  }

  // 老師只需要兩件事：全班多少人打勾完成；檢查過的學生裡成功幾位、失敗幾位
  // （以學生為單位：通過＋未通過＋待確認＋尚未檢查＝全班）
  const summary = useMemo(() => studentRows.reduce((counts, student) => {
    counts.total += 1;
    if (student.report?.ready) counts.reported += 1;
    counts[checkOutcome(student.machines).kind] += 1;
    return counts;
  }, { total: 0, reported: 0, pass: 0, fail: 0, error: 0, pending: 0, none: 0 }), [studentRows]);
  const percentOf = (value) => (summary.total > 0 ? (value / summary.total) * 100 : 0);
  const checkedCount = summary.pass + summary.fail + summary.error + summary.pending;

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
    const draft = drafts[row.key] ?? reviewDraftFromTeacherReview(row.target?.teacher_review);
    if (!row.runId) throw new Error(t("AiJudgePanel.missingRunId"));
    if (row.vmid == null && !row.studentId) {
      throw new Error(t("AiJudgePanel.missingStudentId"));
    }
    const updated = row.vmid == null
      ? await AiJudgeService.updateStudentReview(
        classId,
        sessionId,
        row.runId,
        row.studentId,
        draft,
      )
      : await AiJudgeService.updateTargetReview(
        classId,
        sessionId,
        row.runId,
        row.vmid,
        draft,
      );
    const savedTarget = (updated?.target_results_json?.targets ?? [])
      .find((item) => (
        row.vmid != null ? String(item?.vmid) === String(row.vmid)
          : row.studentId != null && String(item?.student_id ?? "") === String(row.studentId)
      ));
    if (reviewState?.mode === "batch") {
      const savedReview = savedTarget?.teacher_review;
      setReviewState((current) => (
        current?.mode === "batch"
          ? {
            mode: "batch",
            batches: current.batches.map((entry) => (
              entry?.run_batch_id === row.batchId
                ? mergeNodeTeacherReview(entry, row, savedReview)
                : entry
            )),
          }
          : current
      ));
      setDrafts((current) => ({
        ...current,
        [row.key]: isReviewDraftDirty(current[row.key], draft)
          ? current[row.key] : reviewDraftFromTeacherReview(savedReview),
      }));
    } else if (reviewState?.mode === "run") {
      setReviewState((current) => current?.mode === "run" ? {
        mode: "run",
        run: {
          ...current.run,
          target_results_json: {
            ...current.run.target_results_json,
            targets: (current.run.target_results_json?.targets ?? []).map((target) => (
              (row.vmid != null ? String(target.vmid) === String(row.vmid)
                : String(target.student_id) === String(row.studentId))
                ? { ...target, teacher_review: savedTarget?.teacher_review } : target
            )),
          },
        },
      } : current);
      setDrafts((current) => ({
        ...current,
        [row.key]: isReviewDraftDirty(current[row.key], draft)
          ? current[row.key] : reviewDraftFromTeacherReview(savedTarget?.teacher_review),
      }));
    }
  }

  async function saveStudent(student) {
    if (savingStudentRef.current) return;
    savingStudentRef.current = true;
    setSavingKey(student.studentId);
    try {
      for (const row of student.machines) {
        if (row.target && isReviewDraftDirty(drafts[row.key], targetTeacherReview(row.target))) {
          await saveRow(row);
        }
      }
      if (student.userId && replyDrafts[student.userId] !== undefined
        && replyDrafts[student.userId] !== (savedReplyByUser.get(student.userId) ?? "")) {
        await saveReply(student.userId);
      }
      toast.success(t("AiJudgePanel.reviewSaved"));
    } catch (error) {
      toast.error(error?.message ?? t("AiJudgePanel.reviewSaveFailed"));
    } finally {
      savingStudentRef.current = false;
      setSavingKey(null);
    }
  }

  async function handleRunOnce() {
    await startBatch();
  }

  /* 只檢查指定學生（這位學生／已回報完成的人）：用最新核准的那組腳本，其他人的結果不動 */
  async function runForStudents(userIds) {
    if (userIds.length) await startBatch(userIds);
  }

  async function startBatch(userIds = null, retry = null) {
    if (submissionRef.current || creatingBatch || (batchRunning && !retry) || (!activeSet && !retry)) return;
    const attempt = retry ?? { runBatchId: newRunBatchId(), artifactSetId: activeSet.artifact_set_id, userIds };
    submissionRef.current = attempt;
    // 還在路上的核查讀取作廢：送出前的舊結果不能在建立後才蓋上來
    void judgeCache.cancelReview(classId, sessionId);
    setCreatingBatch(true);
    try {
      const next = await AiJudgeService.createSessionScriptSetRun(
        classId, sessionId, attempt.artifactSetId,
        { userIds: attempt.userIds, runBatchId: attempt.runBatchId },
      );
      if (submissionRef.current !== attempt) return;
      acceptBatch(next);
      if (!runIsTerminal(next.status)) {
        toast.success(t(attempt.userIds?.length ? "AiJudgePanel.batchCreatedStudents" : "AiJudgePanel.batchCreated", {
          count: attempt.userIds?.length ?? next?.summary?.targets ?? 0,
        }));
      }
    } catch (error) {
      if (submissionRef.current !== attempt) return;
      if (!error?.sessionChanged && (error?.timeout || !error?.status || error.status >= 500 || error.status === 409)) {
        setPendingBatch({ ...attempt, conflict: error?.status === 409, errorMessage: error?.message });
        setRunOnceOpen(false);
        toast.info(t("AiJudgePanel.batchRecovering"));
      } else {
        setPendingBatch(null);
        toast.error(error?.message ?? t("AiJudgePanel.batchCreateFailed"));
      }
    } finally {
      if (submissionRef.current === attempt) {
        submissionRef.current = null;
        setCreatingBatch(false);
      }
    }
  }

  function renderRunOnceDialog() {
    if (!runOnceDialog.open) return null;
    const children = Array.isArray(activeSet?.children) ? activeSet.children : [];
    return (
      <Modal
        closing={runOnceDialog.closing}
        onClose={() => setRunOnceOpen(false)}
        busy={creatingBatch}
        closeButton
        size="md"
        title={t("AiJudgePanel.runAllTitle")}
        description={t("AiJudgePanel.runAllDesc")}
        actions={
          <>
            <button
              type="button"
              className={styles.btnSecondary}
              onClick={() => setRunOnceOpen(false)}
              disabled={creatingBatch}
            >
              {t("AiJudgePanel.cancelBtn")}
            </button>
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={handleRunOnce}
              disabled={creatingBatch || batchRunning || !activeSet}
            >
              {creatingBatch ? t("AiJudgePanel.creating") : t("AiJudgePanel.confirmRun")}
            </button>
          </>
        }
      >
        <p>{t("AiJudgePanel.activeScriptSet", { version: activeSet?.source_analysis_revision ?? "—", count: children.length })}</p>

        <div className={styles.vmidBox}>
          <span className={styles.fieldLabel}>{t("AiJudgePanel.runScope", { count: children.length })}</span>
          <div className={styles.chipRow}>
            {children.map((child) => (
              <span key={child.id} className={styles.chip}>
                {batchMachineDisplayName(child.target_node_key, machineNodes, child.name) ?? t("AiJudgePanel.nodeNotSpecified")}
              </span>
            ))}
          </div>
        </div>
      </Modal>
    );
  }

  if (loading) return <LoadingState text={t("AiJudgePanel.loadingReview")} />;
  if (loadError) return <ErrorState onRetry={() => retryLoadReview()} />;
  // 讀得到全班名單就直接列出全班，看得到誰回報完成；
  // 要有核准的腳本、而且這週內容已發布（作業跟著每週內容發布）才可以檢查
  const showRoster = (progress?.students?.length ?? 0) > 0;
  const canRun = Boolean(activeSet) && weekPublished;
  const publicationNotice = activeSet && (
    <p className={styles.mutedText} role="status">
      {t("AiJudgePanel.activeScriptSet", { version: activeSet.source_analysis_revision ?? "—", count: activeSet.children?.length ?? 0 })}
      {activeSet.source_is_current === false && ` ${t("AiJudgePanel.activeScriptSetPending")}`}
    </p>
  );
  const recoveryNotice = pendingBatch && (
    <div className={`${styles.noticeInfo} ${styles.noticeWithAction}`} role="status">
      <p>{t("AiJudgePanel.batchRecovering")}</p>
      <button type="button" className={styles.btnSecondary} disabled={creatingBatch}
        onClick={() => startBatch(null, pendingBatch)}>
        {t("AiJudgePanel.batchRetryConfirmation")}
      </button>
    </div>
  );
  if (!reviewState && !showRoster && !activeBatch) {
    return (
      <div className={styles.tabBody} data-guide="ai-judge-review">
        <div className={styles.card}>
          {recoveryNotice}
          {Boolean(activeSet) ? (
            <>
              {publicationNotice}
              <EmptyState
                icon="rate_review"
                title={t("AiJudgePanel.noResultsTitle")}
                description={t("AiJudgePanel.noResultsReadyDesc")}
                action={(
                  <button
                    type="button"
                    className={styles.btnPrimary}
                    onClick={() => setRunOnceOpen(true)}
                    disabled={creatingBatch || batchRunning || !weekPublished}
                    title={weekPublished ? undefined : t("AiJudgePanel.reviewWeekDraftNotice")}
                  >
                    {creatingBatch ? <Spinner size={15} /> : <MIcon name="bolt" size={16} />}
                    {t("AiJudgePanel.runAllTitle")}
                  </button>
                )}
              />
            </>
          ) : (
            <EmptyState
              icon="rate_review"
              title={t("AiJudgePanel.noResultsTitle")}
              description={t("AiJudgePanel.noResultsNeedScriptDesc")}
              action={onGoToSettings && (
                <button type="button" className={styles.btnSecondary} onClick={onGoToSettings}>
                  <MIcon name="arrow_back" size={16} />{t("AiJudgePanel.goToSettings")}
                </button>
              )}
            />
          )}
        </div>
        {renderRunOnceDialog()}
      </div>
    );
  }

  return (
    <div className={styles.tabBody} data-guide="ai-judge-review">
      <div className={`${styles.card} ${styles.reviewOverview}`}>
        {publicationNotice}
        {recoveryNotice}
        <div className={styles.reviewOverviewHead}>
          <h4 className={styles.cardTitle}><MIcon name="rate_review" size={19} />{t("AiJudgePanel.tabReview")}</h4>
          <div className={styles.sectionActions}>
            {activeBatch && batchRunning && (
              <span className={styles.mutedText}>
                <Spinner size={14} />
                {t("AiJudgePanel.runProgress", { done: (activeBatch.summary?.completed ?? 0) + (activeBatch.summary?.failed ?? 0) + (activeBatch.summary?.cancelled ?? 0), total: activeBatch.summary?.targets ?? 0 })}
              </span>
            )}
            <button
              type="button"
              className={styles.btnSecondary}
              onClick={() => runForStudents(readyUserIds)}
              disabled={creatingBatch || batchRunning || !canRun || readyUserIds.length === 0}
              title={t("AiJudgePanel.runReadyHint")}
            >
              <MIcon name="how_to_reg" size={16} />
              {t("AiJudgePanel.runReadyBtn", { count: readyUserIds.length })}
            </button>
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={() => setRunOnceOpen(true)}
              disabled={creatingBatch || batchRunning || !canRun}
              title={!weekPublished
                ? t("AiJudgePanel.reviewWeekDraftNotice")
                : !activeSet
                  ? t("AiJudgePanel.runNoApprovedTitle")
                  : t("AiJudgePanel.runAllHint")}
            >
              {creatingBatch ? <Spinner size={15} /> : <MIcon name="bolt" size={16} />}
              {t("AiJudgePanel.runAllBtn")}
            </button>
          </div>
        </div>
        <div className={styles.reviewStats} aria-label={t("AiJudgePanel.metricsAria")}>
          <div className={styles.reviewStatLine}>
            <span className={styles.reviewStatLabel}><MIcon name="task_alt" size={16} />{t("AiJudgePanel.metricReportedRatio")}</span>
            <div
              className={styles.reviewStackBar}
              role="img"
              aria-label={t("AiJudgePanel.metricReportedValue", { done: summary.reported, total: summary.total })}
            >
              <span className={styles.reviewStackReported} style={{ width: `${percentOf(summary.reported)}%` }} />
            </div>
            <strong className={styles.reviewStatValue}>{summary.reported}<small>{` / ${summary.total}`}</small></strong>
          </div>
          <div className={styles.reviewStatLine}>
            <span className={styles.reviewStatLabel}><MIcon name="fact_check" size={16} />{t("AiJudgePanel.metricCheckResult")}</span>
            <div
              className={styles.reviewStackBar}
              role="img"
              aria-label={t("AiJudgePanel.metricCheckValue", { pass: summary.pass, fail: summary.fail, error: summary.error, checked: checkedCount, total: summary.total })}
            >
              <span className={styles.reviewStackPass} style={{ width: `${percentOf(summary.pass)}%` }} />
              <span className={styles.reviewStackFail} style={{ width: `${percentOf(summary.fail + summary.error)}%` }} />
              <span className={styles.reviewStackPending} style={{ width: `${percentOf(summary.pending)}%` }} />
            </div>
            <span className={styles.reviewStatValue}>
              <em className={styles.reviewStatPass}>{t("AiJudgePanel.metricPassCount", { count: summary.pass })}</em>
              <em className={styles.reviewStatFail}>{t("AiJudgePanel.metricFailCount", { count: summary.fail })}</em>
              {summary.error > 0 && <em className={styles.reviewStatFail}>{t("AiJudgePanel.metricErrorCount", { count: summary.error })}</em>}
              {summary.pending > 0 && <em className={styles.reviewStatPending}>{t("AiJudgePanel.metricPendingCount", { count: summary.pending })}</em>}
              <small>{t("AiJudgePanel.metricCheckedOf", { checked: checkedCount, total: summary.total })}</small>
            </span>
          </div>
        </div>
        {(!weekPublished || !activeSet) && (
          <div className={styles.reviewBlockers} role="status">
            {!weekPublished && (
              <div className={styles.reviewBlocker}>
                <MIcon name="unpublished" size={16} />
                <span>{t("AiJudgePanel.reviewWeekDraftNotice")}</span>
                {onGoToPublish && (
                  <button type="button" className={styles.btnText} onClick={onGoToPublish}>
                    {t("AiJudgePanel.goToPublish")}<MIcon name="arrow_forward" size={15} />
                  </button>
                )}
              </div>
            )}
            {!activeSet && (
              <div className={styles.reviewBlocker}>
                <MIcon name="code_off" size={16} />
                <span>{t("AiJudgePanel.reviewNoScriptNotice")}</span>
                {onGoToSettings && (
                  <button type="button" className={styles.btnText} onClick={onGoToSettings}>
                    {t("AiJudgePanel.goToSettings")}<MIcon name="arrow_forward" size={15} />
                  </button>
                )}
              </div>
            )}
          </div>
        )}
        {newResultsWaiting && (
          <div className={`${styles.noticeInfo} ${styles.noticeWithAction}`} role="status">
            <p>{t(reviewSetId && reviewSetId !== activeSet?.artifact_set_id ? "AiJudgePanel.previousSetDraftsWaiting" : "AiJudgePanel.newResultsWaiting")}</p>
            <button type="button" className={styles.btnSecondary} onClick={discardDraftsAndLoad}>
              {t("AiJudgePanel.discardReviewsBtn")}
            </button>
          </div>
        )}
        {activeBatch && (
          <div className={styles.runOnceProgress} aria-live="polite">
            {(activeBatch.nodes ?? []).map((node) => (
              <div className={styles.runOnceProgressLine} key={node.run_id}>
                <span>{batchMachineDisplayName(node.target_node_key, machineNodes, node.display_label) ?? "—"}</span>
                <StatusBadge map={RUN_STATUS} status={node.status} />
                {node.progress_json?.stage === "preparing" && <small>{t("AiJudgePanel.batchPreparing")}</small>}
                <small>{t("AiJudgePanel.nodeProgress", { done: node.progress_json?.done ?? 0, total: node.progress_json?.total ?? 0 })}</small>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className={styles.reviewListSection}>
        <div className={styles.reviewListToolbar}>
          <label className={styles.reviewSort}>
            <MIcon name="sort" size={16} />
            <span>{t("AiJudgePanel.sortLabel")}</span>
            <select value={sortMode} onChange={(event) => setSortMode(event.target.value)}>
              <option value="pending">{t("AiJudgePanel.sortPending")}</option>
              <option value="student-number">{t("AiJudgePanel.sortStudentNumber")}</option>
            </select>
          </label>
        </div>
        <div className={styles.reviewStudentList}>
          {studentRows.map((student) => {
          const user = student.user ?? {};
          const isOpen = expandedKey === student.studentId;
          const studentDirty = student.machines.some((row) => (
            row.target && isReviewDraftDirty(drafts[row.key], targetTeacherReview(row.target))
          )) || (student.userId && replyDrafts[student.userId] !== undefined
            && replyDrafts[student.userId] !== (student.reply?.text ?? ""));
          const savingStudent = savingKey === student.studentId;
          const savedAt = [student.reply?.at, ...student.machines.map((row) => row.target?.teacher_review?.updated_at)]
            .filter(Boolean).sort((a, b) => new Date(b) - new Date(a))[0];
          const hasSaved = savedAt || student.reply?.text || student.machines.some((row) => (
            Object.keys(targetTeacherReview(row.target).decisions).length > 0
          ));
          const outcome = checkOutcome(student.machines);
          const chips = studentMachineChips(student, members, machineNodes);
          const reportKind = student.report?.kind ?? "none";
          const reportLabel = reportKind === "ready"
            ? t("AiJudgePanel.reportReady")
            : reportKind === "partial"
              ? t("AiJudgePanel.reportPartial", { done: student.report.done, total: student.report.total })
              : t("AiJudgePanel.reportNone");
          return (
            <article className={`${styles.reviewStudent} ${isOpen ? styles.reviewStudentOpen : ""}`} key={student.studentId}>
              <button
                type="button"
                className={styles.reviewStudentToggle}
                onClick={() => setExpandedKey(isOpen ? null : student.studentId)}
                aria-expanded={isOpen}
              >
                <span className={styles.reviewStudentIdentity}>
                  <span
                    className={`${styles.reportTick} ${styles[`reportTick_${reportKind}`] ?? ""}`}
                    title={reportLabel}
                    aria-label={reportLabel}
                    role="img"
                  >
                    {reportKind === "ready" && <MIcon name="check" size={18} />}
                    {reportKind === "partial" && <small>{`${student.report.done}/${student.report.total}`}</small>}
                  </span>
                  <span className={styles.reviewStudentMain}>
                    <span className={styles.reviewStudentName}>
                      <strong>{user.full_name ?? t("AiJudgePanel.unnamedStudent")}</strong>
                      <em className={styles[`reportText_${reportKind}`]}>{reportLabel}</em>
                      {student.reply?.text && (
                        <em className={styles.replyMark}><MIcon name="forum" size={13} />{t("AiJudgePanel.replied")}</em>
                      )}
                    </span>
                    {user.email && <small>{user.email}</small>}
                    <span className={styles.machineChips} aria-label={t("AiJudgePanel.studentMachinesAria")}>
                      {chips.length === 0 ? (
                        <em className={styles.machineChipEmpty}>{t("AiJudgePanel.studentNoMachine")}</em>
                      ) : chips.map((machine) => (
                        <em
                          key={machine.key}
                          className={`${styles.machineChip} ${OUTCOME_CHIP_CLASS[machine.outcome]}`}
                          title={[
                            machine.label,
                            machine.vmid != null ? `VMID ${machine.vmid}` : null,
                            t(`AiJudgePanel.machinePower_${machine.power}`),
                          ].filter(Boolean).join(" · ")}
                        >
                          <span
                            className={`${styles.powerDot} ${POWER_DOT_CLASS[machine.power]}`}
                            role="img"
                            aria-label={t(`AiJudgePanel.machinePower_${machine.power}`)}
                          />
                          {machine.label}
                        </em>
                      ))}
                    </span>
                  </span>
                </span>
                <span className={styles.reviewStudentBadges}>
                  <span className={`${styles.badge} ${OUTCOME_BADGE_CLASS[outcome.kind]}`}>
                    {t(`AiJudgePanel.outcome_${outcome.kind}`, { count: outcome.count })}
                  </span>
                </span>
                <MIcon name={isOpen ? "expand_less" : "expand_more"} size={20} />
              </button>

              {isOpen && (
                <div className={styles.reviewStudentBody}>
                  <div className={styles.reviewStudentActions}>
                    {student.machines.length === 0 && (
                      <span className={styles.mutedText}>{t("AiJudgePanel.studentNotCheckedYet")}</span>
                    )}
                    {student.userId && (
                      <button
                        type="button"
                        className={styles.btnSecondary}
                        onClick={() => runForStudents([student.userId])}
                        disabled={creatingBatch || batchRunning || !canRun}
                      >
                        <MIcon name="play_arrow" size={16} />
                        {t("AiJudgePanel.runStudentBtn")}
                      </button>
                    )}
                  </div>
                  {student.machines.map((row) => {
                    const draft = drafts[row.key] ?? reviewDraftFromTeacherReview(row.target?.teacher_review);
                    return (
                      <section className={styles.reviewMachineCard} key={row.key} aria-label={row.nodeKey ?? `VMID ${row.vmid ?? ""}`}>
                        <MachineReviewDetail
                          row={row}
                          machineNodes={machineNodes}
                          draft={draft}
                          onToggleDecision={toggleDecision}
                        />
                      </section>
                    );
                  })}
                  {/* 看完所有機器的結果後回覆；按鈕與 Ctrl／⌘+Enter 都會一起儲存判定與回覆。 */}
                  {student.userId && (() => {
                    const saved = student.reply?.text ?? "";
                    const value = replyDrafts[student.userId] ?? saved;
                    return (
                      <label
                        className={styles.replyBar}
                        title={student.reply?.at ? t("AiJudgePanel.replyLastSaved", { time: formatDateTime(student.reply.at) }) : undefined}
                      >
                        <span>{t("AiJudgePanel.replyLabel")} <small>{t("AiJudgePanel.optionalShort")}</small></span>
                        <textarea
                          className={styles.replyInput}
                          value={value}
                          maxLength={REPLY_MAX_LENGTH}
                          rows={1}
                          aria-label={t("AiJudgePanel.replyLabel")}
                          placeholder={t("AiJudgePanel.replyPlaceholder")}
                          onChange={(event) => {
                            const next = event.target.value;
                            setReplyDrafts((current) => ({ ...current, [student.userId]: next }));
                          }}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" && !event.nativeEvent.isComposing
                              && (event.ctrlKey || event.metaKey) && studentDirty && savingKey === null) {
                              event.preventDefault();
                              saveStudent(student);
                            }
                          }}
                        />
                        {value.length > REPLY_COUNTER_FROM && <small className={styles.replyCount}>{value.length} / {REPLY_MAX_LENGTH}</small>}
                      </label>
                    );
                  })()}
                  {(student.userId || student.machines.some((row) => row.target)) && (
                    <div className={styles.reviewSaveRow}>
                      <span>{studentDirty ? t("AiJudgePanel.unsavedChanges")
                        : hasSaved ? t("AiJudgePanel.lastSaved", { time: savedAt ? formatDateTime(savedAt) : t("AiJudgePanel.saved") })
                          : t("AiJudgePanel.studentSaveHint")}</span>
                      <button type="button" className={studentDirty ? styles.btnPrimary : styles.btnSecondary}
                        disabled={!studentDirty || savingKey !== null} onClick={() => saveStudent(student)}>
                        {savingStudent ? <><Spinner size={15} />{t("AiJudgePanel.saving")}</>
                          : <><MIcon name="save" size={16} />{t("AiJudgePanel.saveStudentBtn")}</>}
                      </button>
                    </div>
                  )}
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

// 依實際流程排序：設定檢查表 → 核准腳本 → 執行並核查
const TEACHER_JUDGE_TABS = [
  { key: "rubrics", icon: "description", buttonProps: { "data-guide-tab": "ai-judge-tab-rubrics" }, get label() { return jt("tabRubrics"); } },
  { key: "review", icon: "rate_review", buttonProps: { "data-guide-tab": "ai-judge-tab-review" }, get label() { return jt("tabReview"); } },
];

const NO_VISITED_TABS = new Set();

function TeacherWorkspacePanel({ classId, members, weeks = [], machineNodes = [] }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  const { confirmLeave } = useUnsavedChanges();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedWeekId = searchParams.get("week");
  // 只在選預設週次時參考網址；寫回網址不該觸發重新選擇
  const requestedWeekIdRef = useRef(requestedWeekId);
  requestedWeekIdRef.current = requestedWeekId;
  const [activeTab, setActiveTab] = useState("rubrics");
  // 同一個檢查裡看過的分頁保持掛著（隱藏），切分頁時提案、打到一半的訊息、核查草稿都還在；
  // 換週次才整組重來
  const [visitedTabs, setVisitedTabs] = useState({ sessionId: null, tabs: NO_VISITED_TABS });
  const [dirtyTabs, setDirtyTabs] = useState({ rubrics: false, review: false });
  /* 班級的檢查會話（一週一個）；製作腳本、開始執行後靜默重抓，讀取失敗時清單當成空的並提示 */
  const sessionsQuery = useAiJudgeSessionsQuery(classId);
  const sessions = sessionsQuery.data ?? NO_JUDGE_SESSIONS;
  const { loading } = useQueryView(sessionsQuery);
  useQueryErrorEffect(sessionsQuery, (error) => toast.error(error?.message ?? jt("loadChecksFailed")));
  const judgeCache = useAiJudgeCacheUpdater();
  const [activeWeekId, setActiveWeekId] = useState(null);
  const [creatingWeekId, setCreatingWeekId] = useState(null);
  const [openMenuId, setOpenMenuId] = useState(null);
  const [sessionMenuPosition, setSessionMenuPosition] = useState(null);
  // 選單離場動畫：關閉時保留最後的目標與位置 130ms
  const sessionMenuPos = useDialogPresence(sessionMenuPosition, 130);
  const [busySessionIds, setBusySessionIds] = useState(() => new Set());
  // 移到／複製到其他週：{ mode: "move" | "copy", item }
  const [weekPicker, setWeekPicker] = useState(null);
  const [weekPickerId, setWeekPickerId] = useState("");
  // 只在第一次掛上時讀；之後拖拉直接改 DOM 上的 CSS 變數，不經過 React
  const [initialColumnWidths] = useState(readStoredColumnWidths);
  const classIdRef = useRef(classId);
  const closeSessionMenu = useCallback(() => {
    setOpenMenuId(null);
    setSessionMenuPosition(null);
  }, []);

  const sortedWeeks = useMemo(
    () => [...weeks].sort((a, b) => (a.week_number ?? 0) - (b.week_number ?? 0)),
    [weeks],
  );
  // 一週一個對話：用週次找對話；沒掛週次的舊對話不出現在清單上
  const sessionByWeek = useMemo(() => {
    const map = new Map();
    for (const item of sessions) {
      if (item.teaching_class_week_id) map.set(String(item.teaching_class_week_id), item);
    }
    return map;
  }, [sessions]);
  const activeWeek = useMemo(
    () => sortedWeeks.find((week) => String(week.id) === String(activeWeekId)) ?? null,
    [activeWeekId, sortedWeeks],
  );
  const activeSession = activeWeekId ? sessionByWeek.get(String(activeWeekId)) ?? null : null;
  const activeSessionId = activeSession?.id ?? null;
  const freeWeeks = sortedWeeks.filter((week) => !sessionByWeek.has(String(week.id)));
  const openSessionMenuItem = useMemo(
    () => sessions.find((item) => item.id === openMenuId) ?? null,
    [openMenuId, sessions],
  );
  const sessionMenuItemKeep = useDialogPresence(openSessionMenuItem, 130);
  const tabsIdPrefix = useId();

  const reportRubricsDirty = useCallback((value) => {
    setDirtyTabs((current) => (current.rubrics === value ? current : { ...current, rubrics: value }));
  }, []);
  const reportReviewDirty = useCallback((value) => {
    setDirtyTabs((current) => (current.review === value ? current : { ...current, review: value }));
  }, []);
  useUnsavedChangesGuard(dirtyTabs.rubrics || dirtyTabs.review);

  function selectTab(key) {
    setVisitedTabs((current) => {
      const base = current.sessionId === activeSessionId ? current.tabs : NO_VISITED_TABS;
      return { sessionId: activeSessionId, tabs: new Set([...base, activeTab, key]) };
    });
    setActiveTab(key);
  }

  // 週次在班級的「每週內容」發布；離開前一樣先確認沒存的內容
  async function goToWeeklyContent() {
    if (await confirmLeave()) navigate(`/class-management/${classId}/weekly`);
  }

  // 換週次會卸掉目前的分頁：有沒存的提案、訊息或核查草稿先確認
  async function selectWeek(weekId) {
    closeSessionMenu();
    if (String(weekId) === String(activeWeekId)) return;
    if (!(await confirmLeave())) return;
    // 換週次停在同一個分頁：在導師核查逐週看進度時不會被拉回任務設定
    setActiveWeekId(String(weekId));
  }

  /* 製作腳本、執行後的背景更新：已有清單時不閃成轉圈 */
  const reloadSessions = useCallback(() => {
    void judgeCache.invalidateSessions(classId);
  }, [judgeCache, classId]);

  // 換班級：清掉跟上一個班級綁在一起的選取與對話框（清單本身依班級各自一份快取）
  useEffect(() => {
    classIdRef.current = classId;
    setCreatingWeekId(null);
    setWeekPicker(null);
    setActiveWeekId(null);
    closeSessionMenu();
  }, [classId, closeSessionMenu]);

  // 預設選週次：網址指定的有效週次 > 第一週；目前選的週次不見了才重選
  useEffect(() => {
    setActiveWeekId((current) => (
      current && sortedWeeks.some((week) => String(week.id) === String(current))
        ? current
        : resolveActiveWeekId(requestedWeekIdRef.current, sortedWeeks)
    ));
  }, [sortedWeeks]);

  useEffect(() => {
    closeSessionMenu();
  }, [activeWeekId, closeSessionMenu]);

  // 選中的週次寫進網址（?week=），重新整理或分享連結都會回到同一週；用 replace 不塞歷史紀錄
  useEffect(() => {
    if (loading || !activeWeekId) return;
    if (String(activeWeekId) === String(requestedWeekId ?? "")) return;
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("week", activeWeekId);
      next.delete("check");
      return next;
    }, { replace: true });
  }, [activeWeekId, loading, requestedWeekId, setSearchParams]);

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
    judgeCache.updateSession(classId, updated);
  }

  function upsertSession(item) {
    judgeCache.upsertSession(classId, item);
  }

  /* 這一週還沒有檢查：建立（伺服器端保證一週只會有一個，重複按也拿回同一個） */
  async function createWeekCheck(week) {
    if (!week || creatingWeekId) return;
    const requestClassId = classId;
    setCreatingWeekId(String(week.id));
    try {
      const created = await AiJudgeService.ensureWeekSession(classId, week.id);
      if (classIdRef.current !== requestClassId) return;
      upsertSession(created);
      setActiveTab("rubrics");
      toast.success(t("AiJudgePanel.checkCreated", { title: created.title }));
    } catch (error) {
      if (classIdRef.current === requestClassId) {
        toast.error(error?.message ?? t("AiJudgePanel.createCheckFailed"));
      }
    } finally {
      if (classIdRef.current === requestClassId) setCreatingWeekId(null);
    }
  }

  async function runSessionAction(item, action) {
    if (!item || busySessionIds.has(item.id)) return null;
    const requestClassId = classId;
    setBusySessionIds((current) => new Set(current).add(item.id));
    closeSessionMenu();
    try {
      const updated = await action(item);
      if (classIdRef.current !== requestClassId) return null;
      if (updated) {
        if (updated.status === "deleted") {
          judgeCache.removeSession(classId, item.id);
        } else {
          updateSessionInList(updated);
        }
      }
      return updated;
    } catch (error) {
      if (classIdRef.current === requestClassId) {
        toast.error(error?.message ?? t("AiJudgePanel.checkActionFailed"));
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

  function openWeekPicker(mode, item) {
    closeSessionMenu();
    setWeekPicker({ mode, item });
    setWeekPickerId("");
  }

  /* 移到或複製到另一個還沒有檢查的週次，完成後跟著切到那一週 */
  async function submitWeekPicker(event) {
    event.preventDefault();
    const picker = weekPicker;
    if (!picker || !weekPickerId || busySessionIds.has(picker.item.id)) return;
    if (picker.mode === "copy") {
      if (!(await confirmLeave())) return;
      const copy = await runSessionAction(picker.item, (entry) => (
        AiJudgeService.forkSession(classId, entry.id, { weekId: weekPickerId })
      ));
      if (!copy) return;
      upsertSession(copy);
      setWeekPicker(null);
      setActiveWeekId(String(weekPickerId));
      setActiveTab("rubrics");
      toast.success(t("AiJudgePanel.checkCopied", { title: copy.title }));
      return;
    }
    const moved = await runSessionAction(picker.item, (entry) => (
      AiJudgeService.updateSession(classId, entry.id, { teaching_class_week_id: weekPickerId })
    ));
    if (!moved) return;
    setWeekPicker(null);
    if (picker.item.id === activeSessionId) setActiveWeekId(String(weekPickerId));
    toast.success(t("AiJudgePanel.checkMoved", { title: picker.item.title }));
  }

  async function deleteSession(item) {
    /* 檢查表與其所有檢查資料一併消失，無法復原：先確認 */
    closeSessionMenu();
    const ok = await confirm({
      title: t("AiJudgePanel.deleteCheckTitle"),
      message: t("AiJudgePanel.deleteCheckMessage", { title: item.title }),
      confirmText: t("AiJudgePanel.opDelete"),
      danger: true,
    });
    if (!ok) return;
    const deleted = await runSessionAction(item, async (entry) => {
      await AiJudgeService.deleteSession(classId, entry.id);
      return { ...entry, status: "deleted" };
    });
    if (deleted) toast.success(t("AiJudgePanel.checkDeleted", { title: item.title }));
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
    const noFreeWeek = freeWeeks.length === 0;
    return (
      <div
        id={`check-menu-${item.id}`}
        className={`${styles.sessionMenu} ${sessionMenuPos.closing ? styles.sessionMenuOut : ""}`}
        role="menu"
        aria-label={t("AiJudgePanel.moreActionsAria", { title: item.title })}
        style={{ top: `${menuPos.top}px`, left: `${menuPos.left}px` }}
      >
        <button type="button" role="menuitem" disabled={busy || noFreeWeek} title={noFreeWeek ? t("AiJudgePanel.noFreeWeeksHint") : undefined} onClick={() => openWeekPicker("move", item)}><MIcon name="calendar_month" size={16} />{t("AiJudgePanel.moveWeekBtn")}</button>
        <button type="button" role="menuitem" disabled={busy || noFreeWeek} title={noFreeWeek ? t("AiJudgePanel.noFreeWeeksHint") : undefined} onClick={() => openWeekPicker("copy", item)}><MIcon name="content_copy" size={16} />{t("AiJudgePanel.copyToWeekBtn")}</button>
        <span className={styles.menuSeparator} />
        <button type="button" role="menuitem" className={styles.menuDanger} disabled={busy} onClick={() => deleteSession(item)}><MIcon name="delete" size={16} />{t("AiJudgePanel.opDelete")}</button>
      </div>
    );
  }

  const sessionSidebarInner = (
    <>
      {/* 分頁放在週次清單上方的標頭列：跟表格欄位標頭、助理標頭同一條 */}
      <div className={styles.sidebarHead}>
        <SegmentedControl
          ariaLabel={t("AiJudgePanel.tabsAria")}
          className={styles.sidebarTabs}
          options={TEACHER_JUDGE_TABS.map((tab) => ({ value: tab.key, label: tab.label, icon: tab.icon, buttonProps: tab.buttonProps }))}
          value={activeTab}
          onChange={selectTab}
        />
      </div>
      <div className={styles.sidebarBody}>
        {loading ? (
          <div className={styles.sidebarLoading} role="status" aria-label={t("AiJudgePanel.loading")}><LoadingSpinner size={44} /></div>
        ) : sortedWeeks.length === 0 ? null : (
          <div className={styles.sessionList} role="list">
            {sortedWeeks.map((week) => {
              const item = sessionByWeek.get(String(week.id)) ?? null;
              const selected = String(week.id) === String(activeWeekId);
              const busy = item ? busySessionIds.has(item.id) : false;
              const topic = String(week.title ?? "").trim();
              const date = String(week.session_date ?? "").slice(5, 10).replace("-", "/");
              return (
                <div key={week.id} className={`${styles.sessionRow} ${selected ? styles.sessionRowActive : ""}`} role="listitem">
                  <button type="button" className={selected ? styles.sessionItemActive : styles.sessionItem} aria-current={selected ? "true" : undefined} onClick={() => selectWeek(week.id)}>
                    <SessionTitle title={topic || t("AiJudgePanel.weekNoTopic")}>
                      {t("AiJudgePanel.weekRowTitle", { week: week.week_number })}
                      <span className={topic ? styles.weekRowTopic : styles.weekRowTopicEmpty}>{topic || t("AiJudgePanel.weekNoTopic")}</span>
                    </SessionTitle>
                    <small className={styles.sessionWeekLabel}>
                      <span className={item ? styles.weekDotReady : styles.weekDot} aria-hidden="true" />
                      {date && `${date} · `}{item ? t("AiJudgePanel.weekHasCheck") : t("AiJudgePanel.weekNoCheck")}
                      {!isWeekPublished(week) && ` · ${t("AiJudgePanel.weekDraft")}`}
                    </small>
                  </button>
                  {item && (
                    <div className={styles.sessionRowActions}>
                      <button type="button" className={styles.menuBtn} aria-label={t("AiJudgePanel.moreActionsAria", { title: item.title })} title={t("AiJudgePanel.moreActions")} aria-haspopup="menu" aria-expanded={openMenuId === item.id} aria-controls={`check-menu-${item.id}`} disabled={busy} onClick={(event) => toggleSessionMenu(event, item.id)}><MIcon name="more_vert" size={18} /></button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );

  const visited = visitedTabs.sessionId === activeSessionId ? visitedTabs.tabs : NO_VISITED_TABS;
  const tabMounted = (key) => activeTab === key || visited.has(key);

  function tabPanel(key, children, active = activeTab === key, className = "") {
    const tab = TEACHER_JUDGE_TABS.find((entry) => entry.key === key);
    return (
      <section
        id={`${tabsIdPrefix}-panel-${key}`}
        aria-label={tab?.label}
        hidden={!active}
        className={`${styles.checkTabPanel} ${className}`}
      >
        {children}
      </section>
    );
  }

  const pickerItem = weekPicker?.item ?? null;
  const pickerIsCopy = weekPicker?.mode === "copy";

  return (
    <div className={styles.panel}>
      {/* 週次清單、分頁列、分頁內容是同一塊面板，欄位之間只用分隔線區隔 */}
      <section
        className={styles.checkWorkspaceTwo}
        aria-label={t("AiJudgePanel.workspaceAria")}
        data-judge-workspace=""
        style={initialColumnWidths}
      >
        <aside className={styles.checkSessionCol} data-guide="ai-judge-sessions" aria-label={t("AiJudgePanel.checkListAria")}>
          {sessionSidebarInner}
        </aside>
        <ColumnResizer
          column="week"
          side="left"
          label={t("AiJudgePanel.resizeWeekAria")}
          measureSelector='[data-guide="ai-judge-sessions"]'
          otherSelector='[data-guide="ai-judge-assistant"]'
        />
        <div className={styles.checkContentCol}>
          {activeSession ? (
            <>
              {tabMounted("rubrics") && tabPanel("rubrics", (
                <RubricsTab
                  key={activeSession.id}
                  classId={classId}
                  judgeSession={activeSession}
                  onSessionUpdated={updateSessionInList}
                  onDirtyChange={reportRubricsDirty}
                  machineNodes={machineNodes}
                  onScriptCreated={(artifact) => {
                    reloadSessions();
                    selectTab(getScriptCreationDestination(artifact));
                  }}
                />
              ))}
              {tabMounted("review") && tabPanel("review", (
                <TeacherReviewTab
                  key={activeSession.id}
                  classId={classId}
                  sessionId={activeSession.id}
                  members={members}
                  machineNodes={machineNodes}
                  active={activeTab === "review"}
                  weekPublished={isWeekPublished(activeWeek)}
                  onDirtyChange={reportReviewDirty}
                  onGoToSettings={() => selectTab("rubrics")}
                  onGoToPublish={goToWeeklyContent}
                  onRunStarted={reloadSessions}
                />
              ), activeTab === "review", styles.checkTabPanelPadded)}
            </>
          ) : loading ? (
            <div className={styles.checkPane}>
              <LoadingState text={t("AiJudgePanel.loadingChecks")} />
            </div>
          ) : activeWeek ? (
            <div className={styles.checkPane} data-guide="ai-judge-empty">
              <EmptyWeekHero
                week={activeWeek}
                busy={creatingWeekId === String(activeWeek.id)}
                onCreate={() => createWeekCheck(activeWeek)}
              />
            </div>
          ) : (
            <div className={styles.checkPane} data-guide="ai-judge-empty">
              <EmptyState
                icon="calendar_month"
                title={t("AiJudgePanel.noWeeksTitle")}
                description={t("AiJudgePanel.noWeeksDesc")}
              />
            </div>
          )}
        </div>
      </section>

      {typeof document !== "undefined" && sessionMenuItemKeep.open && sessionMenuPos.item && createPortal(renderSessionMenu(sessionMenuItemKeep.item), document.body)}

      {pickerItem && (
        <Modal
          as="form"
          onSubmit={submitWeekPicker}
          onClose={() => setWeekPicker(null)}
          title={pickerIsCopy ? t("AiJudgePanel.copyWeekTitle") : t("AiJudgePanel.moveWeekTitle")}
          description={pickerIsCopy
            ? t("AiJudgePanel.copyWeekDesc", { title: pickerItem.title })
            : t("AiJudgePanel.moveWeekDesc", { title: pickerItem.title })}
          actions={
            <>
              <button type="button" className={styles.btnSecondary} onClick={() => setWeekPicker(null)}>{freeWeeks.length ? t("AiJudgePanel.cancelBtn") : t("AiJudgePanel.closeBtn")}</button>
              {freeWeeks.length > 0 && (
                <button type="submit" className={styles.btnPrimary} disabled={!weekPickerId || busySessionIds.has(pickerItem.id)}>
                  {pickerIsCopy ? t("AiJudgePanel.copyBtn") : t("AiJudgePanel.saveWeekBtn")}
                </button>
              )}
            </>
          }
        >
          <WeekSelectField weeks={freeWeeks} value={weekPickerId} onChange={setWeekPickerId} autoFocus />
        </Modal>
      )}
    </div>
  );
}

export default function AiJudgePanel({ classId, members, weeks = [], machineNodes = [] }) {
  return <TeacherWorkspacePanel classId={classId} members={members} weeks={weeks} machineNodes={machineNodes} />;
}
