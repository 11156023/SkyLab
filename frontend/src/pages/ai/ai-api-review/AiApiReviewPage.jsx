import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./AiApiReviewPage.module.scss";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { AiApiService } from "../../../services/aiApi";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import useDialogPresence from "../../../hooks/useDialogPresence";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { formatDateTime } from "../../../utils/formatDate";

function EmptyState() {
  const { t } = useTranslation("ai");
  return <SharedEmptyState icon="assignment_turned_in" title={t("AiApiReviewPage.emptyTitle")} />;
}

/* ── Review Dialog ── */
function ReviewDialog({ open, onClose, request, action, onDone }) {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // 關閉時先播放離場動畫再卸載
  const presence = useDialogPresence(open);

  if (!presence.open || !request) return null;

  const isApprove = action === "approved";

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await AiApiService.reviewRequest(request.id, {
        status: action,
        review_comment: comment || null,
      });
      toast.success(isApprove ? t("AiApiReviewPage.approveSuccess") : t("AiApiReviewPage.rejectSuccess"));
      setComment("");
      onClose();
      onDone();
    } catch (e) {
      toast.error(e?.message ?? t("AiApiReviewPage.actionError"));
    } finally {
      setSubmitting(false);
    }
  };

  /* 外框（遮罩、Esc、焦點、捲動鎖）交給共用 Modal；送出中 Esc／點遮罩都不關 */
  return (
    <Modal
      closing={presence.closing}
      onClose={onClose}
      busy={submitting}
      size="md"
      title={isApprove ? t("AiApiReviewPage.approveDialogTitle") : t("AiApiReviewPage.rejectDialogTitle")}
      actions={
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={submitting}>
            {t("AiApiReviewPage.cancel")}
          </button>
          <button
            type="button"
            className={isApprove ? styles.btnPrimary : styles.btnDanger}
            onClick={handleSubmit}
            disabled={submitting}
          >
            {submitting ? t("AiApiReviewPage.processing") : isApprove ? t("AiApiReviewPage.confirmApprove") : t("AiApiReviewPage.confirmReject")}
          </button>
        </>
      }
    >
      <div className={styles.dialogBody}>
        <div className={styles.dialogInfo}>
          <div>{t("AiApiReviewPage.dialogApplicant", { value: request.user_full_name || request.user_email })}</div>
          <div>{t("AiApiReviewPage.dialogKeyName", { value: request.api_key_name })}</div>
          <div>{t("AiApiReviewPage.dialogAppliedAt", { value: formatDateTime(request.created_at, t("AiApiReviewPage.notReviewed")) })}</div>
          <div className={styles.dialogPurpose}>{t("AiApiReviewPage.dialogPurpose", { value: request.purpose })}</div>
        </div>
        <textarea
          className={styles.dialogTextarea}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder={t("AiApiReviewPage.commentPlaceholder")}
          maxLength={2000}
          rows={4}
        />
      </div>
    </Modal>
  );
}

