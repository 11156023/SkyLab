import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  TEMPLATE_OPTIONS,
  getTemplateLabel,
  rubricToContext,
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

/** 自動檢測支援標籤：auto=綠、partial=琥珀、manual=紅。 */
const DETECTABLE_INFO = {
  auto: { label: "可自動", icon: "check", className: styles.detBadge_auto },
  partial: { label: "部分自動", icon: "change_history", className: styles.detBadge_partial },
  manual: { label: "不行", icon: "close", className: styles.detBadge_manual },
};

function getDetectableInfo(detectable) {
  return DETECTABLE_INFO[detectable] ?? DETECTABLE_INFO.manual;
}

const RUBRIC_FILE_EXTENSION = /\.(?:md|txt|doc|docx|pdf)$/i;

/**
 * 評分表的可讀名稱不應把匯入文件的副檔名帶進工作區標題；原始檔名仍
 * 保留在 `original_filename`，供衝突判斷與下載使用。
 */
export function getRubricDisplayName(file, fallback = "評分表") {
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
  return item.id ? "修改" : "新增";
}

function comparableItem(item) {
  return JSON.stringify({
    title: item.title ?? "",
    description: item.description ?? "",
    checked: Boolean(item.checked),
    detectable: item.detectable ?? "manual",
    detection_method: item.detection_method ?? null,
    fallback: item.fallback ?? null,
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

/** 只標記與最後儲存內容不同的檢查項目；整表旗標不會外溢到其他列。 */
export function getPendingRubricItemIds(
  currentItems,
  lastSavedItems,
  previousIds = [],
  lastSavedNeedsReview = false,
) {
  const current = Array.isArray(currentItems) ? currentItems : [];
  const saved = Array.isArray(lastSavedItems) ? lastSavedItems : [];
  const savedById = new Map(saved.filter((item) => item?.id).map((item) => [item.id, comparableItem(item)]));
  const currentIds = new Set(current.filter((item) => item?.id).map((item) => item.id));
  const next = new Set(previousIds ?? []);

  current.forEach((item) => {
    if (!item?.id) return;
    const savedValue = savedById.get(item.id);
    if (savedValue === undefined || savedValue !== comparableItem(item)) {
      next.add(item.id);
    } else if (!lastSavedNeedsReview) {
      next.delete(item.id);
    }
  });

  [...next].forEach((itemId) => {
    if (!currentIds.has(itemId)) next.delete(itemId);
  });
  return next;
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

function ProposalPanel({ proposal, selectedIds, onToggle, onApply, onSkip, disabled, isRefine = false }) {
  if (!proposal?.length) return null;
  return (
    <div className={styles.proposalCard}>
      <div className={styles.proposalHeading}>
        <div className={styles.createCheckBody}>
          <strong>{isRefine ? "AI一鍵整理提案" : "AI 評分表提案"}</strong>
          <p>{isRefine ? "請逐項確認評分目標、描述、成功條件與檢查設定後再套用。" : "逐項確認後才會套用到目前的評分表。"}</p>
        </div>
        <span>{selectedIds.size}/{proposal.length} 項</span>
      </div>
      <div className={styles.proposalList}>
        {proposal.map((item, index) => {
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
        <button type="button" className={styles.btnSecondary} disabled={disabled} onClick={onSkip}>略過</button>
        <button type="button" className={styles.btnPrimary} disabled={disabled || selectedIds.size === 0} onClick={onApply}>套用選取</button>
      </div>
    </div>
  );
}

/* ── 可編輯檢查項目表格 ───────────────────────────────── */

function DetectabilityBadge({ detectable, needsReview = false }) {
  const detectableInfo = getDetectableInfo(detectable);
  return (
    <span
      className={`${styles.detBadge} ${detectableInfo.className} ${needsReview ? styles.detBadge_stale : ""}`}
      title={needsReview ? `${detectableInfo.label}（待更新）` : detectableInfo.label}
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
  const hasDetails = Boolean(item.detection_method || item.fallback || checkSteps.length);

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
            <span className={styles.srOnly}>第 {index + 1} 項評分標準</span>
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
          <DetectabilityBadge detectable={item.detectable} needsReview={needsReview} />
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
                  {item.detection_method && (
                    <div className={styles.detectItem}>
                      <span>檢測方式</span>
                      <p>{item.detection_method}</p>
                    </div>
                  )}
                  {item.fallback && (
                    <div className={styles.detectItem}>
                      <span>無法自動檢測時</span>
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
        <caption className={styles.srOnly}>可編輯的 AI 檢查評分表</caption>
        <thead>
          <tr>
            <th scope="col" className={styles.rubricDetailToggleHeader}>
              <span className={styles.srOnly}>詳細設定</span>
            </th>
            <th scope="col">#</th>
            <th scope="col">檢查點</th>
            <th scope="col">評分標準</th>
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

/* ── 上傳區 ─────────────────────────────────────────────── */

function RubricUploader({ onUpload, onInvalidFile, isLoading }) {
  const [isDragging, setIsDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);

  function selectFile(file) {
    if (!file) return;
    const ext = file.name.split(".").pop()?.toLowerCase();
    if (!["md", "txt", "doc", "docx", "pdf"].includes(ext)) {
      onInvalidFile?.("只接受 .md、.txt、.doc、.docx 或 .pdf 資料文件。");
      return;
    }
    setSelectedFile(file);
  }

  function handleDrop(e) {
    e.preventDefault();
    setIsDragging(false);
    selectFile(e.dataTransfer.files?.[0]);
  }

  return (
    <div className={styles.uploaderWrap}>
      <div
        className={`${styles.dropZone} ${isDragging ? styles.dropZoneDragging : ""} ${isLoading ? styles.dropZoneLoading : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={(e) => {
          e.preventDefault();
          setIsDragging(false);
        }}
        onDrop={handleDrop}
      >
        <input
          type="file"
          accept=".md,.txt,.doc,.docx,.pdf"
          className={styles.dropZoneInput}
          disabled={isLoading}
          onChange={(e) => {
            selectFile(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        {selectedFile ? (
          <div className={styles.selectedFile}>
            <MIcon name="description" size={36} />
            <div>
              <p className={styles.selectedFileName}>{selectedFile.name}</p>
              <p className={styles.selectedFileMeta}>
                {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
              </p>
            </div>
            <button
              type="button"
              className={styles.iconBtn}
              aria-label="清除選擇"
              disabled={isLoading}
              onClick={(e) => {
                e.stopPropagation();
                setSelectedFile(null);
              }}
            >
              <MIcon name="close" size={16} />
            </button>
          </div>
        ) : (
          <div className={styles.dropHint}>
            <MIcon name="upload" size={36} />
            <p className={styles.dropHintTitle}>拖放資料文件到這裡</p>
            <p className={styles.dropHintMeta}>或點擊選擇檔案（支援 .md、.txt、.doc、.docx、.pdf）</p>
          </div>
        )}
      </div>

      {selectedFile && (
        <button
          type="button"
          className={`${styles.btnPrimary} ${styles.btnBlock}`}
          disabled={isLoading}
          onClick={() => onUpload(selectedFile)}
        >
          {isLoading ? (
            <>
              <Spinner />
              AI 分析中...
            </>
          ) : (
            <>
              <MIcon name="upload" size={16} />
              上傳並分析
            </>
          )}
        </button>
      )}
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
  onCreateScript,
  isCreatingScript = false,
  canCreateScript = false,
  createScriptHint = "",
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
            <p>{hasRubric ? "與 AI 對話來精煉你的評分表" : "先和 AI 討論你的檢查需求"}</p>
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
                  disabled={isLoading || isClearing || isUploading}
                  onClick={() => onRemoveAttachment(attachment)}
                >
                  <MIcon name="close" size={14} />
                </button>}
              </div>
            ))}
          </div>
        )}
        <div className={styles.chatActions}>
          <button
            type="button"
            className={styles.btnSecondary}
            disabled={isLoading || isClearing || isUploading || disabled || !hasRubric}
            onClick={() => onSendMessage(RUBRIC_POLISH_PROMPT, true)}
            title="(好用) AI幫助你把評分表規則化，後續方便腳本生成"
          >
            {isLoading ? <Spinner size={14} /> : <MIcon name="auto_fix_high" size={14} />}
            AI一鍵整理
          </button>
          {onCreateScript && (
            <button
              type="button"
              className={styles.btnPrimary}
              disabled={isLoading || isClearing || isUploading || disabled || isCreatingScript || !canCreateScript}
              onClick={onCreateScript}
              title={createScriptHint || undefined}
            >
              {isCreatingScript ? <Spinner size={14} /> : <MIcon name="terminal" size={14} />}
              {isCreatingScript ? "製作中..." : "製作檢查腳本"}
            </button>
          )}
          {onToggleSources && <button
            type="button"
            className={styles.btnSecondary}
            disabled={isLoading || isClearing || isUploading}
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
            ? "提示：詢問問題不會修改評估表，需明確指令（如「幫我改」「新增」）才會執行變更"
            : "提示：先用＋上傳文件；分析完成後，AI 才會提出可套用的檢查項目修改"}
        </p>
      </div>
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

function CreateCheckForm({
  classId,
  weeks = [],
  embedded = false,
  initialMode = "",
  closing = false,
  onClose,
  onCreated,
}) {
  const requestVersionRef = useRef(0);
  const availableWeeks = weeks.filter((week) => week.title?.trim());
  const [selectedWeekId, setSelectedWeekId] = useState("");
  const [mode, setMode] = useState(initialMode);
  const [rubricName, setRubricName] = useState("");
  const [environmentKeys, setEnvironmentKeys] = useState(() => (
    initialMode === "existing" ? ["linux"] : []
  ));
  const [files, setFiles] = useState([]);
  const [selectedFileId, setSelectedFileId] = useState("");
  const [uploading, setUploading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [conflictFile, setConflictFile] = useState(null);
  const [invalid, setInvalid] = useState({});
  const weekSelectRef = useRef(null);
  const modeGroupRef = useRef(null);
  const rubricNameRef = useRef(null);
  const envGroupRef = useRef(null);
  const existingListRef = useRef(null);

  useEffect(() => {
    if (mode !== "existing") {
      setFiles([]);
      return undefined;
    }
    const requestVersion = ++requestVersionRef.current;
    let cancelled = false;
    AiJudgeService.listFiles(classId)
      .then((rows) => {
        if (!cancelled && requestVersion === requestVersionRef.current) {
          setFiles(rows.filter((file) => file.status === "active"));
        }
      })
      .catch(() => {
        if (!cancelled && requestVersion === requestVersionRef.current) {
          setError("載入已保存評分表失敗，仍可上傳新文件。");
        }
      });
    return () => {
      cancelled = true;
      requestVersionRef.current += 1;
    };
  }, [classId, mode]);

  function toggleEnvironment(key) {
    setEnvironmentKeys((current) => current.includes(key)
      ? current.filter((item) => item !== key)
      : [...current, key]);
    setInvalid((v) => ({ ...v, envKeys: false }));
  }

  function handleModeChange(nextMode) {
    setMode(nextMode);
    setInvalid((v) => ({ ...v, mode: false }));
    setError("");
    if (nextMode === "existing") {
      setEnvironmentKeys((current) => current.length ? current : ["linux"]);
    }
  }

  async function uploadFile(file, conflictStrategy = null) {
    if (!file) return;
    if (!selectedWeekId) {
      setError("請先選擇這份檢查要放入的週任務。");
      setInvalid((v) => ({ ...v, week: true }));
      focusInvalidField(weekSelectRef.current);
      return;
    }
    const requestVersion = requestVersionRef.current;
    setUploading(true);
    setError("");
    try {
      const result = await AiJudgeService.uploadFile(
        classId,
        file,
        environmentKeys[0] ?? "linux",
        conflictStrategy,
        environmentKeys,
      );
      if (requestVersion !== requestVersionRef.current) return;
      const uploaded = result.file
        ? { ...result.file, analysis_json: result.file.analysis_json ?? result.analysis }
        : { ...result, analysis_json: result.analysis };
      setFiles((current) => [uploaded, ...current.filter((item) => item.id !== uploaded.id)]);
      setSelectedFileId(uploaded.id);
      setEnvironmentKeys(uploaded.environment_keys?.length ? uploaded.environment_keys : [uploaded.template_key]);
      setConflictFile(null);
      setCreating(true);
      try {
        const created = await AiJudgeService.createSession(classId, {
          title: getRubricCheckTitle({
            name: file.name,
            original_filename: uploaded.original_filename,
            display_name: uploaded.display_name,
          }),
          creationMode: "existing",
          selectedFileId: uploaded.id,
          teachingClassWeekId: selectedWeekId,
        });
        if (requestVersion === requestVersionRef.current) onCreated(created, "uploaded");
      } catch (createError) {
        setError(`AI 分析完成，但建立檢查失敗：${createError?.message ?? "請稍後再試"}`);
      } finally {
        setCreating(false);
      }
    } catch (uploadError) {
      if (requestVersion !== requestVersionRef.current) return;
      if (uploadError?.status === 409) {
        setConflictFile(file);
        setError("已有同名資料文件，請選擇覆蓋原本文件或建立副本。");
      } else {
        setError(uploadError?.message ?? "上傳資料文件失敗。");
      }
    } finally {
      if (requestVersion === requestVersionRef.current) setUploading(false);
    }
  }

  async function submit(event) {
    event.preventDefault();
    const missing = {
      week: !selectedWeekId,
      mode: !mode,
      rubricName: mode === "blank" && !rubricName.trim(),
      envKeys: mode === "blank" && !environmentKeys.length,
      file: mode === "existing" && !selectedFileId,
    };
    if (Object.values(missing).some(Boolean)) {
      setInvalid(missing);
      if (missing.week) {
        setError("請先選擇這份檢查要放入的週任務。");
        focusInvalidField(weekSelectRef.current);
      }
      else if (missing.mode) focusInvalidField(modeGroupRef.current?.querySelector("input"));
      else if (missing.rubricName) focusInvalidField(rubricNameRef.current);
      else if (missing.envKeys) focusInvalidField(envGroupRef.current?.querySelector("input"));
      else focusInvalidField(existingListRef.current?.querySelector("input"));
      return;
    }
    setCreating(true);
    setError("");
    try {
      const selectedFile = files.find((file) => file.id === selectedFileId);
      const created = await AiJudgeService.createSession(classId, {
        title: mode === "blank"
          ? getRubricCheckTitle({ name: rubricName })
          : getRubricCheckTitle(selectedFile),
        creationMode: mode,
        rubricName: mode === "blank" ? rubricName.trim() : undefined,
        environmentKeys: mode === "blank" ? environmentKeys : undefined,
        selectedFileId: mode === "existing" ? selectedFileId : null,
        teachingClassWeekId: selectedWeekId,
      });
      onCreated(created, mode);
    } catch (createError) {
      setError(createError?.message ?? "建立檢查失敗，請確認欄位後重試。");
    } finally {
      setCreating(false);
    }
  }

  const form = (
    <section
      className={embedded ? styles.createCheckPanel : `${styles.confirm} ${styles.createCheckDialog}`}
      role={embedded ? undefined : "dialog"}
      aria-modal={embedded ? undefined : "true"}
      aria-labelledby="create-check-title"
    >
      <div className={styles.modalHeader}>
        <div>
          {embedded && <button type="button" className={styles.inlineBackButton} disabled={creating || uploading} onClick={onClose}><MIcon name="arrow_back" size={17} />返回建立方式</button>}
          <h2 id="create-check-title">{mode === "blank" ? "從零開始建立" : mode === "existing" ? "使用已有評分文件" : "新增檢查"}</h2>
          <p>{mode === "blank" ? "建立空白評分表後，會留在 AI 檢查主頁編輯檢查項目與 AI 提案。" : mode === "existing" ? "選擇已保存的評分表，或上傳文件交由 AI 分析；完成後會回到 AI 檢查主頁。" : "選擇建立方式後開始準備評分表。"}</p>
        </div>
        {!embedded && <button type="button" className={styles.iconBtn} aria-label="關閉" disabled={creating || uploading} onClick={onClose}><MIcon name="close" size={18} /></button>}
      </div>
      <form onSubmit={submit}>
        <label className={styles.dialogField}>
          <span>放到哪一週任務？</span>
          <select ref={weekSelectRef} className={invalid.week ? styles.fieldInvalid : undefined} value={selectedWeekId} onChange={(event) => { setSelectedWeekId(event.target.value); setInvalid((v) => ({ ...v, week: false })); }}>
            <option value="" disabled>{availableWeeks.length ? "請選擇週任務" : "請先建立有名稱的週任務"}</option>
            {availableWeeks.map((week) => <option key={week.id} value={week.id}>第 {week.week ?? week.week_number} 週 · {week.title}{["published", "completed"].includes(week.status) ? "" : "（草稿）"}</option>)}
          </select>
          <small>AI 檢查核准後，Checkpoint 才會顯示在學生端；草稿週次發布後才會讓學生看到。</small>
        </label>

        {!embedded && <fieldset className={styles.modeFieldset}>
          <legend>如何建立評分表？</legend>
          <div ref={modeGroupRef} className={`${styles.modeChoices} ${invalid.mode ? styles.groupInvalid : ""}`}>
            <label className={mode === "blank" ? styles.modeChoiceActive : styles.modeChoice}>
              <input type="radio" name="creation-mode" checked={mode === "blank"} onChange={() => handleModeChange("blank")} />
              <span><b>從零開始建立</b><small>建立空白評分表，接著手動新增項目或請 AI 產生初稿。</small></span>
            </label>
            <label className={mode === "existing" ? styles.modeChoiceActive : styles.modeChoice}>
              <input type="radio" name="creation-mode" checked={mode === "existing"} onChange={() => handleModeChange("existing")} />
              <span><b>使用已有評分文件</b><small>選擇班級已保存的評分表，或上傳資料文件；檢查名稱會使用文件名稱。</small></span>
            </label>
          </div>
        </fieldset>}

        {mode === "blank" && <div className={styles.modeFields}>
          <label className={styles.dialogField}>
            <span>評分表名稱</span>
            <input ref={rubricNameRef} className={invalid.rubricName ? styles.fieldInvalid : undefined} autoFocus value={rubricName} maxLength={255} placeholder="例如：期中 Python 評分表" onChange={(event) => { setRubricName(event.target.value); setInvalid((v) => ({ ...v, rubricName: false })); }} />
          </label>
          <fieldset className={styles.modeFieldset}>
            <legend>評分環境（可複選）</legend>
            <div ref={envGroupRef} className={`${styles.dialogChips} ${invalid.envKeys ? styles.groupInvalid : ""}`}>{TEMPLATE_OPTIONS.map((option) => <label key={option.key} className={environmentKeys.includes(option.key) ? styles.dialogChipActive : styles.dialogChip}><input type="checkbox" checked={environmentKeys.includes(option.key)} onChange={() => toggleEnvironment(option.key)} disabled={creating} />{option.label}</label>)}</div>
          </fieldset>
        </div>}

        {mode === "existing" && <div className={styles.existingPicker}>
          <div className={styles.uploadSourceBlock}>
            <div className={styles.existingPickerHead}><div><span>上傳新的資料文件</span><small>支援 .md／.txt／.doc／.docx／.pdf；分析完成後會以文件名稱建立檢查。</small></div></div>
            <fieldset className={styles.modeFieldset}>
              <legend>評分環境（可複選）</legend>
              <p className={styles.uploadEnvironmentHint}>第一個選擇會作為 AI 分析的主要情境，其餘環境可提供跨環境檢查能力。</p>
              <div className={styles.dialogChips}>{TEMPLATE_OPTIONS.map((option) => <label key={option.key} className={environmentKeys.includes(option.key) ? styles.dialogChipActive : styles.dialogChip}><input type="checkbox" checked={environmentKeys.includes(option.key)} onChange={() => toggleEnvironment(option.key)} disabled={uploading || creating} />{option.label}</label>)}</div>
            </fieldset>
            <RubricUploader onUpload={(file) => uploadFile(file)} onInvalidFile={setError} isLoading={uploading || creating || !selectedWeekId} />
          </div>
          <div className={styles.savedRubricBlock}>
            <div className={styles.existingPickerHead}><div><span>或選擇已保存評分表</span><small>每份來源只能綁定一個檢查；若要重構，請使用「重構」。</small></div></div>
            {files.length ? <div ref={existingListRef} className={`${styles.existingList} ${invalid.file ? styles.groupInvalid : ""}`}>{files.map((file) => <label key={file.id} className={selectedFileId === file.id ? styles.existingRowActive : styles.existingRow}><input type="radio" name="saved-rubric" checked={selectedFileId === file.id} onChange={() => { setSelectedFileId(file.id); setInvalid((v) => ({ ...v, file: false })); }} /><span><b>{getRubricDisplayName(file, "未命名評分表")}</b><small>{(file.environment_keys?.length ? file.environment_keys : [file.template_key]).map(getTemplateLabel).join("、")} · {file.analysis_json?.items?.length ?? 0} 項 · {formatDateTime(file.updated_at)}</small></span></label>)}</div> : <p className={styles.mutedText}>尚未有可用的評分表。</p>}
          </div>
          {conflictFile && <div className={styles.conflictActions} role="alert"><span>「{conflictFile.name}」已存在：</span><button type="button" className={styles.btnSecondary} disabled={uploading || creating} onClick={() => uploadFile(conflictFile, "copy")}>建立副本</button><button type="button" className={styles.btnDanger} disabled={uploading || creating} onClick={() => uploadFile(conflictFile, "overwrite")}>覆蓋原本</button><button type="button" className={styles.iconBtn} aria-label="取消同名處理" onClick={() => setConflictFile(null)}><MIcon name="close" size={16} /></button></div>}
        </div>}

        {error && <p className={styles.dialogError} role="alert">{error}</p>}
        <div className={styles.modalActions}>
          {!embedded && <button type="button" className={styles.btnSecondary} disabled={creating || uploading} onClick={onClose}>取消</button>}
          {(mode === "blank" || (mode === "existing" && selectedFileId)) && <button type="submit" className={styles.btnPrimary} disabled={creating || uploading}>{creating ? <><Spinner size={15} />建立中…</> : mode === "blank" ? "建立檢查" : "使用這份評分表"}</button>}
          {mode === "existing" && !selectedFileId && <p className={styles.uploadAutoHint}>選擇已保存的評分表後按「使用這份評分表」；上傳文件後會自動分析並建立檢查。</p>}
        </div>
      </form>
    </section>
  );

  if (embedded) return form;
  return <div className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !creating && !uploading) onClose(); }}>{form}</div>;
}

export function CreateCheckChooser({ onChoose, onCancel, busy = false, error = "" }) {
  return (
    <section className={styles.createChooser} aria-labelledby="create-check-choice-title">
      <div className={styles.createChooserHeader}>
        <div className={styles.createChooserHeading}>
          <h2 id="create-check-choice-title">新增檢查</h2>
          <p>選擇後直接進入對應工作區；從零建立會立即開啟空白評分表。</p>
        </div>
        <button type="button" className={styles.btnSecondary} disabled={busy} onClick={onCancel}>返回目前檢查</button>
      </div>
      {error && <p className={styles.dialogError} role="alert">{error}</p>}
      <div className={styles.createChoiceGrid}>
        <button type="button" className={styles.createChoice} disabled={busy} onClick={() => onChoose("blank")}>
          <span className={styles.createChoiceIcon}><MIcon name="edit_note" size={30} /></span>
          <span className={styles.createChoiceCopy}><strong>從零開始建立</strong><small>立即開啟空白評分表，在同一頁填寫名稱、模板與檢查項目，也可以請 AI 產生初稿。</small></span>
          <span className={styles.createChoiceAction}>{busy ? "正在開啟空白頁面…" : "開始設計"}<MIcon name={busy ? "sync" : "arrow_forward"} size={18} /></span>
        </button>
        <button type="button" className={styles.createChoice} disabled={busy} onClick={() => onChoose("existing")}>
          <span className={styles.createChoiceIcon}><MIcon name="upload_file" size={30} /></span>
          <span className={styles.createChoiceCopy}><strong>使用已有評分文件</strong><small>選用尚未綁定其他檢查的來源，或上傳資料文件；已使用來源請選擇「重構」。</small></span>
          <span className={styles.createChoiceAction}>選擇文件<MIcon name="arrow_forward" size={18} /></span>
        </button>
      </div>
    </section>
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

function RubricSourceRail({ classId, judgeSession, onSessionUpdated, onClose, embedded = false }) {
  const toast = useToast();
  const sourceRailRef = useRef(null);
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [openMenuId, setOpenMenuId] = useState(null);
  const [busyId, setBusyId] = useState(null);
  // 來源選單離場動畫：關閉時保留最後開啟的列 130ms
  const sourceMenuKeep = useDialogPresence(openMenuId, 130);
  const selectedFileId = judgeSession?.selected_file_id ?? null;

  const load = useCallback(async () => {
    if (!selectedFileId) {
      setFiles([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try { setFiles(await AiJudgeService.listFiles(classId)); }
    catch (error) { toast.error(error?.message ?? "載入資料來源失敗"); }
    finally { setLoading(false); }
  }, [classId, selectedFileId, toast]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    setOpenMenuId(null);
  }, [selectedFileId]);

  useEffect(() => {
    if (!openMenuId) return undefined;
    const menuSelector = `[aria-expanded="true"]`;
    const focusTimer = window.setTimeout(() => {
      sourceRailRef.current?.querySelector(`${menuSelector} + [role="menu"] [role="menuitem"]:not(:disabled)`)?.focus();
    }, 0);
    function closeMenuOnOutsideClick(event) {
      const target = event.target;
      if (target instanceof Element && (target.closest('[role="menu"]') || target.closest('[aria-expanded="true"]'))) return;
      setOpenMenuId(null);
    }
    function closeMenuOnEscape(event) {
      if (event.key === "Escape") setOpenMenuId(null);
    }
    function navigateSourceMenu(event) {
      if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
      const menu = sourceRailRef.current?.querySelector('[role="menu"]');
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
    document.addEventListener("keydown", closeMenuOnEscape);
    document.addEventListener("keydown", navigateSourceMenu);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("mousedown", closeMenuOnOutsideClick);
      document.removeEventListener("keydown", closeMenuOnEscape);
      document.removeEventListener("keydown", navigateSourceMenu);
    };
  }, [openMenuId]);

  async function download(file) {
    try { const blob = await AiJudgeService.downloadFile(classId, file.id); downloadBlob(blob, file.original_filename ?? `${getRubricDisplayName(file)}.pdf`); }
    catch (error) { toast.error(error?.message ?? "下載資料文件失敗"); }
    finally { setOpenMenuId(null); }
  }

  async function remove(file) {
    if (!window.confirm(`確定刪除「${getRubricDisplayName(file)}」？已建立的腳本不會受影響。`)) return;
    setBusyId(file.id);
    try {
      await AiJudgeService.deleteFile(classId, file.id);
      setFiles((current) => current.filter((entry) => entry.id !== file.id));
      if (file.id === judgeSession?.selected_file_id) onSessionUpdated(await AiJudgeService.getSession(classId, judgeSession.id));
      toast.success("資料來源已刪除");
    } catch (error) { toast.error(error?.message ?? "刪除資料來源失敗"); }
    finally { setBusyId(null); setOpenMenuId(null); }
  }

  const selectedFile = getSelectedRubricSource(files, selectedFileId);
  const visibleFiles = selectedFile ? [selectedFile] : [];
  return (
    <aside ref={sourceRailRef} className={`${styles.sourceRail} ${embedded ? styles.sourceRailEmbedded : ""}`} aria-label="資料來源">
      <div className={styles.sourceRailHead}>
        <div>
          <h3>資料來源</h3>
          <p>{loading ? "正在確認目前來源…" : selectedFile ? "目前檢查使用的來源" : "尚未選擇來源"}</p>
        </div>
        <div className={styles.sourceRailActions}>
          {onClose && <button type="button" className={styles.iconBtn} aria-label="關閉資料來源" title="關閉" onClick={onClose}><MIcon name="close" size={18} /></button>}
        </div>
      </div>
      {loading ? <p className={styles.mutedText}>載入來源中…</p> : visibleFiles.length > 0 ? (
        <div className={styles.sourceList}>
           {visibleFiles.map((file) => <div key={file.id} className={`${styles.sourceRow} ${file.id === selectedFileId ? styles.sourceRowSelected : ""}`}><div className={styles.sourceSelect} aria-current="true"><span className={styles.sourceIndicator} aria-hidden="true"><MIcon name="radio_button_checked" size={17} /></span><span className={styles.sourceText}><b>{getRubricDisplayName(file, "未命名評分表")}</b><small>{(file.environment_keys?.length ? file.environment_keys : [file.template_key]).map(getTemplateLabel).join("、")} · {file.analysis_json?.items?.length ?? 0} 項 · {formatDateTime(file.updated_at)} · {file.source_type === "created" ? "建立於系統" : "已上傳"}</small><em>已選用</em></span></div><div className={styles.sourceActions}><button type="button" className={styles.iconBtn} aria-label={`管理 ${getRubricDisplayName(file)}`} title="管理資料來源" aria-haspopup="menu" aria-expanded={openMenuId === file.id} onClick={(event) => { event.stopPropagation(); setOpenMenuId((current) => current === file.id ? null : file.id); }}><MIcon name="more_vert" size={18} /></button>{sourceMenuKeep.item === file.id && <div className={`${styles.sourceMenu} ${sourceMenuKeep.closing ? styles.sessionMenuOut : ""}`} role="menu">{file.source_type !== "created" && <button type="button" role="menuitem" onClick={() => download(file)}><MIcon name="download" size={15} />下載原始文件</button>}<button type="button" role="menuitem" className={styles.menuDanger} disabled={busyId === file.id} onClick={() => remove(file)}><MIcon name="delete" size={15} />刪除來源</button></div>}</div></div>)}
        </div>
      ) : <div className={styles.sourceEmpty}><MIcon name="description" size={24} /><p>{selectedFileId ? "目前資料來源已無法使用，請用聊天室輸入框旁的＋重新上傳。" : "請用聊天室輸入框旁的＋上傳文件。"}</p></div>}
    </aside>
  );
}

/* ── Tab 1：評分表 ──────────────────────────────────────── */

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
  const [uploadedFileName, setUploadedFileName] = useState("rubric");
  const [sourceFileId, setSourceFileId] = useState(null);
  const [pendingConflictFile, setPendingConflictFile] = useState(null);
  const conflictDialog = useDialogPresence(pendingConflictFile);
  const [selectedTemplateKey, setSelectedTemplateKey] = useState("linux");
  const [analysisTemplateKey, setAnalysisTemplateKey] = useState("linux");
  const [pendingProposal, setPendingProposal] = useState(null);
  const [selectedProposalIds, setSelectedProposalIds] = useState(() => new Set());
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [pendingProposalMeta, setPendingProposalMeta] = useState(null);
  const [pendingProposalIsRefine, setPendingProposalIsRefine] = useState(false);
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
  classIdRef.current = classId;
  toastRef.current = toast;
  useEffect(() => {
    if (!sourcesOpen) return undefined;
    function closeOnEscape(event) {
      if (event.key === "Escape") setSourcesOpen(false);
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [sourcesOpen]);

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
        setFiles((current) => current.map((entry) => (
          entry.id === updated.id ? updated : entry
        )));
      },
      onError(error) {
        toastRef.current.error(error?.message ?? "更新評分表失敗");
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
      setUploadedFileName("rubric");
      setSourceFileId(null);
      setEnvironmentKeys([]);
      setAnalysisTemplateKey("linux");
      setSelectedTemplateKey("linux");
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
    setUploadedFileName(file.original_filename || "rubric");
    setSourceFileId(file.id);
    setEnvironmentKeys(file.environment_keys?.length ? file.environment_keys : [file.template_key]);
    analysisRevisionsRef.current.set(file.id, file.analysis_revision);
    lastSavedValuesRef.current.set(file.id, getRubricItemsValue(file.analysis_json));
    lastSavedItemsRef.current.set(file.id, Array.isArray(file.analysis_json.items) ? file.analysis_json.items : []);
    lastSavedNeedsReviewRef.current.set(file.id, Boolean(file.analysis_json.detectability_needs_review));
    setPendingReviewIds(new Set(pendingReviewIdsByFileRef.current.get(file.id) ?? []));
    setAnalysisTemplateKey(file.template_key);
    setSelectedTemplateKey(file.template_key);
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

  /** 更新分析結果；persist 時同步寫回已保存的評分表 */
  function applyAnalysis(
    nextAnalysis,
    { persist = false, immediate = false, detectabilityNeedsReview } = {},
  ) {
    const currentValue = getRubricItemsValue(nextAnalysis);
    const lastSavedValue = sourceFileId ? lastSavedValuesRef.current.get(sourceFileId) : undefined;
    const hasActualChange = lastSavedValue !== undefined && currentValue !== lastSavedValue;
    const lastSavedNeedsReview = sourceFileId
      ? Boolean(lastSavedNeedsReviewRef.current.get(sourceFileId))
      : false;
    const evaluatedNeedsReview = typeof detectabilityNeedsReview === "boolean"
      ? (detectabilityNeedsReview ? hasActualChange || lastSavedNeedsReview : false)
      : nextAnalysis.detectability_needs_review;
    const evaluatedAnalysis = typeof evaluatedNeedsReview === "boolean"
      ? { ...nextAnalysis, detectability_needs_review: evaluatedNeedsReview }
      : nextAnalysis;
    setAnalysis(evaluatedAnalysis);
    if (persist && sourceFileId) {
      autosaveRef.current?.schedule({ fileId: sourceFileId, analysis: evaluatedAnalysis });
      if (immediate) return autosaveRef.current?.flush() ?? Promise.resolve(false);
    }
    return Promise.resolve(true);
  }

  function updatePendingReviewIds(nextItems) {
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
    );
    if (sourceFileId) pendingReviewIdsByFileRef.current.set(sourceFileId, nextIds);
    setPendingReviewIds(nextIds);
    return nextIds;
  }

  async function handleUpload(file, conflictStrategy) {
    setIsUploading(true);
    try {
      let response;
      try {
        response = await AiJudgeService.uploadFile(
          classId,
          file,
          selectedTemplateKey,
          conflictStrategy,
          environmentKeys,
        );
      } catch (err) {
        if (err?.status === 409) {
          setPendingConflictFile(file);
        } else {
          toast.error(err?.message ?? "上傳失敗");
        }
        return false;
      }
      const uploadedFile = {
        ...response.file,
        analysis_json: response.file.analysis_json ?? response.analysis,
      };
      setPendingConflictFile(null);
      if (judgeSession?.id) {
        try {
          const updated = await AiJudgeService.updateSession(classId, judgeSession.id, {
            selected_file_id: uploadedFile.id,
          });
          onSessionUpdated?.(updated);
        } catch (err) {
          toast.error(err?.message ?? "套用資料來源失敗");
          await fetchFiles();
          return false;
        }
      }
      setAnalysis(response.analysis);
      setUploadedFileName(file.name || "rubric");
      setSourceFileId(uploadedFile.id);
      setEnvironmentKeys(uploadedFile.environment_keys?.length ? uploadedFile.environment_keys : [uploadedFile.template_key]);
      analysisRevisionsRef.current.set(uploadedFile.id, uploadedFile.analysis_revision);
      lastSavedValuesRef.current.set(uploadedFile.id, getRubricItemsValue(uploadedFile.analysis_json));
      lastSavedItemsRef.current.set(uploadedFile.id, Array.isArray(uploadedFile.analysis_json?.items) ? uploadedFile.analysis_json.items : []);
      lastSavedNeedsReviewRef.current.set(uploadedFile.id, Boolean(uploadedFile.analysis_json?.detectability_needs_review));
      pendingReviewIdsByFileRef.current.set(uploadedFile.id, new Set());
      setPendingReviewIds(new Set());
      setAnalysisTemplateKey(response.template_key ?? selectedTemplateKey);
      setSelectedTemplateKey(response.template_key ?? selectedTemplateKey);
      setFiles((current) => [
        uploadedFile,
        ...current.filter((item) => item.id !== uploadedFile.id),
      ]);
      toast.success(`分析完成：${response.analysis.items.length} 題檢查項目`);
      fetchFiles();
      return true;
    } catch (err) {
      toast.error(err?.message ?? "上傳失敗");
      return false;
    } finally {
      setIsUploading(false);
    }
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
    if (!judgeSession?.id && !analysis) return;
    if (attachments.length && !judgeSession?.id) {
      toast.error("附件需要在已保存的檢查中送出。");
      return;
    }
    if (autosaveRef.current && !(await autosaveRef.current.flush())) return;
    const requestMessages = [...messages, { role: "user", content, attachments }];
    const newMessages = isRefine ? messages : requestMessages;
    setMessages(newMessages);
    setIsChatting(true);
    try {
      if (judgeSession?.id) {
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
          });
          if (saved) {
            if (sourceFileId) pendingReviewIdsByFileRef.current.set(sourceFileId, new Set());
            setPendingReviewIds(new Set());
            toast.success("潤飾完成，評分表目前無需修改。");
          }
        }
        return;
      }
      const response = await AiJudgeService.chat({
        messages: requestMessages,
        rubricContext: rubricToContext(analysis),
        isRefine,
        templateKey: analysisTemplateKey,
      });
      setPendingAttachments([]);
      setMessages((prev) => [...prev, { role: "assistant", content: response.reply }]);
      if (response.updated_items) {
        const saved = await applyAnalysis(applyItems(analysis, response.updated_items), {
          persist: true,
          immediate: true,
          detectabilityNeedsReview: false,
        });
        if (!saved) return;
        if (sourceFileId) pendingReviewIdsByFileRef.current.set(sourceFileId, new Set());
        setPendingReviewIds(new Set());
        toast.success(isRefine ? "潤飾完成，評分表已更新。" : "評估表已更新");
      } else if (isRefine) {
        toast.error("AI 未回傳完整檢查項目列表，潤飾尚未套用，請稍後再試");
      }
    } catch (err) {
      toast.error(err?.message ?? "對話失敗");
      setMessages(messages);
    } finally {
      setIsChatting(false);
    }
  }

  async function applyPendingProposal() {
    if (!pendingProposal) return;
    if (autosaveRef.current && !(await autosaveRef.current.flush())) return;
    const currentRevision = sourceFileId ? analysisRevisionsRef.current.get(sourceFileId) : null;
    if (pendingProposalMeta?.baseRevision && currentRevision !== pendingProposalMeta.baseRevision) {
      setPendingProposal(null);
      setSelectedProposalIds(new Set());
      setPendingProposalMeta(null);
      setPendingProposalIsRefine(false);
      toast.error("評分表已經有新的修改，請重新請 AI 產生提案。");
      return;
    }
    const selected = pendingProposal.filter((item, index) => selectedProposalIds.has(item.id ?? `proposal-${index}`));
    const byId = new Map((analysis?.items ?? []).map((item) => [item.id, item]));
    const evaluatedIds = new Set();
    selected.forEach((item) => {
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
    const currentPendingIds = sourceFileId
      ? pendingReviewIdsByFileRef.current.get(sourceFileId) ?? pendingReviewIds
      : pendingReviewIds;
    const pendingIdsAfterApply = new Set(currentPendingIds);
    evaluatedIds.forEach((id) => pendingIdsAfterApply.delete(id));
    const saved = await applyAnalysis(applyItems(analysis, [...byId.values()]), {
      persist: true,
      immediate: true,
      detectabilityNeedsReview: pendingIdsAfterApply.size > 0,
    });
    if (!saved) return;
    if (sourceFileId) pendingReviewIdsByFileRef.current.set(sourceFileId, pendingIdsAfterApply);
    setPendingReviewIds(pendingIdsAfterApply);
    setPendingProposal(null);
    setSelectedProposalIds(new Set());
    setPendingProposalMeta(null);
    setPendingProposalIsRefine(false);
    toast.success("已套用 AI 提出的檢查項目修改");
  }

  function handleItemChange(index, updatedItem) {
    const nextItems = [...analysis.items];
    nextItems[index] = updatedItem;
    updatePendingReviewIds(nextItems);
    applyAnalysis(applyItems(analysis, nextItems), {
      persist: true,
      detectabilityNeedsReview: true,
    });
  }

  function handleItemDelete(index) {
    const nextItems = analysis.items.filter((_, i) => i !== index);
    updatePendingReviewIds(nextItems);
    applyAnalysis(applyItems(analysis, nextItems), {
      persist: true,
      detectabilityNeedsReview: true,
    });
  }

  function handleAddItem() {
    const newItem = {
      id: `item-${Date.now()}`,
      title: "新檢查項目",
      description: "",
      checked: false,
      detectable: "manual",
      detection_method: null,
      fallback: null,
      check_steps: [],
    };
    const nextItems = [...analysis.items, newItem];
    updatePendingReviewIds(nextItems);
    applyAnalysis(applyItems(analysis, nextItems), {
      persist: true,
      detectabilityNeedsReview: true,
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
      toast.success("對話內容已清除");
    } catch (err) {
      toast.error(err?.message ?? "清除對話內容失敗");
    } finally {
      setIsClearingMessages(false);
    }
  }

  async function handleCreateScript() {
    setIsCreatingScript(true);
    try {
      if (autosaveRef.current && !(await autosaveRef.current.flush())) return;
      const artifact = judgeSession?.id
        ? await AiJudgeService.createSessionScript(classId, judgeSession.id)
        : await AiJudgeService.createScript(classId, {
            name: uploadedFileName,
            templateKey: analysisTemplateKey,
            rubricSnapshot: analysis,
            sourceFileId,
          });
      if (artifact.status === "approved") {
        toast.success("檢查腳本已通過靜態與 AI 檢查，可開始執行");
      } else if (artifact.status === "review_failed") {
        toast.error("自動修正仍未通過，已將原因放到腳本總覽");
      } else {
        toast.success("檢查腳本已產生，請查看審查結果");
      }
      onScriptCreated?.(artifact);
    } catch (err) {
      toast.error(err?.message ?? "製作檢查腳本失敗");
    } finally {
      setIsCreatingScript(false);
    }
  }

  const items = analysis?.items ?? [];

  return (
    <div className={styles.tabBody}>
      {isCreatingScript && (
        <div className={`${styles.noticeInfo} ${styles.noticeProgress}`} role="status" aria-live="polite">
          <p className={styles.noticeProgressTitle}>
            <MIcon name="autorenew" size={16} />
            <strong>正在製作檢查腳本</strong>
          </p>
          <p>
            AI 正在依目前檢查項目產生收集腳本，完成後會接著進行安全規則檢查與 AI 複核；
            <strong className={styles.noticeProgressHighlight}>可以離開此頁面，稍後回到「腳本總覽」查看結果。</strong>
          </p>
        </div>
      )}

      {analysis && items.length === 0 && (
        <div className={styles.noticeInfo}>
          <p><strong>尚未新增檢查項目</strong></p>
          <p>請先新增至少一個檢查項目，才能製作檢查腳本。</p>
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
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    onClick={handleAddItem}
                  >
                    <MIcon name="add" size={16} />
                    新增項目
                  </button>
                </div>
                <div className={sidebar ? styles.checkRubricBody : undefined}>
                  <RubricTable
                    items={items}
                    onChange={handleItemChange}
                    onDelete={handleItemDelete}
                    disabled={isChatting}
                    needsReviewIds={pendingReviewIds}
                  />
                </div>
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
                <p>尚未選擇評分表來源，請先上傳文件或與 AI 討論。</p>
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
              isClearing={isClearingMessages}
              hasRubric={Boolean(analysis)}
              onToggleSources={judgeSession?.id ? () => setSourcesOpen((current) => !current) : undefined}
              sourcesOpen={sourcesOpen}
              sourcesContent={judgeSession?.id ? (
                <RubricSourceRail
                  classId={classId}
                  judgeSession={judgeSession}
                  onSessionUpdated={onSessionUpdated}
                  onClose={() => setSourcesOpen(false)}
                  embedded
                />
              ) : null}
              pendingAttachments={pendingAttachments}
              onRemoveAttachment={handleRemoveAttachment}
              onUploadFile={!judgeSession?.id ? undefined : handleAddAttachment}
              isUploading={isUploading}
              onCreateScript={analysis ? handleCreateScript : undefined}
              isCreatingScript={isCreatingScript}
              canCreateScript={Boolean(analysis) && items.length > 0}
              createScriptHint={items.length === 0 ? "請先新增至少一個檢查項目" : undefined}
            />
            {pendingProposal && analysis && <ProposalPanel
              proposal={pendingProposal}
              selectedIds={selectedProposalIds}
              onToggle={(id) => setSelectedProposalIds((current) => {
                const next = new Set(current);
                if (next.has(id)) next.delete(id); else next.add(id);
                return next;
              })}
              onApply={applyPendingProposal}
              onSkip={() => {
               setPendingProposal(null);
               setSelectedProposalIds(new Set());
               setPendingProposalMeta(null);
                setPendingProposalIsRefine(false);
              }}
              isRefine={pendingProposalIsRefine}
              disabled={isChatting || isClearingMessages}
            />}
          </div>
        </div>
      </div>

      {conflictDialog.open && (
        <ConfirmModal
          title="已有同名評分表"
          description={`「${conflictDialog.item.name}」已存在。請選擇覆蓋原本文件，或建立一份副本後重新分析。`}
          closing={conflictDialog.closing}
          onClose={() => {
            if (!isUploading) setPendingConflictFile(null);
          }}
          actions={
            <>
              <button
                type="button"
                className={styles.btnSecondary}
                disabled={isUploading}
                onClick={() => setPendingConflictFile(null)}
              >
                取消
              </button>
              <button
                type="button"
                className={styles.btnSecondary}
                disabled={isUploading}
                onClick={() => handleUpload(conflictDialog.item, "copy")}
              >
                建立副本
              </button>
              <button
                type="button"
                className={styles.btnPrimary}
                disabled={isUploading}
                onClick={() => handleUpload(conflictDialog.item, "overwrite")}
              >
                覆蓋原本
              </button>
            </>
          }
        />
      )}

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
        Agent 已自動修正 {retryCount} 次；仍未通過時，請檢查下列原因，回到評分表調整後重新製作檢查腳本。
      </p>
      {attempts.length > 0 && (
        <ul className={styles.reviewIssues}>
          {attempts.slice(-3).map((attempt, index) => {
            const issues = [
              ...(Array.isArray(attempt?.safety_issues) ? attempt.safety_issues : []),
              ...(Array.isArray(attempt?.quality_issues) ? attempt.quality_issues : []),
              ...(Array.isArray(attempt?.ai_review_issues) ? attempt.ai_review_issues : []),
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
            尚未建立檢查腳本。請先建立或上傳資料文件，完成評分表調整後再製作檢查腳本。
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
    const score = typeof judgement.score === "number" ? judgement.score : null;
    const maxScore = typeof judgement.max_score === "number" ? judgement.max_score : 5;
    return (
      <span className={`${styles.badge} ${styles.badge_success}`}>
        {score === null ? "已分析" : `${score}/${maxScore}`}
      </span>
    );
  }
  if (judgement.status === "failed") {
    return <span className={`${styles.badge} ${styles.badge_danger}`}>AI 分析失敗</span>;
  }
  if (judgement.status === "skipped") {
    return <span className={`${styles.badge} ${styles.badge_muted}`}>略過</span>;
  }
  return <span className={`${styles.badge} ${styles.badge_info}`}>分析中</span>;
}

function aiJudgementSummary(result) {
  if (!result) return null;
  if (result.validation?.valid === false) {
    return result.validation.error ?? "JSON 驗證未通過，未進入 AI 分析。";
  }
  const judgement = result.ai_judgement;
  if (!judgement) return "AI 分析尚未完成。";
  return judgement.error ?? judgement.summary ?? null;
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
                  <th>AI 分析</th>
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
                            <summary>查看心得</summary>
                            {summary && (
                              <p className={summaryIsError ? styles.dangerText : styles.mutedText}>
                                {summary}
                              </p>
                            )}
                            {(result.ai_judgement?.item_judgements ?? []).map((item, index) => (
                              <div key={`${item.item_id ?? "item"}-${index}`} className={styles.judgeItem}>
                                <div className={styles.judgeItemHead}>
                                  <span>{item.title ?? item.item_id ?? "檢查項目"}</span>
                                  {typeof item.score === "number" && (
                                    <span className={`${styles.badge} ${styles.badge_muted}`}>
                                      {item.score}/{item.max_score ?? 1}
                                    </span>
                                  )}
                                </div>
                                {item.comment && <p>{item.comment}</p>}
                              </div>
                            ))}
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
  const [creationView, setCreationView] = useState(null);
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
    setCreationView(null);
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

  function handleCreationChoice(mode) {
    setCreationView(mode);
  }

  function handleCreated(created) {
    if (classIdRef.current !== classId) return;
    setCreationView(null);
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
    toast.success(`已建立「${copy.title}」，可開始調整評分表。`);
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
      <button type="button" className={`${styles.btnPrimary} ${styles.newCheckButton}`} onClick={() => setCreationView("choose")}><MIcon name="add" size={17} />新增檢查</button>
      <div className={styles.sessionList} role="list">
        {loading ? <p className={styles.mutedText}>載入中…</p> : sessions.length === 0 ? <div className={styles.sidebarEmpty}><MIcon name="checklist" size={24} /><p>尚未建立檢查。新增時可從零建立評分表，或上傳資料文件，再與 AI 討論並調整。</p></div> : sessions.map((item) => {
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
                     <button type="button" className={selected ? styles.sessionItemActive : styles.sessionItem} aria-current={selected ? "true" : undefined} onClick={() => { setCreationView(null); setActiveSessionId(item.id); closeSessionMenu(); }}>
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
        <p className={styles.panelDesc}>建立評分表、準備檢查腳本，並查看班級機器的執行結果。</p>
      </div>

      {!creationView && activeSession ? (
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
          {creationView === "choose" ? <CreateCheckChooser onChoose={handleCreationChoice} onCancel={() => setCreationView(null)} /> : creationView ? <CreateCheckForm key={creationView} classId={classId} weeks={weeks} embedded initialMode={creationView} onClose={() => setCreationView("choose")} onCreated={handleCreated} /> : <div className={styles.card}><div className={styles.mainEmpty}><MIcon name="checklist" size={30} /><p>請從左側選擇一項檢查，或新增檢查。</p><button type="button" className={styles.btnPrimary} onClick={() => setCreationView("choose")}>新增檢查</button></div></div>}
        </section>
      </div>
      )}

      {typeof document !== "undefined" && sessionMenuItemKeep.open && sessionMenuPos.item && createPortal(renderSessionMenu(sessionMenuItemKeep.item), document.body)}

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
