import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./AiJudgePanel.module.scss";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import { downloadBlob } from "../../../services/api";
import {
  AiJudgeService,
  TEMPLATE_OPTIONS,
  getTemplateLabel,
  rubricToContext,
} from "../../../services/aiJudge";

/* ── 共用小元件 ─────────────────────────────────────────── */

function Spinner({ size = 16 }) {
  return (
    <span className={styles.spinning}>
      <MIcon name="autorenew" size={size} />
    </span>
  );
}

/** 偵測方式標籤：auto=綠、partial=藍、manual=紅（不使用黃色警示色） */
const DETECTABLE_INFO = {
  auto: { label: "可自動偵測", className: styles.detBadge_auto },
  partial: { label: "部分可偵測", className: styles.detBadge_partial },
  manual: { label: "需人工評閱", className: styles.detBadge_manual },
};

function getDetectableInfo(detectable) {
  return DETECTABLE_INFO[detectable] ?? DETECTABLE_INFO.manual;
}

function formatDateTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-TW");
}

/* ── 評分表統計 ─────────────────────────────────────────── */

function RubricStats({ items }) {
  const total = items.length;
  const autoCount = items.filter((item) => item.detectable === "auto").length;
  const partialCount = items.filter((item) => item.detectable === "partial").length;
  const manualCount = items.filter((item) => item.detectable === "manual").length;
  const pct = (count) => (total > 0 ? Math.round((count / total) * 100) : 0);

  return (
    <div className={styles.statsGrid}>
      <div className={styles.statBox}>
        <p className={styles.statValue}>{total}</p>
        <p className={styles.statLabel}>共幾題</p>
      </div>
      <div className={`${styles.statBox} ${styles.statBox_success}`}>
        <p className={styles.statValue}>
          <MIcon name="check_circle" size={16} />
          {autoCount}
        </p>
        <p className={styles.statLabel}>可自動偵測（{pct(autoCount)}%）</p>
      </div>
      <div className={`${styles.statBox} ${styles.statBox_info}`}>
        <p className={styles.statValue}>
          <MIcon name="info" size={16} />
          {partialCount}
        </p>
        <p className={styles.statLabel}>部分可偵測（{pct(partialCount)}%）</p>
      </div>
      <div className={`${styles.statBox} ${styles.statBox_danger}`}>
        <p className={styles.statValue}>
          <MIcon name="schedule" size={16} />
          {manualCount}
        </p>
        <p className={styles.statLabel}>需人工評閱（{pct(manualCount)}%）</p>
      </div>
    </div>
  );
}

/* ── 單一評分項目卡片 ───────────────────────────────────── */