/* ── Bulk reject dialog ── */
function BulkRejectDialog({ open, onClose, requestIds, onDone }) {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const reasonId = useId();
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const presence = useDialogPresence(open);

  useEffect(() => {
    if (!open) setComment("");
  }, [open]);

  if (!presence.open || requestIds.length === 0) return null;

  const handleSubmit = async () => {
    const reason = comment.trim();
    if (!reason) return;

    setSubmitting(true);
    try {
      const result = await AiApiService.bulkRejectRequests(requestIds, reason);
      toast.success(t("AiApiReviewPage.bulkRejectSuccess", { count: result?.count ?? requestIds.length }));
      onClose();
      onDone();
    } catch (e) {
      toast.error(e?.message ?? t("AiApiReviewPage.bulkRejectError"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      closing={presence.closing}
      onClose={onClose}
      busy={submitting}
      role="alertdialog"
      size="md"
      title={t("AiApiReviewPage.bulkRejectDialogTitle")}
      description={t("AiApiReviewPage.bulkRejectSummary", { count: requestIds.length })}
      actions={
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={submitting}>
            {t("AiApiReviewPage.cancel")}
          </button>
          <button
            type="button"
            className={styles.btnDanger}
            onClick={handleSubmit}
            disabled={submitting || !comment.trim()}
          >
            {submitting ? t("AiApiReviewPage.processing") : t("AiApiReviewPage.bulkRejectConfirm")}
          </button>
        </>
      }
    >
      <div className={styles.dialogBody}>
        <label className={styles.dialogFieldLabel} htmlFor={reasonId}>
          {t("AiApiReviewPage.bulkRejectReasonLabel")}
        </label>
        <textarea
          id={reasonId}
          className={styles.dialogTextarea}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder={t("AiApiReviewPage.bulkRejectReasonPlaceholder")}
          maxLength={2000}
          rows={5}
          required
          aria-required="true"
        />
      </div>
    </Modal>
  );
}

/* ── ReviewActions in table row ── */
function ReviewActions({ item, onDone }) {
  const { t } = useTranslation("ai");
  const [approveOpen, setApproveOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);

  if (item.status !== "pending") {
    return (
      <span className={styles.reviewComment}>
        {item.review_comment || "—"}
      </span>
    );
  }

  return (
    <>
      <div className={styles.actions}>
        <button
          type="button"
          className={`${styles.actionBtn} ${styles.actionBtnOk}`}
          title={t("AiApiReviewPage.actionApprove")}
          onClick={() => setApproveOpen(true)}
        >
          <MIcon name="check" size={16} />
          {t("AiApiReviewPage.actionApprove")}
        </button>
        <button
          type="button"
          className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
          title={t("AiApiReviewPage.actionReject")}
          onClick={() => setRejectOpen(true)}
        >
          <MIcon name="close" size={16} />
          {t("AiApiReviewPage.actionReject")}
        </button>
      </div>
      <ReviewDialog
        open={approveOpen}
        onClose={() => setApproveOpen(false)}
        request={item}
        action="approved"
        onDone={onDone}
      />
      <ReviewDialog
        open={rejectOpen}
        onClose={() => setRejectOpen(false)}
        request={item}
        action="rejected"
        onDone={onDone}
      />
    </>
  );
}

/* ── Main ── */
const REQUEST_TAB_KEYS = ["pending", "approved", "rejected", "all"];
/* 後端 /ai-api/requests 的 limit 上限 */
const REQUEST_LIST_LIMIT = 100;

export default function AiApiReviewPage() {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("pending");
  const [requests, setRequests] = useState([]);
  const [counts, setCounts] = useState({});
  const [loading, setLoading] = useState(true);
  const [selectedIds, setSelectedIds] = useState(() => new Set());
  const [bulkRejectOpen, setBulkRejectOpen] = useState(false);
  /* 切換分頁時舊請求可能較晚回來，只讓最新一次載入寫入畫面 */
  const loadSeqRef = useRef(0);

  const TABS = [
    { key: "pending",  label: t("AiApiReviewPage.tabPending") },
    { key: "approved", label: t("AiApiReviewPage.tabApproved") },
    { key: "rejected", label: t("AiApiReviewPage.tabRejected") },
    { key: "all",      label: t("AiApiReviewPage.tabAll") },
  ];

  const STATUS_LABELS = {
    pending:  t("AiApiReviewPage.statusPending"),
    approved: t("AiApiReviewPage.statusApproved"),
    rejected: t("AiApiReviewPage.statusRejected"),
  };

  /** silent = true 時不觸發 loading 與錯誤提示，供背景自動刷新使用 */
  const load = useCallback(async (silent = false) => {
    const seq = ++loadSeqRef.current;
    const isCurrent = () => seq === loadSeqRef.current;
    if (!silent) setLoading(true);
    try {
      /* 狀態篩選交給後端：清單上限 100 筆且新到舊，前端自己篩會讓
         比最新 100 筆還舊的待審申請看不到。非目前分頁只取筆數給角標。 */
      const pages = await Promise.all(
        REQUEST_TAB_KEYS.map((key) => AiApiService.listAllRequests({
          status: key === "all" ? undefined : key,
          limit: key === activeTab ? REQUEST_LIST_LIMIT : 1,
        })),
      );
      if (!isCurrent()) return;
      const activePage = pages[REQUEST_TAB_KEYS.indexOf(activeTab)];
      setRequests(activePage?.data ?? []);
      setCounts(Object.fromEntries(REQUEST_TAB_KEYS.map((key, i) => [
        key,
        pages[i]?.count ?? pages[i]?.data?.length ?? 0,
      ])));
    } catch (e) {
      if (!silent && isCurrent()) toast.error(e?.message ?? t("AiApiReviewPage.loadError"));
    } finally {
      /* 由最新一次載入收掉 loading（即使它是靜默刷新），避免卡在載入畫面 */
      if (isCurrent()) setLoading(false);
    }
  }, [activeTab, toast, t]);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(() => load(true));

  const filtered = useMemo(() => {
    if (activeTab === "all") return requests;
    return requests.filter((r) => r.status === activeTab);
  }, [requests, activeTab]);

  const selectableRequests = useMemo(
    () => filtered.filter((request) => request.status === "pending"),
    [filtered],
  );
  const selectableIds = useMemo(
    () => new Set(selectableRequests.map((request) => String(request.id))),
    [selectableRequests],
  );
  const selectedCount = selectedIds.size;
  const allSelected = selectableRequests.length > 0
    && selectableRequests.every((request) => selectedIds.has(String(request.id)));

  /* 自動刷新／分頁切換後，剛被其他人審核的列不能繼續留在選取集合。 */
  useEffect(() => {
    setSelectedIds((current) => {
      const next = new Set([...current].filter((id) => selectableIds.has(id)));
      if (next.size === current.size && [...next].every((id) => current.has(id))) return current;
      return next;
    });
  }, [selectableIds]);

  const handleToggleRequest = useCallback((requestId, checked) => {
    const id = String(requestId);
    setSelectedIds((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const handleToggleAll = useCallback((checked) => {
    setSelectedIds(checked ? new Set(selectableRequests.map((request) => String(request.id))) : new Set());
  }, [selectableRequests]);

  const handleBulkDone = useCallback(() => {
    setSelectedIds(new Set());
    load();
  }, [load]);

  const COLS = [
    t("AiApiReviewPage.colApplicant"),
    t("AiApiReviewPage.colKeyName"),
    t("AiApiReviewPage.colPurpose"),
    t("AiApiReviewPage.colStatus"),
    t("AiApiReviewPage.colAppliedAt"),
    t("AiApiReviewPage.colReviewedAt"),
    t("AiApiReviewPage.colActions"),
  ];

  return (
    <div className={styles.page}>
      <PageHeader title={t("AiApiReviewPage.pageTitle")} />

      <div className={styles.tabsRow}>
        <SegmentedControl
          className={styles.tabsControl}
          options={TABS.map(({ key, label }) => ({
            value: key,
            label,
            badge: counts[key] ?? 0,
          }))}
          value={activeTab}
          onChange={setActiveTab}
          ariaLabel={t("AiApiReviewPage.tabsAriaLabel")}
        />
      </div>

      <div className={styles.content}>
        {loading ? (
          <LoadingState fullPage />
        ) : filtered.length === 0 ? (
          <EmptyState />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {COLS.slice(0, -1).map((col) => (
                    <th key={col} className={styles.th}>{col}</th>
                  ))}
                  <th className={styles.th}>{COLS[COLS.length - 1]}</th>
                  <th className={`${styles.th} ${styles.bulkSelectionHeader}`}>
                    <span>{t("AiApiReviewPage.bulkRejectColumn")}</span>
                    {selectableRequests.length > 0 && (
                      <div className={styles.bulkControls}>
                        <label className={styles.selectAllControl}>
                          <input
                            type="checkbox"
                            checked={allSelected}
                            ref={(element) => {
                              if (element) element.indeterminate = selectedCount > 0 && !allSelected;
                            }}
                            onChange={(event) => handleToggleAll(event.target.checked)}
                            aria-label={t("AiApiReviewPage.selectAll")}
                          />
                          <span>{t("AiApiReviewPage.selectAll")}</span>
                        </label>
                        {selectedCount > 0 && (
                          <button
                            type="button"
                            className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                            onClick={() => setBulkRejectOpen(true)}
                          >
                            <MIcon name="close" size={16} />
                            {t("AiApiReviewPage.bulkReject", { count: selectedCount })}
                          </button>
                        )}
                      </div>
                    )}
                  </th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr
                    key={r.id}
                    className={`${styles.tr} ${selectedIds.has(String(r.id)) ? styles.trSelected : ""}`}
                  >
                    <td className={styles.td}>
                      <div className={styles.userCell}>
                        <span className={styles.userName}>{r.user_full_name || r.user_email}</span>
                        {r.user_full_name && r.user_email && (
                          <span className={styles.userEmail}>{r.user_email}</span>
                        )}
                      </div>
                    </td>
                    <td className={styles.td}>{r.api_key_name}</td>
                    <td className={styles.td}>
                      <span className={styles.purposeCell} title={r.purpose}>
                        {(r.purpose ?? "").length > 60 ? `${r.purpose.slice(0, 60)}…` : r.purpose}
                      </span>
                    </td>
                    <td className={styles.td}>
                      <span className={`${styles.badge} ${styles[`badge_${r.status}`]}`}>
                        <span className={styles.dot} />
                        {STATUS_LABELS[r.status] ?? r.status}
                      </span>
                    </td>
                    <td className={styles.td}>{formatDateTime(r.created_at, t("AiApiReviewPage.notReviewed"))}</td>
                    <td className={styles.td}>{formatDateTime(r.reviewed_at, t("AiApiReviewPage.notReviewed"))}</td>
                    <td className={styles.td}>
                      <ReviewActions item={r} onDone={() => load()} />
                    </td>
                    <td className={`${styles.td} ${styles.bulkSelectionCell}`}>
                      {r.status === "pending" && (
                        <input
                          className={styles.selectCheckbox}
                          type="checkbox"
                          checked={selectedIds.has(String(r.id))}
                          onChange={(event) => handleToggleRequest(r.id, event.target.checked)}
                          aria-label={t("AiApiReviewPage.selectRequest", {
                            value: r.user_full_name || r.user_email || r.api_key_name,
                          })}
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <BulkRejectDialog
        open={bulkRejectOpen}
        onClose={() => setBulkRejectOpen(false)}
        requestIds={[...selectedIds]}
        onDone={handleBulkDone}
      />
    </div>
  );
}
