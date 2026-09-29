import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import i18n from "../../../i18n";
import styles from "./AiJudgePanel.module.scss";
import LoadingState, { LoadingSpinner } from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { useToast } from "../../../hooks/useToast";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useUnsavedChanges, useUnsavedChangesGuard } from "../../../contexts/UnsavedChangesContext";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import useDialogPresence from "../../../hooks/useDialogPresence";
import useFileDrop from "../../../hooks/useFileDrop";
import FileDropOverlay from "../../../components/FileDropzone/FileDropOverlay";
import { downloadBlob } from "../../../services/api";
import { focusInvalidField } from "../../../utils/focusField";
import { formatDateTime } from "../../../utils/formatDate";
import { joinList } from "../../../utils/joinList";
import { createRubricAnalysisAutosave } from "./rubricAnalysisAutosave";
import {
  AiJudgeService,
  RUBRIC_POLISH_PROMPT,
  getTemplateLabel,
  shouldDisplayChatMessage,
} from "../../../services/aiJudge";

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
    chips.push({ key: "argv", label: jt("chipCommand"), mono: true, parts: argv });
  }
  if (typeof parameters.cwd === "string" && parameters.cwd.trim()) {
    chips.push({ key: "cwd", label: jt("chipWorkingDir"), mono: true, parts: [parameters.cwd.trim()] });
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

  if (typeof parameters.cwd === "string") {
    addTarget("cwd", jt("chipWorkingDir"), "folder_open", parameters.cwd);
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
  if (pendingProposal) return jt("blockerProposal");
  if (items.length === 0) return jt("blockerNoItems");
  const reviewIds = getRubricReviewItemIds(analysis, pendingReviewIds);
  if (reviewIds.size > 0) {
    return jt("blockerNeedsReview");
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

/** 還沒有任何檢查時的中央空狀態；新增入口在左側清單頂端（有檢查時一律自動選最近的一項）。 */
export function EmptyCheckHero() {
  const { t } = useTranslation("teaching");
  return (
    <EmptyState
      icon="checklist"
      title={t("AiJudgePanel.noChecksTitle")}
      description={t("AiJudgePanel.noChecksDesc")}
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

function RubricTableRow({ item, index, onChange, onDelete, disabled, needsReview, machineNodes = [] }) {
  const { t } = useTranslation("teaching");
  const [expanded, setExpanded] = useState(false);
  const checkSteps = item.check_steps ?? [];
  const targetSummaries = getRubricTargetSummaries(item);
  const executionNode = getExecutionNodeSummary(item, machineNodes);
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
            aria-label={t(expanded ? "AiJudgePanel.collapseRowAria" : "AiJudgePanel.expandRowAria", { index: index + 1 })}
            title={expanded ? t("AiJudgePanel.collapseRowTitle") : t("AiJudgePanel.expandRowTitle")}
            onClick={() => setExpanded((current) => !current)}
          >
            <MIcon name={expanded ? "expand_less" : "expand_more"} size={17} aria-hidden="true" />
          </button>
        </td>
        <td className={styles.rubricNumberCell}>{index + 1}</td>
        <td>
          <label className={styles.tableField}>
            <span className={styles.srOnly}>{t("AiJudgePanel.rowTitleLabel", { index: index + 1 })}</span>
            <input
              value={item.title}
              onChange={(event) => onChange({ ...item, title: event.target.value })}
              placeholder={t("AiJudgePanel.rowTitlePlaceholder")}
              disabled={disabled}
            />
          </label>
        </td>
        <td className={styles.rubricDescriptionCell}>
          <div className={styles.tableField}>
            <span className={styles.srOnly}>{t("AiJudgePanel.rowMethodLabel", { index: index + 1 })}</span>
            <p className={`${styles.rubricMethodText} ${!item.detection_method ? styles.rubricMethodTextEmpty : ""}`}>
              {item.detection_method || t("AiJudgePanel.noMethod")}
            </p>
            {targetSummaries.length > 0 && (
              <div className={styles.rubricTargetSummary} aria-label={t("AiJudgePanel.targetsLabel")}>
                <span className={styles.rubricTargetLabel}>{t("AiJudgePanel.targetsLabel")}</span>
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
              className={styles.iconBtnDanger}
              title={t("AiJudgePanel.deleteItemTitle")}
              aria-label={t("AiJudgePanel.deleteItemAria", { index: index + 1, title: item.title || t("AiJudgePanel.unnamedItem") })}
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
  return (
    <div className={styles.rubricTableWrap}>
      <table className={styles.rubricTable}>
        <caption className={styles.srOnly}>{t("AiJudgePanel.tableCaption")}</caption>
        <thead>
          <tr>
            <th scope="col">
              <span className={styles.srOnly}>{t("AiJudgePanel.thDetails")}</span>
            </th>
            <th scope="col">#</th>
            <th scope="col">{t("AiJudgePanel.thCheckpoint")}</th>
            <th scope="col">{t("AiJudgePanel.thMethod")}</th>
            <th scope="col">{t("AiJudgePanel.thAutoSupport")}</th>
            <th scope="col"><span className={styles.srOnly}>{t("AiJudgePanel.thActions")}</span></th>
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
    if (call.status === "staged") {
      const label =
        call.operation === "update"
          ? jt("toolProposalUpdated")
          : jt("toolProposalCreated");
      lines.push({ icon: "check_circle", text: jt("labelValue", { label, value: call.title ?? "" }) });
    } else if (call.status === "rejected") {
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
}) {
  const { t } = useTranslation("teaching");
  const [input, setInput] = useState("");
  const fileInputRef = useRef(null);
  const messagesEndRef = useRef(null);
  const visibleMessages = messages.filter(shouldDisplayChatMessage);
  const hasDraft = Boolean(input.trim());

  useEffect(() => {
    onDraftChange?.(hasDraft);
  }, [hasDraft, onDraftChange]);

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
  // 空對話（無可顯示訊息且非載入中）走中央 Hero Composer：置中 ✦＋標題＋圓角輸入框；
  // 有訊息或載入中則維持訊息串＋底部輸入的既有版面。
  const isEmpty = visibleMessages.length === 0 && !isLoading;
  const heroPlaceholder = t("AiJudgePanel.chatHeroPlaceholder");

  /* 以下幾塊兩種版面（中央 Hero／底部輸入列）共用；同一時間只會畫其中一種版面 */
  const attachmentRail = (
    <ChatAttachmentRail attachments={pendingAttachments} onRemove={onRemoveAttachment} disabled={!canInteract} />
  );
  const attachmentInput = onUploadFile && (
    <input
      ref={fileInputRef}
      type="file"
      accept=".md,.txt,.doc,.docx,.pdf"
      className={styles.srOnly}
      tabIndex={-1}
      onChange={handleAttachmentInput}
    />
  );
  const sourcesToggle = onToggleSources && <button
    type="button"
    className={styles.btnSecondary}
    disabled={!canInteract}
    onClick={onToggleSources}
    aria-expanded={sourcesOpen}
    aria-controls="ai-chat-data-sources"
  >
    <MIcon name="description" size={14} />
    {t("AiJudgePanel.sourcesBtn")}
  </button>;
  const sourcesPanel = sourcesOpen && sourcesContent && (
    <div id="ai-chat-data-sources" className={styles.chatSourcesPanel}>
      {sourcesContent}
    </div>
  );

  return (
    <div className={`${styles.chatPanel} ${isEmpty ? styles.chatPanelEmpty : ""}`} {...(onUploadFile ? dropProps : {})}>
      <div className={styles.chatMessages}>
        {isEmpty ? (
          <div className={styles.chatHero} data-chat-empty-hero="true">
            <MIcon name="fact_check" size={44} className={styles.chatHeroIcon} />
            <h3 className={styles.chatHeroTitle}>{t("AiJudgePanel.chatHeroTitle")}</h3>
            <p className={styles.chatHeroDesc}>
              {t("AiJudgePanel.chatHeroDesc")}
            </p>
            {attachmentRail}
            <form
              className={styles.chatHeroComposer}
              aria-label={t("AiJudgePanel.chatFormAria")}
              onSubmit={(e) => {
                e.preventDefault();
                send();
              }}
            >
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                placeholder={heroPlaceholder}
                rows={3}
                aria-label={t("AiJudgePanel.chatInputAria")}
                disabled={!canInteract}
              />
              <div className={styles.chatHeroFooter}>
                {onUploadFile ? (
                  <>
                    {attachmentInput}
                    <button
                      type="button"
                      className={styles.btnSecondary}
                      disabled={!canInteract}
                      aria-label={t("AiJudgePanel.addAttachmentAria")}
                      title={t("AiJudgePanel.attachFileTitle")}
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <MIcon name="add" size={16} />
                      {t("AiJudgePanel.attachFileBtn")}
                    </button>
                  </>
                ) : <span />}
                <button
                  type="submit"
                  className={`${styles.btnSecondary} ${styles.chatHeroSend}`}
                  disabled={!canSend}
                  aria-label={t("AiJudgePanel.sendAria")}
                  title={t("AiJudgePanel.sendAria")}
                >
                  <MIcon name="arrow_upward" size={18} />
                </button>
              </div>
            </form>
            {sourcesToggle}
            {sourcesPanel}
          </div>
        ) : (
          visibleMessages.map((msg, i) => (
            <div
              key={`${msg.role}-${i}`}
              className={`${styles.chatMsgRow} ${msg.role === "user" ? styles.chatMsgRow_user : ""}`}
            >
              {msg.role === "assistant" && (
                <span className={styles.chatAvatar}>
                  <MIcon name="fact_check" size={16} />
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
              <MIcon name="fact_check" size={16} />
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

      {!isEmpty && (
      <div className={styles.chatInputArea}>
        {attachmentRail}
        <div className={styles.chatActions}>
          {sourcesToggle}
          <button
            type="button"
            className={styles.btnSecondary}
            disabled={!canInteract || messages.length === 0}
            onClick={onClearMessages}
          >
            {isClearing ? <Spinner size={14} /> : <MIcon name="delete_sweep" size={14} />}
            {t("AiJudgePanel.clearChatBtn")}
          </button>
        </div>
        {sourcesPanel}
        <form
          className={styles.chatForm}
          onSubmit={(e) => {
            e.preventDefault();
            send();
          }}
        >
          {onUploadFile && (
            <>
              {attachmentInput}
              <button
                type="button"
                className={`${styles.iconBtn} ${styles.chatAttachButton}`}
                disabled={!canInteract}
                aria-label={t("AiJudgePanel.addAttachmentAria")}
                title={t("AiJudgePanel.addAttachmentAria")}
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
            placeholder={t("AiJudgePanel.chatInputPlaceholder")}
            rows={1}
            disabled={!canInteract}
          />
          <button
            type="submit"
            className={styles.btnSecondary}
            disabled={!canSend}
            aria-label={t("AiJudgePanel.sendAria")}
          >
            <MIcon name="send" size={16} />
          </button>
        </form>
        <p className={styles.chatHint}>{t("AiJudgePanel.chatHint")}</p>
      </div>
      )}
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
    <div className={styles.rubricActionBar}>
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
        {isProcessing ? <Spinner size={14} /> : <MIcon name="save" size={14} />}
        {label}
      </button>
    </div>
  );
}

/* ── 新增檢查命名 Dialog ──────────────────────────────── */

export function CreateCheckDialog({
  closing = false,
  busy = false,
  error = "",
  weeks = [],
  defaultWeekId = "",
  onClose = () => {},
  onSubmit = () => {},
}) {
  const { t } = useTranslation("teaching");
  const [title, setTitle] = useState("");
  const [weekId, setWeekId] = useState(defaultWeekId);
  const [invalid, setInvalid] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!busy && !closing) inputRef.current?.focus();
  }, [busy, closing]);

  function submit(event) {
    event.preventDefault();
    const nextTitle = title.trim();
    if (!nextTitle) {
      setInvalid(true);
      inputRef.current?.focus();
      return;
    }
    onSubmit(nextTitle, weekId || null);
  }

  return (
    <Modal
      as="form"
      onSubmit={submit}
      closing={closing}
      onClose={onClose}
      busy={busy}
      title={t("AiJudgePanel.newCheckBtn")}
      description={t("AiJudgePanel.createCheckDesc")}
      aria-busy={busy || undefined}
      actions={
        <>
          <button type="button" className={styles.btnSecondary} disabled={busy} onClick={onClose}>
            {t("AiJudgePanel.cancelBtn")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={busy}>
            {busy ? <><Spinner size={15} />{t("AiJudgePanel.creating")}</> : t("AiJudgePanel.createBlankBtn")}
          </button>
        </>
      }
    >
      <label className={styles.dialogField} htmlFor="create-check-name-input">
        <span>{t("AiJudgePanel.checkNameLabel")}</span>
        <input
          id="create-check-name-input"
          ref={inputRef}
          className={`${styles.createCheckNameInput} ${invalid ? styles.fieldInvalid : ""}`}
          value={title}
          maxLength={255}
          placeholder={t("AiJudgePanel.checkNamePlaceholder")}
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
          {t("AiJudgePanel.checkNameRequired")}
        </p>
      )}
      <WeekSelectField weeks={weeks} value={weekId} onChange={setWeekId} disabled={busy} optional />
      {error && <p className={styles.dialogError} role="alert">{error}</p>}
    </Modal>
  );
}

/** 週次下拉：只列填了標題的週次；一週都沒有時說明要去哪裡補 */
function WeekSelectField({ weeks, value, onChange, disabled = false, optional = false, autoFocus = false }) {
  const { t } = useTranslation("teaching");
  const selectId = useId();
  if (weeks.length === 0) {
    return (
      <p className={styles.dialogHint}>
        <MIcon name="info" size={15} aria-hidden="true" />
        {optional ? t("AiJudgePanel.noWeeksHintOptional") : t("AiJudgePanel.noWeeksHint")}
      </p>
    );
  }
  return (
    <label className={styles.dialogField} htmlFor={selectId}>
      <span>{t("AiJudgePanel.weekLabel")}{optional && <small>{t("AiJudgePanel.optionalTag")}</small>}</span>
      <select
        id={selectId}
        className={styles.dialogSelect}
        value={value}
        disabled={disabled}
        autoFocus={autoFocus}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="" disabled={!optional}>{optional ? t("AiJudgePanel.weekNone") : t("AiJudgePanel.weekSelect")}</option>
        {weeks.map((week) => (
          <option key={week.id} value={week.id}>{t("AiJudgePanel.weekOption", { week: week.week ?? week.week_number, title: week.title })}</option>
        ))}
      </select>
    </label>
  );
}

/** 新增檢查時預設帶入「最近已上過的一週」，都還沒開始就不指定 */
export function getDefaultWeekId(weeks, today = new Date()) {
  const todayKey = today.toISOString().slice(0, 10);
  const past = weeks
    .filter((week) => week.session_date && String(week.session_date).slice(0, 10) <= todayKey)
    .sort((a, b) => String(b.session_date).localeCompare(String(a.session_date)));
  return past[0]?.id ?? "";
}

/** 沒有指定（或網址上的檢查已不存在）時預設選最近動過的一項；釘選只影響清單排序 */
export function getDefaultSessionId(sessions) {
  if (!Array.isArray(sessions) || sessions.length === 0) return null;
  const time = (session) => Date.parse(session?.last_activity_at ?? session?.updated_at ?? "") || 0;
  return sessions.reduce((latest, session) => (time(session) > time(latest) ? session : latest)).id;
}

export function resolveActiveSessionId(currentId, sessions) {
  if (!currentId || !Array.isArray(sessions)) return null;
  return sessions.some((session) => session.id === currentId) ? currentId : null;
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

export function RubricsTab({ classId, judgeSession, onSessionUpdated, onScriptCreated, onDirtyChange, machineNodes = [] }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  // 製作腳本要等很久：途中切到別的檢查（這個分頁已卸載）就不能再把上層帶去腳本／核查分頁
  const mountedRef = useRef(true);
  const [chatHasDraft, setChatHasDraft] = useState(false);

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

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  function clearPendingProposal() {
    setPendingProposal(null);
    setSelectedProposalIds(new Set());
    setPendingProposalMeta(null);
    setPendingProposalIsRefine(false);
    setPendingItemResults(null);
  }

  // 忽略後提案就不見了，要再請 AI 產生一次，先確認
  async function skipPendingProposal() {
    const ok = await confirm({
      title: t("AiJudgePanel.ignoreProposalTitle"),
      message: t("AiJudgePanel.ignoreProposalMessage"),
      confirmText: t("AiJudgePanel.ignoreBtn"),
    });
    if (ok) clearPendingProposal();
  }

  async function refreshSessionMessages({ silent = false, replace = false } = {}) {
    if (!judgeSession?.id) return false;
    try {
      const rows = await AiJudgeService.listSessionMessages(classId, judgeSession.id);
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
        setFiles((current) => current.map((entry) => (
          entry.id === updated.id ? updated : entry
        )));
      },
      onError(error) {
        if (error?.status === 409) {
          clearPendingProposal();
          toastRef.current.error(jt("rubricConflict"));
          void AiJudgeService.listFiles(classIdRef.current)
            .then((rows) => {
              setFiles(rows);
              setFilesLoaded(true);
            })
            .catch(() => {});
          return;
        }
        toastRef.current.error(error?.message ?? jt("updateRubricFailed"));
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
      if (!silent) toast.error(t("AiJudgePanel.loadSourcesFailed"));
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
    clearPendingProposal();
    if (!judgeSession?.id) return undefined;
    AiJudgeService.listSessionMessages(classId, judgeSession.id)
      .then((rows) => {
        if (!cancelled) setMessages(rows);
      })
      .catch(() => {
        if (!cancelled) toast.error(t("AiJudgePanel.loadChatFailed"));
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

  // 回傳 false 代表這則訊息沒有送出去，聊天輸入框會把內容還給老師，不會無聲消失
  async function handleSendMessage(content, isRefine = false, attachments = []) {
    if (!judgeSession?.id) return false;
    if (!analysis) {
      toast.error(t("AiJudgePanel.rubricStillLoading"));
      return false;
    }
    // 自動儲存失敗時 autosave 已經用 toast 說明原因
    if (autosaveRef.current && !(await autosaveRef.current.flush())) return false;
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
    if (!saved) {
      setAnalysis(previousAnalysis);
      return;
    }
    if (sourceFileId) pendingReviewIdsByFileRef.current.set(sourceFileId, pendingIdsAfterApply);
    setPendingReviewIds(pendingIdsAfterApply);
    clearPendingProposal();
    toast.success(t("AiJudgePanel.proposalApplied"));
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
    if (!judgeSession?.id || !sourceFileId || !analysis || isCreatingScript) return;
    setIsCreatingScript(true);
    /* 製作中 ScriptGenerationNotice 只看 status 顯示進度，不看 notice；
       notice 只在結束時（成功／失敗）寫入，所以進行中不必另外設定 */
    setScriptGenerationStatus("saving");
    try {
      if (autosaveRef.current && !(await autosaveRef.current.flush())) {
        setScriptGenerationNotice({
          status: "error",
          message: t("AiJudgePanel.buildNotSaved"),
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
        const message = t("AiJudgePanel.buildReviewMalformed");
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
      // AI 核對通過但還建議修改項目時，一律停下來讓老師確認：畫面承諾「同意提案後才會正式保存」，
      // 不能由按鈕自動套用。老師同意套用後再按一次「儲存並製作」。
      if (hasSelectable) {
        setScriptGenerationNotice(null);
        toast.info(t("AiJudgePanel.buildNeedsApply", { count: selectableIds.size }));
        return;
      }
      const candidateAnalysis = {
        ...applyItems(analysis, analysis.items ?? []),
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
        throw new Error(t("AiJudgePanel.buildReviewNotSaved"));
      }
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
  const saveAndCreateHint = isCreatingScript ? null : busyReason ?? scriptCreationBlocker;

  // 這些只存在畫面上（提案、附件、打到一半的訊息），或 AI 還在處理：切走就沒了
  const dirty = Boolean(pendingProposal)
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
      <ScriptGenerationNotice
        isCreatingScript={isCreatingScript}
        status={scriptGenerationStatus}
        notice={scriptGenerationNotice}
      />

      <div className={styles.rubricsGrid}>
        <div className={`${styles.card} ${styles.rubricTableCard} ${styles.checkRubricCard}`}>
          <div className={`${styles.cardHead} ${styles.checkHead}`}>
            <h4 className={styles.cardTitle}>{analysis ? t("AiJudgePanel.itemsTitleCount", { count: items.length }) : t("AiJudgePanel.itemsTitle")}</h4>
            {items.length > 0 && (isChatting || isCreatingScript) && (
              <span className={styles.actionHint}>{isCreatingScript ? t("AiJudgePanel.editLockedBuilding") : t("AiJudgePanel.editLockedChatting")}</span>
            )}
          </div>
          {!filesLoaded && !analysis ? (
            <LoadingState text={t("AiJudgePanel.loadingRubric")} />
          ) : items.length === 0 && !pendingProposal ? (
            <EmptyState
              icon="playlist_add"
              title={t("AiJudgePanel.noItemsTitle")}
              description={t("AiJudgePanel.noItemsDesc")}
            />
          ) : (
            <>
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
                <div className={styles.checkRubricBody}>
                  <RubricTable
                    items={items}
                    onChange={handleItemChange}
                    onDelete={handleItemDelete}
                    disabled={isChatting || isCreatingScript}
                    needsReviewIds={pendingReviewIds}
                    machineNodes={machineNodes}
                  />
                </div>
              )}
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

        <div className={`${styles.card} ${styles.checkChatCol}`}>
          <div className={styles.checkChatInner}>
            <div className={styles.checkHead}>
              <h4 className={styles.cardTitle}>
                <MIcon name="fact_check" size={18} />
                {t("AiJudgePanel.assistantTitle")}
              </h4>
            </div>
            <ChatPanel
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

/* ── Tab 3：腳本總覽 ────────────────────────────────────── */

const SCRIPT_STATUS_LABELS = {
  get draft() { return jt("scriptStatusDraft"); },
  get review_failed() { return jt("scriptStatusReviewFailed"); },
  get reviewed() { return jt("scriptStatusReviewed"); },
  get approved() { return jt("scriptStatusApproved"); },
  get archived() { return jt("scriptStatusArchived"); },
};

const RETRY_STOP_REASON_LABELS = {
  get passed() { return jt("retryStopPassed"); },
  get same_failure_limit() { return jt("retryStopSameFailure", { count: 2 }); },
  get total_retry_limit() { return jt("retryStopTotal", { count: 4 }); },
  get unrecoverable_error() { return jt("retryStopUnrecoverable"); },
};

export function getScriptCreationDestination(artifact) {
  return artifact?.status === "approved" ? "review" : "scripts";
}

function scriptStatusBadgeClass(status) {
  if (status === "approved") return styles.badge_success;
  if (status === "review_failed") return styles.badge_danger;
  if (status === "reviewed" || status === "draft") return styles.badge_pending;
  return styles.badge_muted;
}

function ReviewPanel({ title, result }) {
  const { t } = useTranslation("teaching");
  const issues = Array.isArray(result?.issues) ? result.issues : [];
  return (
    <div className={styles.reviewPanel}>
      <div className={styles.reviewPanelHead}>
        <span>{title}</span>
        <span
          className={`${styles.badge} ${result?.approved ? styles.badge_success : styles.badge_danger}`}
        >
          {result?.approved ? t("AiJudgePanel.reviewPass") : t("AiJudgePanel.reviewBlock")}
        </span>
      </div>
      {issues.length > 0 ? (
        <ul className={styles.reviewIssues}>
          {issues.map((issue, index) => (
            <li key={`${title}-${index}`}>{String(issue)}</li>
          ))}
        </ul>
      ) : (
        <p className={styles.mutedText}>{t("AiJudgePanel.reviewNoRisks")}</p>
      )}
      {result?.suggested_fix && (
        <p className={styles.mutedText}>{t("AiJudgePanel.reviewSuggestion", { fix: String(result.suggested_fix) })}</p>
      )}
    </div>
  );
}

export function getScriptReviewAttemptIssues(attempt) {
  const uncoveredIssues = Array.isArray(attempt?.uncovered_rubric_items)
    ? attempt.uncovered_rubric_items.map((item) => {
      if (typeof item === "string") return jt("uncoveredItem", { title: item });
      if (!item || typeof item !== "object") return "";
      const label = item.title || item.id;
      return label ? jt("uncoveredItem", { title: label }) : "";
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
  const { t } = useTranslation("teaching");
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
  const stopReason = RETRY_STOP_REASON_LABELS[summary?.stop_reason] ?? t("AiJudgePanel.scriptStatusReviewFailed");
  return (
    <div className={styles.noticeInfo}>
      <p>
        <strong className={styles.dangerText}>{stopReason}</strong>
      </p>
      <p>
        {t("AiJudgePanel.retrySummary", { count: retryCount })}
      </p>
      {displayedAttempts.length > 0 && (
        <ul className={styles.reviewIssues}>
          {displayedAttempts.slice(-3).map((attempt, index) => {
            const issues = getScriptReviewAttemptIssues(attempt);
            return (
              <li key={`${attempt?.attempt ?? index}-${attempt?.failure_signature ?? "failure"}`}>
                {t("AiJudgePanel.retryAttempt", { attempt: attempt?.attempt ?? index + 1, phase: attempt?.phase ?? t("AiJudgePanel.retryPhaseReview") })}
                {issues.slice(0, 2).join(t("AiJudgePanel.issueSeparator")) || t("AiJudgePanel.retryNoDetail")}
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
  onGoToSettings,
}) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  const [scripts, setScripts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [renameTarget, setRenameTarget] = useState(null);
  const [renameName, setRenameName] = useState("");
  const [renameInvalid, setRenameInvalid] = useState(false);
  const [actionPending, setActionPending] = useState(null); // "approve" | "rename" | "delete"
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
      toast.success(t("AiJudgePanel.scriptApproved"));
      fetchScripts();
      onScriptApproved?.();
    } catch (err) {
      toast.error(err?.message ?? t("AiJudgePanel.approveFailed"));
    } finally {
      setActionPending(null);
    }
  }

  async function handleDelete(target) {
    if (!target) return;
    const ok = await confirm({
      title: t("AiJudgePanel.deleteScriptTitle"),
      message: t("AiJudgePanel.deleteScriptMessage", { name: target.name }),
      confirmText: t("AiJudgePanel.opDelete"),
      danger: true,
    });
    if (!ok) return;
    setActionPending("delete");
    try {
      await AiJudgeService.deleteScript(classId, target.id);
      toast.success(t("AiJudgePanel.scriptDeleted"));
      setSelectedId(null);
      setScripts((current) => current.filter((script) => script.id !== target.id));
    } catch (err) {
      toast.error(err?.message ?? t("AiJudgePanel.deleteFailed"));
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
      toast.success(t("AiJudgePanel.scriptRenamed"));
      setScripts((current) =>
        current.map((script) => (script.id === updated.id ? updated : script)),
      );
      setRenameTarget(null);
      setRenameName("");
      setRenameInvalid(false);
    } catch (err) {
      toast.error(err?.message ?? t("AiJudgePanel.renameFailed"));
    } finally {
      setActionPending(null);
    }
  }

  return (
    <div className={styles.tabBody}>
      {loading ? (
        <LoadingState text={t("AiJudgePanel.loadingScripts")} />
      ) : error ? (
        <div className={styles.card}>
          <ErrorState onRetry={fetchScripts} />
        </div>
      ) : scripts.length === 0 ? (
        <div className={styles.card}>
          <EmptyState
            icon="terminal"
            title={t("AiJudgePanel.noScriptsTitle")}
            description={t("AiJudgePanel.noScriptsDesc")}
            action={onGoToSettings && (
              <button type="button" className={styles.btnSecondary} onClick={onGoToSettings}>
                <MIcon name="arrow_back" size={16} />{t("AiJudgePanel.goToSettings")}
              </button>
            )}
          />
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
                    aria-label={t("AiJudgePanel.renameAria", { name: selected.name })}
                    onSubmit={handleRename}
                    onClick={(event) => event.stopPropagation()}
                  >
                    <MIcon name="security" size={18} />
                    <label className={styles.srOnly} htmlFor={`script-name-${selected.id}`}>
                      {t("AiJudgePanel.scriptNameLabel")}
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
                      aria-label={t("AiJudgePanel.renameAria", { name: selected.name })}
                      aria-invalid={renameInvalid}
                      aria-describedby={renameInvalid ? `script-name-error-${selected.id}` : undefined}
                      title={t("AiJudgePanel.renameKeysHint")}
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
                        {t("AiJudgePanel.scriptNameRequired")}
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
                      {actionPending === "approve" ? t("AiJudgePanel.approving") : t("AiJudgePanel.approveBtn")}
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
                    {actionPending === "rename" ? t("AiJudgePanel.saving") : selectedIsRenaming ? t("AiJudgePanel.cancelBtn") : t("AiJudgePanel.renameBtn")}
                  </button>
                  <button
                    type="button"
                    className={styles.btnDangerOutline}
                    onClick={() => handleDelete(selected)}
                    disabled={actionPending !== null || selectedIsRenaming}
                  >
                    <MIcon name="delete" size={16} />
                    {actionPending === "delete" ? t("AiJudgePanel.deleting") : t("AiJudgePanel.deleteScriptBtn")}
                  </button>
                </div>
              </div>

              <div className={styles.reviewGrid}>
                <ReviewPanel title={t("AiJudgePanel.policyReviewTitle")} result={selected.policy_check_result_json} />
                <ReviewPanel title={t("AiJudgePanel.aiReviewTitle")} result={selected.ai_review_result_json} />
              </div>

              <RetrySummary script={selected} />

              <pre className={styles.codeBlock}>{selected.script_content}</pre>
            </div>
          )}
        </div>
      )}


    </div>
  );
}

/* ── 執行結果／核查共用的結果顯示元件 ───────────────────── */

function runIsTerminal(status) {
  return status === "completed"
    || status === "completed_with_failures"
    || status === "failed"
    || status === "cancelled";
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
      <CommandOutput label={t("AiJudgePanel.chipWorkingDir")} text={parsed.cwd} />
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
  if (target.status === "failed" || target.validation?.valid === false) {
    return { kind: "failed", label: jt("statusRunFailed"), pending: 0, reviewable: 0 };
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

function ReviewCheckRow({ check, decision, onDecide }) {
  const { t } = useTranslation("teaching");
  const meta = checkStatusMeta(check?.status);
  const reviewable = TEACHER_REVIEW_STATUSES.has(check?.status);
  return (
    <div className={styles.reviewCheck}>
      <span className={`${styles.reviewCheckIcon} ${meta.className}`}><MIcon name={meta.icon} size={17} /></span>
      <div className={styles.reviewCheckContent}>
        <div><strong>{check?.title ?? check?.id ?? t("AiJudgePanel.collectedItem")}</strong><span>{meta.label}</span></div>
        <p>{check?.evidence || t("AiJudgePanel.noSummary")}</p>
        {(check?.raw || (Array.isArray(check?.errors) && check.errors.length > 0)) && (
          <details><summary>{t("AiJudgePanel.viewEvidence")}</summary><CommandLog raw={check?.raw} fallbackText={(check?.errors ?? []).join("\n")} /></details>
        )}
      </div>
      {reviewable && (
        <div className={styles.reviewDecision} aria-label={t("AiJudgePanel.manualDecisionAria", { title: check?.title ?? check?.id })}>
          <button type="button" className={decision === "pass" ? styles.reviewPassActive : ""} aria-pressed={decision === "pass"} onClick={() => onDecide("pass")}><MIcon name="check" size={16} />{t("AiJudgePanel.checkPass")}</button>
          <button type="button" className={decision === "fail" ? styles.reviewFailActive : ""} aria-pressed={decision === "fail"} onClick={() => onDecide("fail")}><MIcon name="close" size={16} />{t("AiJudgePanel.checkFail")}</button>
        </div>
      )}
    </div>
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
  const { t } = useTranslation("teaching");
  const { target } = row;
  const checks = targetChecks(target);
  const nodeLabel = row.nodeKey
    ? batchMachineDisplayName(row.nodeKey, machineNodes, row.node?.display_label) ?? row.nodeKey
    : null;
  if (!target) {
    return <div className={styles.reviewNoResult}>{t("AiJudgePanel.machineNotInRun")}</div>;
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
            <p className={styles.mutedText}>{t("AiJudgePanel.machineNoCheckpoints")}</p>
          )}
          {row.items.map((item, itemIndex) => {
            const itemMeta = checkStatusMeta(item?.status);
            const itemChecks = Array.isArray(item?.checks) ? item.checks : [];
            const peerText = item?.peer_node_key
              ? t("AiJudgePanel.peerObserve", { name: item?.peer_display_label ?? item.peer_node_key })
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
                    <strong>{item?.title ?? item?.rubric_item_id ?? t("AiJudgePanel.thCheckpoint")}</strong>
                    <span>{itemMeta.label}{peerText ? ` · ${peerText}` : ""}</span>
                  </div>
                </div>
                {itemChecks.length === 0 ? (
                  <p className={styles.mutedText}>
                    {item?.reason_code === "peer_unavailable"
                      ? t("AiJudgePanel.peerUnavailable")
                      : t("AiJudgePanel.checkpointNoResult")}
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
              <summary>{t("AiJudgePanel.unmappedChecks")}</summary>
              <CheckResultsTable checks={row.unmappedChecks} />
            </details>
          )}
        </div>
      ) : (
        <div className={styles.reviewCheckList}>
          {checks.length === 0 ? <p className={styles.mutedText}>{t("AiJudgePanel.scriptNoChecks")}</p> : checks.map((check, index) => (
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
        <span>{t("AiJudgePanel.feedbackLabel")} <small>{t("AiJudgePanel.optionalShort")}</small></span>
        <textarea
          value={draft.feedback}
          maxLength={4000}
          rows={3}
          placeholder={t("AiJudgePanel.feedbackPlaceholder")}
          onChange={(event) => onUpdateFeedback(row.key, event.target.value)}
        />
        <small>{draft.feedback.length} / 4000</small>
      </label>
      <div className={styles.reviewSaveRow}>
        <span>{isDirty
          ? t("AiJudgePanel.unsavedChanges")
          : saved.feedback || Object.keys(saved.decisions).length
            ? t("AiJudgePanel.lastSaved", { time: target.teacher_review?.updated_at ? formatDateTime(target.teacher_review.updated_at) : t("AiJudgePanel.saved") })
            : t("AiJudgePanel.reviewSaveHint")}</span>
        <button type="button" className={styles.btnSecondary} disabled={!isDirty || saving} onClick={() => onSave(row)}>{saving ? <><Spinner size={15} />{t("AiJudgePanel.saving")}</> : <><MIcon name="save" size={16} />{t("AiJudgePanel.saveReviewBtn")}</>}</button>
      </div>
    </>
  );
}

export function TeacherReviewTab({
  classId,
  sessionId,
  members,
  machineNodes = [],
  active = true,
  onDirtyChange,
  onGoToScripts,
  onRunStarted,
}) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  const [reviewState, setReviewState] = useState(null); // { mode: "batch", batch } | { mode: "run", run }
  const [scriptSets, setScriptSets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [expandedKey, setExpandedKey] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [savingKey, setSavingKey] = useState(null);
  const [sortMode, setSortMode] = useState("pending");
  const [runOnceOpen, setRunOnceOpen] = useState(false);
  const [creatingBatch, setCreatingBatch] = useState(false);
  const [activeBatch, setActiveBatch] = useState(null);
  const [selectedSetId, setSelectedSetId] = useState(null);
  // 整批執行完成時老師還有沒存的核查：先不換結果（草稿會套到新結果上），存完再自動載入
  const [newResultsWaiting, setNewResultsWaiting] = useState(false);
  const loadRequestRef = useRef(0);
  const runOnceDialog = useDialogPresence(runOnceOpen);
  const batchStatusRef = useRef(null);
  const hasDirtyDraftsRef = useRef(false);
  const wasActiveRef = useRef(active);

  /* silent：背景更新（整批執行完成、切回這個分頁），不蓋掉畫面；
     老師還有沒存的核查時只更新腳本集，結果留到存完再換 */
  const loadReview = useCallback(async ({ silent = false } = {}) => {
    const requestId = ++loadRequestRef.current;
    if (!silent) {
      setLoading(true);
      setLoadError(false);
    }
    try {
      const [sets, runs] = await Promise.all([
        AiJudgeService.listSessionScriptSets(classId, sessionId),
        AiJudgeService.listSessionRuns(classId, sessionId),
      ]);
      if (loadRequestRef.current !== requestId) return;
      setScriptSets(Array.isArray(sets) ? sets : []);
      const orderedRuns = Array.isArray(runs) ? runs : [];
      const latest = orderedRuns[0] ?? null;
      let nextState = null;
      const nextDrafts = {};
      if (latest?.run_batch_id) {
        const batch = await AiJudgeService.getSessionRunBatch(
          classId,
          sessionId,
          latest.run_batch_id,
        );
        if (loadRequestRef.current !== requestId) return;
        // 離開再回來時整批還在跑：接回進度輪詢，「一次執行」維持停用，避免重複執行
        if (batch?.run_batch_id && !runIsTerminal(batch.status)) {
          batchStatusRef.current = batch.status;
          setActiveBatch(batch);
        }
        nextState = { mode: "batch", batch };
        Object.assign(nextDrafts, draftsFromBatch(batch));
      } else {
        const preferred = orderedRuns.find((item) => item.status === "completed") ?? latest;
        if (preferred) {
          const detail = await AiJudgeService.getSessionRun(classId, sessionId, preferred.id);
          if (loadRequestRef.current !== requestId) return;
          nextState = { mode: "run", run: detail };
          for (const target of detail?.target_results_json?.targets ?? []) {
            nextDrafts[String(target.vmid)] = reviewDraftFromTeacherReview(target?.teacher_review);
          }
        }
      }
      if (silent && hasDirtyDraftsRef.current) return;
      setReviewState(nextState);
      setDrafts(nextDrafts);
      setNewResultsWaiting(false);
    } catch {
      if (loadRequestRef.current === requestId && !silent) setLoadError(true);
    } finally {
      if (loadRequestRef.current === requestId && !silent) setLoading(false);
    }
  }, [classId, sessionId]);

  useEffect(() => {
    setReviewState(null);
    setScriptSets([]);
    setExpandedKey(null);
    setActiveBatch(null);
    setSelectedSetId(null);
    setNewResultsWaiting(false);
    batchStatusRef.current = null;
    loadReview();
    return () => {
      loadRequestRef.current += 1;
    };
  }, [loadReview]);

  /* 分頁一直掛著（草稿才不會消失），切回來時靜默更新：
     例如剛在腳本總覽核准腳本，這裡要能馬上一次執行 */
  useEffect(() => {
    if (active && !wasActiveRef.current) loadReview({ silent: true });
    wasActiveRef.current = active;
  }, [active, loadReview]);

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
      loadReview({ silent: true });
    }
  }, [activeBatch, loadReview, t, toast]);

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

  const dirtyCount = useMemo(() => machineRows.filter((row) => (
    Boolean(row.target) && isReviewDraftDirty(drafts[row.key], targetTeacherReview(row.target))
  )).length, [machineRows, drafts]);
  hasDirtyDraftsRef.current = dirtyCount > 0;

  useEffect(() => {
    onDirtyChange?.(dirtyCount > 0);
  }, [dirtyCount, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  useEffect(() => {
    if (newResultsWaiting && dirtyCount === 0 && savingKey === null) loadReview({ silent: true });
  }, [newResultsWaiting, dirtyCount, savingKey, loadReview]);

  async function discardDraftsAndLoad() {
    const ok = await confirm({
      title: t("AiJudgePanel.discardReviewsTitle"),
      message: t("AiJudgePanel.discardReviewsMessage", { count: dirtyCount }),
      confirmText: t("AiJudgePanel.discardAndLoad"),
      danger: true,
    });
    if (!ok) return;
    hasDirtyDraftsRef.current = false;
    loadReview({ silent: true });
  }

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
    const draft = drafts[row.key] ?? reviewDraftFromTeacherReview(row.target?.teacher_review);
    setSavingKey(row.key);
    try {
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
          (row.studentId != null
            && String(item?.student_id ?? "") === String(row.studentId))
          || (row.vmid != null && String(item?.vmid) === String(row.vmid))
        ));
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
        setDrafts((current) => ({ ...current, [row.key]: reviewDraftFromTeacherReview(savedTarget?.teacher_review) }));
      }
      toast.success(t("AiJudgePanel.reviewSaved"));
    } catch (error) {
      toast.error(error?.message ?? t("AiJudgePanel.reviewSaveFailed"));
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
      onRunStarted?.();
      toast.success(t("AiJudgePanel.batchCreated", { count: next?.summary?.targets ?? 0 }));
    } catch (error) {
      toast.error(error?.message ?? t("AiJudgePanel.batchCreateFailed"));
    } finally {
      setCreatingBatch(false);
    }
  }

  function renderRunOnceDialog() {
    if (!runOnceDialog.open) return null;
    const children = Array.isArray(selectedSet?.children) ? selectedSet.children : [];
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
              disabled={creatingBatch || !selectedSet}
            >
              {creatingBatch ? t("AiJudgePanel.creating") : t("AiJudgePanel.confirmRun")}
            </button>
          </>
        }
      >
        {approvedSets.length > 1 && (
          <label className={styles.field}>
            <span>{t("AiJudgePanel.scriptSetLabel")}</span>
            <select
              value={selectedSet?.artifact_set_id ?? ""}
              onChange={(event) => setSelectedSetId(event.target.value)}
            >
              {approvedSets.map((set) => (
                <option key={set.artifact_set_id} value={set.artifact_set_id}>
                  {t("AiJudgePanel.scriptSetOption", { version: set.source_analysis_revision ?? "—", count: set.children?.length ?? 0 })}
                </option>
              ))}
            </select>
          </label>
        )}

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
  if (loadError) return <ErrorState onRetry={() => loadReview()} />;
  if (!reviewState) {
    return (
      <div className={styles.tabBody}>
        <div className={styles.card}>
          {approvedSets.length > 0 ? (
            <EmptyState
              icon="rate_review"
              title={t("AiJudgePanel.noResultsTitle")}
              description={t("AiJudgePanel.noResultsReadyDesc")}
              action={(
                <button
                  type="button"
                  className={styles.btnPrimary}
                  onClick={() => setRunOnceOpen(true)}
                  disabled={creatingBatch}
                >
                  {creatingBatch ? <Spinner size={15} /> : <MIcon name="bolt" size={16} />}
                  {t("AiJudgePanel.runAllTitle")}
                </button>
              )}
            />
          ) : (
            <EmptyState
              icon="rate_review"
              title={t("AiJudgePanel.noResultsTitle")}
              description={t("AiJudgePanel.noResultsNeedScriptDesc")}
              action={onGoToScripts && (
                <button type="button" className={styles.btnSecondary} onClick={onGoToScripts}>
                  <MIcon name="arrow_back" size={16} />{t("AiJudgePanel.goToScripts")}
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
    <div className={styles.tabBody}>
      <div className={`${styles.card} ${styles.reviewOverview}`}>
        <div className={styles.reviewOverviewHead}>
          <h4 className={styles.cardTitle}><MIcon name="rate_review" size={19} />{t("AiJudgePanel.tabReview")}</h4>
          <div className={styles.sectionActions}>
            {batchRunning && (
              <span className={styles.mutedText}>
                <Spinner size={14} />
                {t("AiJudgePanel.runProgress", { done: activeBatch.summary?.completed ?? 0, total: activeBatch.summary?.targets ?? 0 })}
              </span>
            )}
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={() => setRunOnceOpen(true)}
              disabled={creatingBatch || batchRunning || approvedSets.length === 0}
              title={approvedSets.length === 0
                ? t("AiJudgePanel.runNoApprovedTitle")
                : t("AiJudgePanel.runAllHint")}
            >
              {creatingBatch ? <Spinner size={15} /> : <MIcon name="bolt" size={16} />}
              {t("AiJudgePanel.runAllBtn")}
            </button>
          </div>
        </div>
        <div className={styles.reviewMetrics} aria-label={t("AiJudgePanel.metricsAria")}>
          <span><strong>{summary.pending}</strong><small>{t("AiJudgePanel.metricPending")}</small></span>
          <span><strong>{summary.reviewed}</strong><small>{t("AiJudgePanel.metricReviewed")}</small></span>
          <span><strong>{summary.automatic}</strong><small>{t("AiJudgePanel.statusAutomatic")}</small></span>
          <span><strong>{summary.unavailable}</strong><small>{t("AiJudgePanel.metricUnavailable")}</small></span>
          <span><strong>{summary.total}</strong><small>{t("AiJudgePanel.metricTotal")}</small></span>
        </div>
        {newResultsWaiting && (
          <div className={`${styles.noticeInfo} ${styles.noticeWithAction}`} role="status">
            <p>{t("AiJudgePanel.newResultsWaiting")}</p>
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
                    <strong>{user.full_name ?? t("AiJudgePanel.unnamedStudent")}</strong>
                    <small>
                      {user.email ?? ""}
                      {` · ${t("AiJudgePanel.machineCount", { count: student.machines.length })}`}
                    </small>
                  </span>
                </span>
                <span className={styles.reviewAiCounts} aria-label={t("AiJudgePanel.studentTotalsAria")}>
                  <em className={styles.reviewCountPass}>{t("AiJudgePanel.countPass", { count: totals.pass })}</em>
                  <em className={styles.reviewCountFail}>{t("AiJudgePanel.countFail", { count: totals.fail })}</em>
                  <em className={styles.reviewCountPending}>{t("AiJudgePanel.countPending", { count: totals.pending })}</em>
                </span>
                <span className={`${styles.badge} ${reviewBadgeClass(student.status.kind)}`}>{student.status.label}</span>
                <MIcon name={isOpen ? "expand_less" : "expand_more"} size={20} />
              </button>

              {isOpen && (
                <div className={styles.reviewStudentBody}>
                  {student.machines.map((row) => {
                    const draft = drafts[row.key] ?? reviewDraftFromTeacherReview(row.target?.teacher_review);
                    const saved = targetTeacherReview(row.target);
                    const isDirty = Boolean(row.target) && isReviewDraftDirty(draft, saved);
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

// 依實際流程排序：設定檢查表 → 核准腳本 → 執行並核查
const TEACHER_JUDGE_TABS = [
  { key: "rubrics", icon: "description", get label() { return jt("tabRubrics"); } },
  { key: "scripts", icon: "terminal", get label() { return jt("tabScripts"); } },
  { key: "review", icon: "rate_review", get label() { return jt("tabReview"); } },
];

const NO_VISITED_TABS = new Set();

function TeacherWorkspacePanel({ classId, members, weeks = [], machineNodes = [] }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  const { confirmLeave } = useUnsavedChanges();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedSessionId = searchParams.get("check");
  // 只在載入清單時參考網址；寫回網址不該觸發重新載入
  const requestedSessionIdRef = useRef(requestedSessionId);
  requestedSessionIdRef.current = requestedSessionId;
  const [activeTab, setActiveTab] = useState("rubrics");
  // 同一個檢查裡看過的分頁保持掛著（隱藏），切分頁時提案、打到一半的訊息、核查草稿都還在；
  // 換檢查才整組重來
  const [visitedTabs, setVisitedTabs] = useState({ sessionId: null, tabs: NO_VISITED_TABS });
  const [dirtyTabs, setDirtyTabs] = useState({ rubrics: false, review: false });
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

  // 換檢查會卸掉目前的分頁：有沒存的提案、訊息或核查草稿先確認
  async function selectSession(sessionId) {
    closeSessionMenu();
    if (sessionId === activeSessionId) return;
    if (!(await confirmLeave())) return;
    setCreateDialogOpen(false);
    setActiveSessionId(sessionId);
  }

  /* silent：釘選、製作腳本、執行後的背景更新，不讓整個清單閃成轉圈 */
  const loadSessions = useCallback(async ({ silent = false } = {}) => {
    const requestVersion = ++requestVersionRef.current;
    const requestClassId = classId;
    if (!silent) setLoading(true);
    try {
      const rows = await AiJudgeService.listSessions(classId);
      if (requestVersion !== requestVersionRef.current || classIdRef.current !== requestClassId) return;
      setSessions(rows);
      setActiveSessionId((current) => (
        resolveActiveSessionId(current, rows)
        ?? resolveActiveSessionId(requestedSessionIdRef.current, rows)
        ?? getDefaultSessionId(rows)
      ));
    } catch (error) {
      if (requestVersion === requestVersionRef.current && classIdRef.current === requestClassId) {
        setSessions([]);
        setActiveSessionId(null);
        toast.error(error?.message ?? jt("loadChecksFailed"));
      }
    } finally {
      if (requestVersion === requestVersionRef.current && classIdRef.current === requestClassId) setLoading(false);
    }
  }, [classId, toast]);

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

  // 選中的檢查寫進網址（?check=），重新整理或分享連結都會回到同一項；用 replace 不塞歷史紀錄
  useEffect(() => {
    if (loading) return;
    if ((activeSessionId ?? null) === (requestedSessionId ?? null)) return;
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (activeSessionId) next.set("check", activeSessionId);
      else next.delete("check");
      return next;
    }, { replace: true });
  }, [activeSessionId, loading, requestedSessionId, setSearchParams]);

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

  async function handleCreateCheck(title, weekId = null) {
    const nextTitle = String(title ?? "").trim();
    if (!nextTitle || creatingCheck) return;
    // 建好會直接切到新檢查
    if (!(await confirmLeave())) return;
    const requestClassId = classId;
    const requestId = ++createCheckRequestRef.current;
    setCreatingCheck(true);
    setCreateCheckError("");
    try {
      const created = await AiJudgeService.createBlankSession(classId, {
        title: nextTitle,
        rubricName: nextTitle,
        teachingClassWeekId: weekId,
      });
      if (requestId !== createCheckRequestRef.current || classIdRef.current !== requestClassId) return;
      handleCreated(created);
    } catch (error) {
      if (requestId === createCheckRequestRef.current && classIdRef.current === requestClassId) {
        setCreateCheckError(error?.message ?? t("AiJudgePanel.createCheckFailed"));
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
    toast.success(t("AiJudgePanel.checkCreated", { title: created.title }));
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
          const rest = sessions.filter((entry) => entry.id !== item.id);
          setSessions(rest);
          if (item.id === activeSessionId) setActiveSessionId(getDefaultSessionId(rest));
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

  async function pinSession(item) {
    await runSessionAction(item, (entry) => AiJudgeService.updateSession(classId, entry.id, { is_pinned: !entry.pinned_at }));
    loadSessions({ silent: true });
  }

  async function forkSession(item) {
    closeSessionMenu();
    // 複製完會直接切到副本
    if (!(await confirmLeave())) return;
    const copy = await runSessionAction(item, (entry) => AiJudgeService.forkSession(classId, entry.id));
    if (!copy) return;
    setSessions((current) => [copy, ...current.filter((entry) => entry.id !== copy.id)]);
    setActiveSessionId(copy.id);
    setActiveTab("rubrics");
    toast.success(t("AiJudgePanel.checkCopied", { title: copy.title }));
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
      toast.success(t("AiJudgePanel.checkMoved", { title: target.title }));
    }
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
        aria-label={t("AiJudgePanel.moreActionsAria", { title: item.title })}
        style={{ top: `${menuPos.top}px`, left: `${menuPos.left}px` }}
      >
        <button type="button" role="menuitem" disabled={busy} onClick={() => { setRenameTarget(item); setRenameTitle(item.title); setRenameInvalid(false); closeSessionMenu(); }}><MIcon name="edit" size={16} />{t("AiJudgePanel.renameBtn")}</button>
        <button type="button" role="menuitem" disabled={busy} onClick={() => { setMoveWeekTarget(item); setMoveWeekId(item.teaching_class_week_id ?? ""); closeSessionMenu(); }}><MIcon name="calendar_month" size={16} />{t("AiJudgePanel.moveWeekBtn")}</button>
        <button type="button" role="menuitem" disabled={busy} onClick={() => pinSession(item)}><MIcon name="push_pin" filled={Boolean(item.pinned_at)} size={16} />{item.pinned_at ? t("AiJudgePanel.unpinBtn") : t("AiJudgePanel.pinBtn")}</button>
        <button type="button" role="menuitem" disabled={busy} onClick={() => forkSession(item)}><MIcon name="content_copy" size={16} />{t("AiJudgePanel.copyBtn")}</button>
        <span className={styles.menuSeparator} />
        <button type="button" role="menuitem" className={styles.menuDanger} disabled={busy} onClick={() => deleteSession(item)}><MIcon name="delete" size={16} />{t("AiJudgePanel.opDelete")}</button>
      </div>
    );
  }

  const sessionSidebarInner = (
    <>
      <button type="button" className={`${styles.btnSecondary} ${styles.newCheckButton}`} onClick={openCreateCheckDialog}><MIcon name="add" size={17} />{t("AiJudgePanel.newCheckBtn")}</button>
      {loading ? (
        <div className={styles.sidebarLoading} role="status" aria-label={t("AiJudgePanel.loading")}><LoadingSpinner size={44} /></div>
      ) : sessions.length === 0 ? null : (
      <div className={styles.sessionList} role="list">
        {sessions.map((item) => {
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
                         aria-label={t("AiJudgePanel.renameAria", { name: item.title })}
                         aria-invalid={renameInvalid}
                         title={t("AiJudgePanel.renameKeysHint")}
                         onChange={(event) => { setRenameTitle(event.target.value); setRenameInvalid(false); }}
                         onKeyDown={(event) => {
                           if (event.key === "Escape") {
                             event.preventDefault();
                             cancelRename();
                           }
                         }}
                       />
                       {renameInvalid && <span className={styles.sessionRenameError} role="alert">{t("AiJudgePanel.checkNameRequired")}</span>}
                     </form>
                   ) : (
                     <button type="button" className={selected ? styles.sessionItemActive : styles.sessionItem} aria-current={selected ? "true" : undefined} onClick={() => selectSession(item.id)}>
                       <SessionTitle title={item.title}>{item.title}</SessionTitle>
                       <small className={styles.sessionWeekLabel}>{linkedWeek ? t("AiJudgePanel.weekOption", { week: linkedWeek.week ?? linkedWeek.week_number, title: linkedWeek.title }) : t("AiJudgePanel.weekUnset")}</small>
                     </button>
                   )}
                   <div className={styles.sessionRowActions}>
                     {renaming ? <button type="button" className={styles.iconBtn} aria-label={t("AiJudgePanel.cancelRenameAria")} title={t("AiJudgePanel.cancelBtn")} onClick={cancelRename}><MIcon name="close" size={17} /></button> : <>
                       <button type="button" className={`${styles.pinBtn} ${item.pinned_at ? styles.pinBtnPinned : ""}`} aria-label={t(item.pinned_at ? "AiJudgePanel.unpinAria" : "AiJudgePanel.pinAria", { title: item.title })} aria-pressed={Boolean(item.pinned_at)} title={item.pinned_at ? t("AiJudgePanel.unpinBtn") : t("AiJudgePanel.pinBtn")} disabled={busy} onClick={(event) => { event.stopPropagation(); pinSession(item); }}><MIcon name="push_pin" filled={Boolean(item.pinned_at)} size={16} /></button>
                       <button type="button" className={styles.menuBtn} aria-label={t("AiJudgePanel.moreActionsAria", { title: item.title })} title={t("AiJudgePanel.moreActions")} aria-haspopup="menu" aria-expanded={openMenuId === item.id} aria-controls={`check-menu-${item.id}`} disabled={busy} onClick={(event) => toggleSessionMenu(event, item.id)}><MIcon name="more_vert" size={18} /></button>
                     </>}
                   </div>
                 </div>
               );
            })}
          </div>
      )}
    </>
  );

  const visited = visitedTabs.sessionId === activeSessionId ? visitedTabs.tabs : NO_VISITED_TABS;
  const tabMounted = (key) => activeTab === key || visited.has(key);
  const titledWeeks = weeks.filter((week) => String(week.title ?? "").trim());

  function tabPanel(key, children) {
    const tab = TEACHER_JUDGE_TABS.find((entry) => entry.key === key);
    return (
      <section
        id={`${tabsIdPrefix}-panel-${key}`}
        aria-label={tab?.label}
        hidden={activeTab !== key}
        className={styles.checkTabPanel}
      >
        {children}
      </section>
    );
  }

  return (
    <div className={styles.panel}>
      {/* 檢查清單與分頁列固定在同一個位置，三個分頁只換下方內容 */}
      <section className={styles.checkWorkspaceTwo} aria-label={t("AiJudgePanel.workspaceAria")}>
        <aside className={`${styles.card} ${styles.checkSessionCol}`} aria-label={t("AiJudgePanel.checkListAria")}>
          {sessionSidebarInner}
        </aside>
        <div className={styles.checkContentCol}>
          {activeSession ? (
            <>
              {/* 上方已有班級步驟列，這裡用分段切換，不再疊第二條步驟列 */}
              <SegmentedControl
                ariaLabel={t("AiJudgePanel.tabsAria")}
                className={styles.checkTabs}
                options={TEACHER_JUDGE_TABS.map((tab) => ({ value: tab.key, label: tab.label, icon: tab.icon }))}
                value={activeTab}
                onChange={selectTab}
              />
              {tabMounted("rubrics") && tabPanel("rubrics", (
                <RubricsTab
                  key={activeSession.id}
                  classId={classId}
                  judgeSession={activeSession}
                  onSessionUpdated={updateSessionInList}
                  onDirtyChange={reportRubricsDirty}
                  machineNodes={machineNodes}
                  onScriptCreated={(artifact) => {
                    loadSessions({ silent: true });
                    const destination = getScriptCreationDestination(artifact);
                    setFocusedScriptId(destination === "scripts" ? (artifact?.id ?? null) : null);
                    selectTab(destination);
                  }}
                />
              ))}
              {activeTab === "scripts" && tabPanel("scripts", (
                <ScriptsTab
                  key={activeSession.id}
                  classId={classId}
                  sessionId={activeSession.id}
                  initialSelectedId={focusedScriptId}
                  onScriptApproved={() => selectTab("review")}
                  onGoToSettings={() => selectTab("rubrics")}
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
                  onDirtyChange={reportReviewDirty}
                  onGoToScripts={() => selectTab("scripts")}
                  onRunStarted={() => loadSessions({ silent: true })}
                />
              ))}
            </>
          ) : loading ? (
            <div className={styles.card}>
              <LoadingState text={t("AiJudgePanel.loadingChecks")} />
            </div>
          ) : (
            <div className={styles.card}>
              <EmptyCheckHero />
            </div>
          )}
        </div>
      </section>

      {typeof document !== "undefined" && sessionMenuItemKeep.open && sessionMenuPos.item && createPortal(renderSessionMenu(sessionMenuItemKeep.item), document.body)}

      {createDialogPresence.open && (
        <CreateCheckDialog
          closing={createDialogPresence.closing}
          busy={creatingCheck}
          error={createCheckError}
          weeks={titledWeeks}
          defaultWeekId={getDefaultWeekId(titledWeeks)}
          onClose={closeCreateCheckDialog}
          onSubmit={handleCreateCheck}
        />
      )}
      {moveWeekTarget && (
        <Modal
          as="form"
          onSubmit={moveSessionToWeek}
          onClose={() => setMoveWeekTarget(null)}
          title={t("AiJudgePanel.moveWeekTitle")}
          description={t("AiJudgePanel.moveWeekDesc", { title: moveWeekTarget.title })}
          actions={
            <>
              <button type="button" className={styles.btnSecondary} onClick={() => setMoveWeekTarget(null)}>{titledWeeks.length ? t("AiJudgePanel.cancelBtn") : t("AiJudgePanel.closeBtn")}</button>
              {titledWeeks.length > 0 && (
                <button type="submit" className={styles.btnPrimary} disabled={!moveWeekId || busySessionIds.has(moveWeekTarget.id)}>{t("AiJudgePanel.saveWeekBtn")}</button>
              )}
            </>
          }
        >
          <WeekSelectField weeks={titledWeeks} value={moveWeekId} onChange={setMoveWeekId} autoFocus />
        </Modal>
      )}

    </div>
  );
}

export default function AiJudgePanel({ classId, members, weeks = [], machineNodes = [] }) {
  return <TeacherWorkspacePanel classId={classId} members={members} weeks={weeks} machineNodes={machineNodes} />;
}