function RubricCard({ item, index, onChange, onDelete, disabled }) {
  const detectableInfo = getDetectableInfo(item.detectable);
  const checkSteps = item.check_steps ?? [];
  const cardVariant =
    item.detectable === "auto"
      ? styles.rubricCard_auto
      : item.detectable === "partial"
        ? styles.rubricCard_partial
        : styles.rubricCard_manual;

  return (
    <div className={`${styles.rubricCard} ${cardVariant}`}>
      <div className={styles.rubricCardHead}>
        <div className={styles.rubricCardHeadMain}>
          <span className={styles.rubricIndex}>#{index + 1}</span>
          <span className={`${styles.detBadge} ${detectableInfo.className}`}>
            {detectableInfo.label}
          </span>
        </div>
        <button
          type="button"
          className={`${styles.iconBtn} ${styles.iconBtnDanger}`}
          title="刪除項目"
          onClick={onDelete}
          disabled={disabled}
        >
          <MIcon name="delete" size={16} />
        </button>
      </div>

      <label className={styles.rubricField}>
        <span>主題</span>
        <input
          value={item.title}
          onChange={(e) => onChange({ ...item, title: e.target.value })}
          placeholder="評分項目名稱"
          disabled={disabled}
        />
      </label>

      <label className={styles.rubricField}>
        <span>說明</span>
        <input
          value={item.description}
          onChange={(e) => onChange({ ...item, description: e.target.value })}
          placeholder="評分說明"
          disabled={disabled}
        />
      </label>

      {(item.detection_method || item.fallback || checkSteps.length > 0) && (
        <div className={styles.detectInfo}>
          <div className={styles.detectInfoHead}>
            <MIcon name="security" size={14} />
            AI 偵測判斷（僅由 AI 更新）
          </div>
          <div className={styles.detectGrid}>
            {item.detection_method && (
              <div className={styles.detectItem}>
                <span>偵測方式</span>
                <p>{item.detection_method}</p>
              </div>
            )}
            {item.fallback && (
              <div className={styles.detectItem}>
                <span>替代建議</span>
                <p>{item.fallback}</p>
              </div>
            )}
          </div>
          {checkSteps.length > 0 && (
            <div className={styles.detectItem}>
              <span>評分計劃書（未執行）</span>
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
  );
}

/* ── 上傳區 ─────────────────────────────────────────────── */

function RubricUploader({ onUpload, isLoading }) {
  const [isDragging, setIsDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);

  function handleDrop(e) {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (!file) return;
    const ext = file.name.split(".").pop()?.toLowerCase();
    if (ext === "docx" || ext === "pdf") setSelectedFile(file);
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
          accept=".docx,.pdf"
          className={styles.dropZoneInput}
          disabled={isLoading}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) setSelectedFile(file);
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
            <p className={styles.dropHintTitle}>拖放情境評估表文件到這裡</p>
            <p className={styles.dropHintMeta}>或點擊選擇檔案（支援 .docx、.pdf）</p>
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

function ChatPanel({ messages, onSendMessage, isLoading }) {
  const [input, setInput] = useState("");
  const messagesEndRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  function send() {
    const content = input.trim();
    if (!content || isLoading) return;
    onSendMessage(content);
    setInput("");
  }

  return (
    <div className={styles.chatPanel}>
      <div className={styles.chatMessages}>
        {messages.length === 0 ? (
          <div className={styles.chatEmpty}>
            <MIcon name="smart_toy" size={32} />
            <p>與 AI 對話來精煉你的情境評估表</p>
            <p className={styles.chatEmptyMeta}>可以詢問修改建議，或直接下達調整指令</p>
          </div>
        ) : (
          messages.map((msg, i) => (
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
        <button
          type="button"
          className={styles.btnSecondary}
          disabled={isLoading}
          onClick={() => onSendMessage("請幫我審核並潤飾目前的情境評估表", true)}
        >
          <MIcon name="auto_fix_high" size={14} />
          全表潤飾
        </button>
        <form
          className={styles.chatForm}
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
            placeholder="輸入訊息...（Shift+Enter 換行）"
            rows={1}
            disabled={isLoading}
          />
          <button
            type="submit"
            className={styles.btnPrimary}
            disabled={isLoading || !input.trim()}
            aria-label="送出"
          >
            <MIcon name="send" size={16} />
          </button>
        </form>
        <p className={styles.chatHint}>
          提示：詢問問題不會修改評估表，需明確指令（如「幫我改」「新增」）才會執行變更
        </p>
      </div>
    </div>
  );
}

/* ── 確認 Modal（覆蓋/副本、刪除） ──────────────────────── */

function ConfirmModal({ title, description, actions, onClose }) {
  return (
    <div className={styles.modalOverlay} onMouseDown={onClose}>
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

/* ── Tab 1：評分表 ──────────────────────────────────────── */

function RubricsTab({ groupId, onScriptCreated }) {
  const toast = useToast();

  const [files, setFiles] = useState([]);
  const [filesLoading, setFilesLoading] = useState(true);
  const [filesError, setFilesError] = useState(false);

  const [analysis, setAnalysis] = useState(null);
  const [messages, setMessages] = useState([]);
  const [isUploading, setIsUploading] = useState(false);
  const [isChatting, setIsChatting] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [isCreatingScript, setIsCreatingScript] = useState(false);
  const [uploadedFileName, setUploadedFileName] = useState("rubric");
  const [sourceFileId, setSourceFileId] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [pendingConflictFile, setPendingConflictFile] = useState(null);
  const [selectedTemplateKey, setSelectedTemplateKey] = useState("linux");
  const [analysisTemplateKey, setAnalysisTemplateKey] = useState("linux");

  /** silent = true 時不觸發 loading / error state，供背景自動刷新使用 */
  const fetchFiles = useCallback(async (silent = false) => {
    if (!silent) {
      setFilesLoading(true);
      setFilesError(false);
    }
    try {
      setFiles(await AiJudgeService.listFiles(groupId));
    } catch {
      if (!silent) setFilesError(true);
    } finally {
      if (!silent) setFilesLoading(false);
    }
  }, [groupId]);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);
  useAutoRefresh(() => fetchFiles(true));

  /** 重算統計欄位後套用新的項目清單 */
  function applyItems(base, nextItems) {
    return {
      ...base,
      items: nextItems,
      total_items: nextItems.length,
      checked_count: nextItems.filter((item) => item.checked).length,
    };
  }

  /** 更新分析結果；persist 時同步寫回已保存的評分表 */
  async function applyAnalysis(nextAnalysis, { persist = false } = {}) {
    setAnalysis(nextAnalysis);
    if (persist && sourceFileId) {
      try {
        const file = await AiJudgeService.updateFileAnalysis(groupId, sourceFileId, nextAnalysis);
        setFiles((current) => current.map((item) => (item.id === file.id ? file : item)));
      } catch (err) {
        toast.error(err?.message ?? "更新評分表失敗");
      }
    }
  }

  async function handleUpload(file, conflictStrategy) {
    setIsUploading(true);
    try {
      const response = await AiJudgeService.uploadFile(
        groupId,
        file,
        selectedTemplateKey,
        conflictStrategy,
      );
      const uploadedFile = {
        ...response.file,
        analysis_json: response.file.analysis_json ?? response.analysis,
      };
      setAnalysis(response.analysis);
      setUploadedFileName(file.name || "rubric");
      setSourceFileId(uploadedFile.id);
      setAnalysisTemplateKey(response.template_key ?? selectedTemplateKey);
      setMessages([]);
      setPendingConflictFile(null);
      setFiles((current) => [
        uploadedFile,
        ...current.filter((item) => item.id !== uploadedFile.id),
      ]);
      toast.success(`分析完成：${response.analysis.items.length} 題評估項目`);
      fetchFiles();
    } catch (err) {
      if (err?.status === 409) {
        setPendingConflictFile(file);
      } else {
        toast.error(err?.message ?? "上傳失敗");
      }
    } finally {
      setIsUploading(false);
    }
  }

  function handleSelectFile(file) {
    if (!file.analysis_json) {
      toast.error("這份評分表尚未有可載入的分析結果");
      return;
    }
    setAnalysis(file.analysis_json);
    setUploadedFileName(file.original_filename || "rubric");
    setSourceFileId(file.id);
    setAnalysisTemplateKey(file.template_key);
    setSelectedTemplateKey(file.template_key);
    setMessages([]);
    toast.success(`已載入「${file.original_filename}」`);
  }

  async function handleDownloadFile(file) {
    try {
      const blob = await AiJudgeService.downloadFile(groupId, file.id);
      downloadBlob(blob, file.original_filename);
    } catch (err) {
      toast.error(err?.message ?? "下載評分表失敗");
    }
  }

  async function handleDeleteFile() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await AiJudgeService.deleteFile(groupId, deleteTarget.id);
      toast.success("評分表已刪除");
      setFiles((current) => current.filter((file) => file.id !== deleteTarget.id));
      if (sourceFileId === deleteTarget.id) setSourceFileId(null);
      setDeleteTarget(null);
    } catch (err) {
      toast.error(err?.message ?? "刪除評分表失敗");
    } finally {
      setDeleting(false);
    }
  }

  async function handleSendMessage(content, isRefine = false) {
    if (!analysis) return;
    const newMessages = [...messages, { role: "user", content }];
    setMessages(newMessages);
    setIsChatting(true);
    try {
      const response = await AiJudgeService.chat({
        messages: newMessages,
        rubricContext: rubricToContext(analysis),
        isRefine,
        templateKey: analysisTemplateKey,
      });
      setMessages((prev) => [...prev, { role: "assistant", content: response.reply }]);
      if (response.updated_items) {
        await applyAnalysis(applyItems(analysis, response.updated_items), { persist: true });
        toast.success("評估表已更新");
      }
    } catch (err) {
      toast.error(err?.message ?? "對話失敗");
      setMessages(messages);
    } finally {
      setIsChatting(false);
    }
  }

  function handleItemChange(index, updatedItem) {
    const nextItems = [...analysis.items];
    nextItems[index] = updatedItem;
    applyAnalysis(applyItems(analysis, nextItems), { persist: true });
  }

  function handleItemDelete(index) {
    const nextItems = analysis.items.filter((_, i) => i !== index);
    applyAnalysis(applyItems(analysis, nextItems), { persist: true });
  }

  function handleAddItem() {
    const newItem = {
      id: `item-${Date.now()}`,
      title: "新評估項目",
      description: "",
      checked: false,
      detectable: "manual",
      detection_method: null,
      fallback: null,
      check_steps: [],
    };
    applyAnalysis(applyItems(analysis, [...analysis.items, newItem]), { persist: true });
  }

  async function handleExport() {
    setIsExporting(true);
    try {
      const blob = await AiJudgeService.downloadExcel(analysis.items, analysis.summary);
      downloadBlob(blob, "rubric.xlsx");
      toast.success("Excel 下載成功");
    } catch (err) {
      toast.error(err?.message ?? "匯出失敗");
    } finally {
      setIsExporting(false);
    }
  }

  async function handleCreateScript() {
    setIsCreatingScript(true);
    try {
      const artifact = await AiJudgeService.createScript(groupId, {
        name: uploadedFileName,
        templateKey: analysisTemplateKey,
        rubricSnapshot: analysis,
        sourceFileId,
      });
      toast.success(
        artifact.status === "reviewed"
          ? "收集腳本已產生並通過審查"
          : "收集腳本已產生，請查看審查結果",
      );
      onScriptCreated?.();
    } catch (err) {
      toast.error(err?.message ?? "製作收集腳本失敗");
    } finally {
      setIsCreatingScript(false);
    }
  }

  const items = analysis?.items ?? [];

  return (
    <div className={styles.tabBody}>
      <div className={styles.sectionHead}>
        <div>
          <h3 className={styles.sectionTitle}>評分表</h3>
          <p className={styles.sectionDesc}>上傳評估表，查看 AI 偵測判斷並調整評估項目</p>
        </div>
        {analysis && (
          <div className={styles.sectionActions}>
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={handleCreateScript}
              disabled={isCreatingScript || isChatting}
            >
              {isCreatingScript ? <Spinner /> : <MIcon name="auto_fix_high" size={16} />}
              {isCreatingScript ? "製作中..." : "製作收集腳本"}
            </button>
            <button
              type="button"
              className={styles.btnSecondary}
              onClick={handleExport}
              disabled={isExporting}
            >
              {isExporting ? <Spinner /> : <MIcon name="download" size={16} />}
              {isExporting ? "匯出中..." : "匯出 Excel"}
            </button>
          </div>
        )}
      </div>

      {isCreatingScript && (
        <div className={styles.noticeInfo}>
          <p>
            <strong>正在生成受管收集腳本</strong>
          </p>
          <p>
            AI 正在依目前評分項目與環境命令產生 Python 收集腳本，系統會接著執行 hard policy 與 AI
            reviewer 審查。
          </p>
        </div>
      )}

      {/* 已保存評分表 */}
      <div className={styles.card}>
        <div className={styles.cardHead}>
          <h4 className={styles.cardTitle}>
            <MIcon name="description" size={18} />
            已保存評分表
          </h4>
        </div>
        {filesLoading ? (
          <p className={styles.mutedText}>載入評分表中...</p>
        ) : filesError ? (
          <p className={styles.dangerText}>載入評分表失敗，請稍後再試。</p>
        ) : files.length === 0 ? (
          <p className={styles.mutedText}>
            尚未保存評分表。上傳文件後會自動保存原始檔與分析結果。
          </p>
        ) : (
          <div className={styles.fileList}>
            {files.map((file) => (
              <div
                key={file.id}
                className={`${styles.fileRow} ${sourceFileId === file.id ? styles.fileRowActive : ""}`}
              >
                <button type="button" className={styles.fileMain} onClick={() => handleSelectFile(file)}>
                  <span className={styles.fileName}>{file.original_filename}</span>
                  <span className={styles.fileMeta}>
                    {getTemplateLabel(file.template_key)} · {formatDateTime(file.updated_at)}
                    {file.status === "replaced" ? " · 已取代" : ""}
                  </span>
                </button>
                <div className={styles.fileActions}>
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    onClick={() => handleDownloadFile(file)}
                  >
                    <MIcon name="download" size={14} />
                    原檔
                  </button>
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    onClick={() => setDeleteTarget(file)}
                    disabled={deleting}
                  >
                    <MIcon name="delete" size={14} />
                    刪除
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {!analysis ? (
        <div className={styles.card}>
          <h4 className={styles.cardTitle}>
            <MIcon name="upload" size={18} />
            上傳情境評估表
          </h4>
          <div className={styles.templateRow}>
            <span className={styles.fieldLabel}>評分環境</span>
            <div className={styles.chipBtns}>
              {TEMPLATE_OPTIONS.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  className={
                    selectedTemplateKey === option.key ? styles.chipBtnActive : styles.chipBtn
                  }
                  onClick={() => setSelectedTemplateKey(option.key)}
                  disabled={isUploading}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          <RubricUploader onUpload={handleUpload} isLoading={isUploading} />
        </div>
      ) : (
        <div className={styles.analysisGrid}>
          <div className={styles.analysisMain}>
            <div className={styles.card}>
              <RubricStats items={items} />
              <p className={styles.mutedText}>
                本次評分環境：{getTemplateLabel(analysisTemplateKey)}
              </p>
              {analysis.summary && <p className={styles.summaryBox}>{analysis.summary}</p>}
            </div>

            <div className={styles.card}>
              <div className={styles.cardHead}>
                <h4 className={styles.cardTitle}>評估項目（{items.length}）</h4>
                <button type="button" className={styles.btnSecondary} onClick={handleAddItem}>
                  <MIcon name="add" size={16} />
                  新增項目
                </button>
              </div>
              <div className={styles.itemsList}>
                {items.map((item, index) => (
                  <RubricCard
                    key={item.id}
                    item={item}
                    index={index}
                    onChange={(updated) => handleItemChange(index, updated)}
                    onDelete={() => handleItemDelete(index)}
                    disabled={isChatting}
                  />
                ))}
              </div>
            </div>
          </div>

          <div className={`${styles.card} ${styles.chatCard}`}>
            <h4 className={styles.cardTitle}>
              <MIcon name="smart_toy" size={18} />
              AI 對話助手
            </h4>
            <ChatPanel messages={messages} onSendMessage={handleSendMessage} isLoading={isChatting} />
          </div>
        </div>
      )}

      {pendingConflictFile && (
        <ConfirmModal
          title="已有同名評分表"
          description={`「${pendingConflictFile.name}」已存在。請選擇覆蓋原本文件，或建立一份副本後重新分析。`}
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
                onClick={() => handleUpload(pendingConflictFile, "copy")}
              >
                建立副本
              </button>
              <button
                type="button"
                className={styles.btnPrimary}
                disabled={isUploading}
                onClick={() => handleUpload(pendingConflictFile, "overwrite")}
              >
                覆蓋原本
              </button>
            </>
          }
        />
      )}

      {deleteTarget && (
        <ConfirmModal
          title="確認刪除評分表？"
          description={`你即將刪除「${deleteTarget.original_filename}」的原始檔與保存分析。刪除後不會影響已建立的腳本。`}
          onClose={() => {
            if (!deleting) setDeleteTarget(null);
          }}
          actions={
            <>
              <button
                type="button"
                className={styles.btnSecondary}
                disabled={deleting}
                onClick={() => setDeleteTarget(null)}
              >
                取消
              </button>
              <button
                type="button"
                className={styles.btnDanger}
                disabled={deleting}
                onClick={handleDeleteFile}
              >
                {deleting ? "刪除中..." : "確認刪除"}
              </button>
            </>
          }
        />
      )}
    </div>
  );
}

/* ── Tab 2：收集腳本 ────────────────────────────────────── */

const SCRIPT_STATUS_LABELS = {
  draft: "草稿",
  review_failed: "審查未通過",
  reviewed: "待老師核准",
  approved: "已核准",
  archived: "已停用",
};

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

function ScriptsTab({ groupId, onScriptApproved }) {
  const toast = useToast();
  const [scripts, setScripts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [actionPending, setActionPending] = useState(null); // "approve" | "regenerate" | "delete"

  const fetchScripts = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      setScripts(await AiJudgeService.listScripts(groupId));
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [groupId]);

  useEffect(() => {
    fetchScripts();
  }, [fetchScripts]);

  const selected = useMemo(() => {
    if (scripts.length === 0) return null;
    return scripts.find((script) => script.id === selectedId) ?? scripts[0];
  }, [scripts, selectedId]);

  async function handleApprove() {
    setActionPending("approve");
    try {
      await AiJudgeService.approveScript(groupId, selected.id);
      toast.success("收集腳本已核准");
      fetchScripts();
      onScriptApproved?.();
    } catch (err) {
      toast.error(err?.message ?? "核准失敗");
    } finally {
      setActionPending(null);
    }
  }

  async function handleRegenerate() {
    setActionPending("regenerate");
    try {
      const script = await AiJudgeService.regenerateScript(groupId, selected.id);
      setSelectedId(script.id);
      toast.success("收集腳本已重新生成");
      fetchScripts();
    } catch (err) {
      toast.error(err?.message ?? "重新生成失敗");
    } finally {
      setActionPending(null);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setActionPending("delete");
    try {
      await AiJudgeService.deleteScript(groupId, deleteTarget.id);
      toast.success("收集腳本已刪除");
      setSelectedId(null);
      setDeleteTarget(null);
      setScripts((current) => current.filter((script) => script.id !== deleteTarget.id));
    } catch (err) {
      toast.error(err?.message ?? "刪除失敗");
    } finally {
      setActionPending(null);
    }
  }

  return (
    <div className={styles.tabBody}>
      <div className={styles.sectionHead}>
        <div>
          <h3 className={styles.sectionTitle}>收集腳本</h3>
          <p className={styles.sectionDesc}>管理群組內由評分表產生的受管 Python 收集腳本。</p>
        </div>
      </div>

      {loading ? (
        <p className={styles.mutedText}>載入腳本中...</p>
      ) : error ? (
        <div className={styles.card}>
          <div className={styles.cardHead}>
            <span className={styles.dangerText}>載入收集腳本失敗，請稍後再試。</span>
            <button type="button" className={styles.btnSecondary} onClick={fetchScripts}>
              重新載入
            </button>
          </div>
        </div>
      ) : scripts.length === 0 ? (
        <div className={styles.card}>
          <p className={styles.mutedText}>
            尚未建立收集腳本。請先到「評分表」上傳評分表並製作腳本。
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
                onClick={() => setSelectedId(script.id)}
              >
                <span className={styles.scriptItemHead}>
                  <span className={styles.scriptName}>{script.name}</span>
                  <span className={`${styles.badge} ${scriptStatusBadgeClass(script.status)}`}>
                    {SCRIPT_STATUS_LABELS[script.status] ?? script.status}
                  </span>
                </span>
                <span className={styles.fileMeta}>
                  v{script.version} · {script.template_key} · {formatDateTime(script.updated_at)}
                </span>
              </button>
            ))}
          </div>

          {selected && (
            <div className={styles.card}>
              <div className={styles.cardHead}>
                <h4 className={styles.cardTitle}>
                  <MIcon name="security" size={18} />
                  {selected.name} v{selected.version}
                </h4>
                <div className={styles.sectionActions}>
                  <button
                    type="button"
                    className={styles.btnPrimary}
                    onClick={handleApprove}
                    disabled={selected.status !== "reviewed" || actionPending !== null}
                  >
                    <MIcon name="check_circle" size={16} />
                    {actionPending === "approve" ? "核准中..." : "核准"}
                  </button>
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    onClick={handleRegenerate}
                    disabled={selected.status === "archived" || actionPending !== null}
                  >
                    {actionPending === "regenerate" ? <Spinner /> : <MIcon name="refresh" size={16} />}
                    {actionPending === "regenerate" ? "生成中..." : "重新生成"}
                  </button>
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    onClick={() => setDeleteTarget(selected)}
                    disabled={actionPending !== null}
                  >
                    <MIcon name="delete" size={16} />
                    刪除腳本
                  </button>
                </div>
              </div>

              <div className={styles.reviewGrid}>
                <ReviewPanel title="Hard policy（靜態）" result={selected.policy_check_result_json} />
                <ReviewPanel title="AI reviewer" result={selected.ai_review_result_json} />
              </div>

              <pre className={styles.codeBlock}>{selected.script_content}</pre>
            </div>
          )}
        </div>
      )}

      {deleteTarget && (
        <ConfirmModal
          title="確認刪除收集腳本？"
          description={`你即將永久刪除「${deleteTarget.name}」v${deleteTarget.version}。刪除後無法再查看、核准或重新生成。`}
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

/* ── Tab 3：腳本執行 ────────────────────────────────────── */

const REASON_LABELS = {
  success: "成功",
  not_running: "未運行",
  missing_ip: "缺少 IP",
  missing_ssh_key: "缺少 SSH 金鑰",
  owner_mismatch: "資源擁有者不一致",
  missing_db_resource: "資料庫無資源",
  invalid_resource_type: "類型不可執行",
  python_missing: "缺少 python3",
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

function ExecutionTab({ groupId, members }) {
  const toast = useToast();
  const [selectedVmids, setSelectedVmids] = useState([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [selectedScriptId, setSelectedScriptId] = useState(null);
  const [creatingRun, setCreatingRun] = useState(false);
  const [activeRunRef, setActiveRunRef] = useState(null); // { scriptId, runId }
  const [activeRun, setActiveRun] = useState(null);
  const [scripts, setScripts] = useState([]);

  useEffect(() => {
    AiJudgeService.listScripts(groupId)
      .then(setScripts)
      .catch(() => {});
  }, [groupId]);

  /* 執行任務輪詢：每 2 秒直到終態；失敗放慢到 5 秒重試 */
  useEffect(() => {
    if (!activeRunRef) return undefined;
    let cancelled = false;
    let timer = null;

    async function poll() {
      try {
        const run = await AiJudgeService.getScriptRun(
          groupId,
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
  }, [groupId, activeRunRef]);

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
      const run = await AiJudgeService.createScriptRun(groupId, effectiveScriptId, selectedVmids);
      toast.success(
        `已建立腳本執行任務（${run.progress_json?.total ?? selectedVmids.length} 台）`,
      );
      setActiveRun(run);
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
      <div className={styles.sectionHead}>
        <div>
          <h3 className={styles.sectionTitle}>腳本執行</h3>
          <p className={styles.sectionDesc}>
            選擇群組內運行中的 VM/LXC，套用已核准的 AI 收集腳本。
          </p>
        </div>
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
        </div>
      </div>

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.checkCol} />
              <th>機器名稱</th>
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
                  <td className={styles.typeCell}>{member.vm_type ?? "-"}</td>
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
                  <th>VMID</th>
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
                          {resourceType ?? "-"}
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
                                  <span>{item.title ?? item.item_id ?? "評分項目"}</span>
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

      {dialogOpen && (
        <div className={styles.modalOverlay} onMouseDown={() => setDialogOpen(false)}>
          <div className={styles.modal} onMouseDown={(e) => e.stopPropagation()}>
            <div className={styles.modalHeader}>
              <div>
                <h2>確認執行腳本</h2>
                <p>後端會在送出時再次確認這些 VM/LXC 仍屬於此群組且正在運行。</p>
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
                    {script.name} v{script.version}
                  </option>
                ))}
              </select>
              {approvedScripts.length === 0 && (
                <span className={styles.fileMeta}>
                  目前沒有已核准的收集腳本，請先到收集腳本分頁核准。
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
                即將使用：{effectiveScript.name} v{effectiveScript.version}（
                {effectiveScript.template_key}）
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

/* ── 主面板 ─────────────────────────────────────────────── */

const JUDGE_TABS = [
  { key: "rubrics", label: "評分表", icon: "description" },
  { key: "scripts", label: "收集腳本", icon: "terminal" },
  { key: "execution", label: "腳本執行", icon: "play_circle_outline" },
];

export default function AiJudgePanel({ groupId, members }) {
  const [activeTab, setActiveTab] = useState("rubrics");

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeading}>
        <h2 className={styles.panelTitle}>
          <MIcon name="checklist" size={20} />
          AI 評分管理
        </h2>
        <p className={styles.panelDesc}>管理群組評分表、收集腳本與腳本執行。</p>
      </div>

      <div className={styles.subTabs}>
        {JUDGE_TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={activeTab === tab.key ? styles.subTabActive : styles.subTab}
            onClick={() => setActiveTab(tab.key)}
          >
            <MIcon name={tab.icon} size={16} />
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "rubrics" && (
        <RubricsTab groupId={groupId} onScriptCreated={() => setActiveTab("scripts")} />
      )}
      {activeTab === "scripts" && (
        <ScriptsTab groupId={groupId} onScriptApproved={() => setActiveTab("execution")} />
      )}
      {activeTab === "execution" && <ExecutionTab groupId={groupId} members={members} />}
    </div>
  );
}
