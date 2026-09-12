import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import styles from "./AiApiReviewPage.module.scss";
import MIcon from "../../../components/MIcon";
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

  // Portal 到 body：此 Dialog 由表格列觸發，若直接掛在 .tableWrap（backdrop-filter）
  // 底下，position: fixed 會以卡片為 containing block，遮罩蓋不滿整個視窗
  return createPortal(
    <div
      className={`${styles.dialogOverlay} ${presence.closing ? styles.dialogOverlayOut : ""}`}
      onClick={onClose}
    >
      <div className={styles.dialog} onClick={(e) => e.stopPropagation()}>
        <div className={styles.dialogHeader}>
          <h3 className={styles.dialogTitle}>
            {isApprove ? t("AiApiReviewPage.approveDialogTitle") : t("AiApiReviewPage.rejectDialogTitle")}
          </h3>
          <p className={styles.dialogDesc}>
            {isApprove
              ? t("AiApiReviewPage.approveDialogDesc")
              : t("AiApiReviewPage.rejectDialogDesc")}
          </p>
        </div>

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
            rows={4}
          />
        </div>

        <div className={styles.dialogFooter}>
          <button type="button" className={styles.btnOutline} onClick={onClose} disabled={submitting}>
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
        </div>
      </div>
    </div>,
    document.body,
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
export default function AiApiReviewPage() {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("pending");
  const [allRequests, setAllRequests] = useState([]);
  const [loading, setLoading] = useState(true);

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
    if (!silent) setLoading(true);
    try {
      const res = await AiApiService.listAllRequests();
      setAllRequests(res?.data ?? []);
    } catch (e) {
      if (!silent) toast.error(e?.message ?? t("AiApiReviewPage.loadError"));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [toast, t]);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(() => load(true));

  const filtered = useMemo(() => {
    if (activeTab === "all") return allRequests;
    return allRequests.filter((r) => r.status === activeTab);
  }, [allRequests, activeTab]);

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
      <PageHeader title={t("AiApiReviewPage.pageTitle")} subtitle={t("AiApiReviewPage.pageSubtitle")} />

      <div className={styles.tabsRow}>
        <SegmentedControl
          className={styles.tabsControl}
          options={TABS.map(({ key, label }) => ({ value: key, label }))}
          value={activeTab}
          onChange={setActiveTab}
          ariaLabel={t("AiApiReviewPage.tabsAriaLabel")}
        />
      </div>

      <div className={styles.content}>
        {loading ? (
          <LoadingState fullPage />
        ) : filtered.length === 0 ? (
          <EmptyState tab={activeTab} />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {COLS.map((col) => (
                    <th key={col} className={styles.th}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <tr key={r.id} className={styles.tr}>
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
                      <ReviewActions item={r} onDone={load} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
