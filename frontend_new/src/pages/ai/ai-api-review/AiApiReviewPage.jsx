import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import styles from "./AiApiReviewPage.module.scss";
import MIcon from "../../../components/MIcon";
import { AiApiService } from "../../../services/aiApi";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";

const TABS = [
  { key: "pending",  label: "待審核" },
  { key: "approved", label: "已通過" },
  { key: "rejected", label: "已拒絕" },
  { key: "all",      label: "全部"   },
];

const STATUS_LABELS = {
  pending:  "待審核",
  approved: "已通過",
  rejected: "已拒絕",
};

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString("zh-TW") : "尚未審核";
}

function EmptyState({ tab }) {
  const text = {
    pending:  "目前沒有符合條件的 AI API 申請",
    approved: "目前沒有已通過的 AI API 申請",
    rejected: "目前沒有已拒絕的 AI API 申請",
    all:      "目前沒有任何 AI API 申請紀錄",
  };
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="assignment_turned_in" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>尚無資料</h2>
      <p className={styles.emptyDesc}>{text[tab]}</p>
    </div>
  );
}

/* ── Review Dialog ── */
function ReviewDialog({ open, onClose, request, action, onDone }) {
  const toast = useToast();
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!open || !request) return null;

  const isApprove = action === "approved";

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await AiApiService.reviewRequest(request.id, {
        status: action,
        review_comment: comment || null,
      });
      toast.success(isApprove ? "AI API 申請已通過" : "AI API 申請已拒絕");
      setComment("");
      onClose();
      onDone();
    } catch (e) {
      toast.error(e?.message ?? "操作失敗");
    } finally {
      setSubmitting(false);
    }
  };

  // Portal 到 body：此 Dialog 由表格列觸發，若直接掛在 .tableWrap（backdrop-filter）
  // 底下，position: fixed 會以卡片為 containing block，遮罩蓋不滿整個視窗
  return createPortal(
    <div className={styles.dialogOverlay} onClick={onClose}>
      <div className={styles.dialog} onClick={(e) => e.stopPropagation()}>
        <div className={styles.dialogHeader}>
          <h3 className={styles.dialogTitle}>
            {isApprove ? "通過 AI API 申請" : "拒絕 AI API 申請"}
          </h3>
          <p className={styles.dialogDesc}>
            {isApprove
              ? "通過後，系統會直接核發可用的 base_url 與 api_key。"
              : "你可以留下拒絕原因，讓申請者知道下一步。"}
          </p>
        </div>

        <div className={styles.dialogBody}>
          <div className={styles.dialogInfo}>
            <div>申請者：{request.user_full_name || request.user_email}</div>
            <div>金鑰名稱：{request.api_key_name}</div>
            <div>申請時間：{fmtTime(request.created_at)}</div>
            <div className={styles.dialogPurpose}>用途：{request.purpose}</div>
          </div>
          <textarea
            className={styles.dialogTextarea}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="審核備註（可留空）"
            rows={4}
          />
        </div>

        <div className={styles.dialogFooter}>
          <button type="button" className={styles.btnOutline} onClick={onClose} disabled={submitting}>
            取消
          </button>
          <button
            type="button"
            className={isApprove ? styles.btnPrimary : styles.btnDanger}
            onClick={handleSubmit}
            disabled={submitting}
          >
            {submitting ? "處理中…" : isApprove ? "確認通過" : "確認拒絕"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ── ReviewActions in table row ── */
function ReviewActions({ item, onDone }) {
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
          title="通過"
          onClick={() => setApproveOpen(true)}
        >
          <MIcon name="check" size={16} />
          通過
        </button>
        <button
          type="button"
          className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
          title="拒絕"
          onClick={() => setRejectOpen(true)}
        >
          <MIcon name="close" size={16} />
          拒絕
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
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("pending");
  const [allRequests, setAllRequests] = useState([]);
  const [loading, setLoading] = useState(true);

  /** silent = true 時不觸發 loading 與錯誤提示，供背景自動刷新使用 */
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await AiApiService.listAllRequests();
      setAllRequests(res?.data ?? []);
    } catch (e) {
      if (!silent) toast.error(e?.message ?? "載入 AI API 審核資料失敗");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(() => load(true));

  const filtered = useMemo(() => {
    if (activeTab === "all") return allRequests;
    return allRequests.filter((r) => r.status === activeTab);
  }, [allRequests, activeTab]);

  const COLS = ["申請者", "金鑰名稱", "用途", "狀態", "申請時間", "審核時間", "操作"];

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>AI API 審核</h1>
          <p className={styles.pageSubtitle}>審核申請並核發 API 存取參數。</p>
        </div>
      </div>

      <div className={styles.tabs}>
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={`${styles.tab} ${activeTab === tab.key ? styles.tabActive : ""}`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className={styles.content}>
        {loading ? (
          <div className={styles.loadingText}>載入中…</div>
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
                    <td className={styles.td}>{fmtTime(r.created_at)}</td>
                    <td className={styles.td}>{fmtTime(r.reviewed_at)}</td>
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
