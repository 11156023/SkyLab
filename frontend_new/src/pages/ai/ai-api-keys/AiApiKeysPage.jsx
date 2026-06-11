import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./AiApiKeysPage.module.scss";
import MIcon from "../../../components/MIcon";
import { AiApiService } from "../../../services/aiApi";
import { useToast } from "../../../hooks/useToast";

const PAGE_SIZE = 50;

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString("zh-TW") : "—";
}

function inactiveReasonLabel(reason) {
  if (!reason) return "—";
  return reason === "revoked" ? "已撤銷" : "已過期";
}

function StatusBadge({ item }) {
  const isActive = item.status === "active";
  return (
    <span className={`${styles.badge} ${isActive ? styles.badge_active : styles.badge_inactive}`}>
      <span className={styles.dot} />
      {isActive ? "啟用" : "失效"}
    </span>
  );
}

function EmptyState() {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name="vpn_key" size={40} />
      </div>
      <h2 className={styles.emptyTitle}>尚無金鑰紀錄</h2>
      <p className={styles.emptyDesc}>目前沒有符合條件的金鑰紀錄。</p>
    </div>
  );
}

/* ── Delete dialog ── */
function DeleteDialog({ item, onClose, onDone }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  if (!item) return null;

  const handleDelete = async () => {
    setBusy(true);
    try {
      await AiApiService.revokeCredential(item.id);
      toast.success("金鑰已刪除");
      onClose();
      onDone();
    } catch (e) {
      toast.error(e?.message ?? "刪除失敗");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.dialogOverlay} onClick={() => { if (!busy) onClose(); }}>
      <div className={styles.dialog} onClick={(e) => e.stopPropagation()}>
        <div className={styles.dialogHeader}>
          <h3 className={styles.dialogTitle}>確認刪除這把金鑰？</h3>
          <p className={styles.dialogDesc}>
            你即將刪除「{item.api_key_name}」。這個動作無法復原。
          </p>
        </div>
        <div className={styles.dialogFooter}>
          <button type="button" className={styles.btnOutline} onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="button" className={styles.btnDanger} onClick={handleDelete} disabled={busy}>
            {busy ? "刪除中…" : "確認刪除"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function AiApiKeysPage() {
  const toast = useToast();
  const [statusFilter, setStatusFilter] = useState("all");
  const [userEmail, setUserEmail] = useState("");
  const [page, setPage] = useState(0);
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [deletingItem, setDeletingItem] = useState(null);

  /* ── counts ── */
  const [allCount, setAllCount] = useState(0);
  const [activeCount, setActiveCount] = useState(0);
  const [inactiveCount, setInactiveCount] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await AiApiService.listAllCredentials();
      const data = res?.data ?? [];
      setTotal(data.length);
      setAllCount(data.length);
      setActiveCount(data.filter((c) => c.status === "active").length);
      setInactiveCount(data.filter((c) => c.status === "inactive").length);

      // Client-side filtering
      let filtered = data;
      if (statusFilter !== "all") {
        filtered = filtered.filter((c) => c.status === statusFilter);
      }
      if (userEmail.trim()) {
        const q = userEmail.trim().toLowerCase();
        filtered = filtered.filter(
          (c) =>
            (c.user_email ?? "").toLowerCase().includes(q) ||
            (c.user_full_name ?? "").toLowerCase().includes(q),
        );
      }

      setTotal(filtered.length);
      // Paginate
      const start = page * PAGE_SIZE;
      setRows(filtered.slice(start, start + PAGE_SIZE));
    } catch (e) {
      toast.error(e?.message ?? "載入金鑰資料失敗");
    } finally {
      setLoading(false);
    }
  }, [statusFilter, userEmail, page, toast]);

  useEffect(() => { load(); }, [load]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const COLS = ["使用者", "金鑰名稱", "金鑰前綴", "狀態", "失效原因", "建立時間", "過期時間", "撤銷時間", "操作"];

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>AI API 金鑰狀態</h1>
          <p className={styles.pageSubtitle}>
            查看目前資料庫中所有 AI API 金鑰紀錄與狀態（僅顯示現存紀錄）。
          </p>
        </div>
        <div className={styles.pageActions}>
          <button type="button" className={styles.btnSecondary} onClick={load} disabled={loading}>
            <MIcon name="sync" size={16} />
            {loading ? "載入中…" : "重新整理"}
          </button>
        </div>
      </div>

      {/* ── Stat cards ── */}
      <div className={styles.statRow}>
        <div className={styles.statCard}>
          <div className={styles.statIcon}><MIcon name="key" size={20} /></div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>全部紀錄</span>
            <span className={styles.statValue}>{allCount}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconOk}`}><MIcon name="check_circle" size={20} /></div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>啟用</span>
            <span className={styles.statValue}>{activeCount}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconErr}`}><MIcon name="cancel" size={20} /></div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>失效</span>
            <span className={styles.statValue}>{inactiveCount}</span>
          </div>
        </div>
      </div>

      {/* ── Filters ── */}
      <div className={styles.filterCard}>
        <h3 className={styles.filterTitle}>篩選</h3>
        <p className={styles.filterDesc}>可依狀態與使用者 Email 篩選。</p>
        <div className={styles.filterRow}>
          <select
            className={styles.filterSelect}
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(0); }}
          >
            <option value="all">全部</option>
            <option value="active">啟用</option>
            <option value="inactive">失效</option>
          </select>
          <input
            type="text"
            className={styles.filterInput}
            placeholder="使用者 Email 關鍵字"
            value={userEmail}
            onChange={(e) => { setUserEmail(e.target.value); setPage(0); }}
          />
        </div>
      </div>

      {/* ── Table ── */}
      <div className={styles.content}>
        {loading ? (
          <div className={styles.loadingText}>載入中…</div>
        ) : rows.length === 0 ? (
          <EmptyState />
        ) : (
          <>
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
                  {rows.map((item) => (
                    <tr key={item.id} className={styles.tr}>
                      <td className={styles.td}>
                        <div className={styles.userCell}>
                          <span className={styles.userName}>
                            {item.user_full_name
                              ? `${item.user_full_name} (${item.user_email || "—"})`
                              : (item.user_email ?? "—")}
                          </span>
                        </div>
                      </td>
                      <td className={styles.td}>{item.api_key_name}</td>
                      <td className={`${styles.td} ${styles.mono}`}>{item.api_key_prefix}</td>
                      <td className={styles.td}><StatusBadge item={item} /></td>
                      <td className={styles.td}>{inactiveReasonLabel(item.inactive_reason)}</td>
                      <td className={styles.td}>{fmtTime(item.created_at)}</td>
                      <td className={styles.td}>{fmtTime(item.expires_at)}</td>
                      <td className={styles.td}>{fmtTime(item.revoked_at)}</td>
                      <td className={styles.td}>
                        <button
                          type="button"
                          className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                          title="刪除"
                          onClick={() => setDeletingItem(item)}
                        >
                          <MIcon name="delete" size={16} />
                          刪除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* ── Pagination ── */}
            {totalPages > 1 && (
              <div className={styles.pagination}>
                <span className={styles.paginationInfo}>
                  第 {page + 1} / {totalPages} 頁，共 {total} 筆
                </span>
                <div className={styles.paginationBtns}>
                  <button
                    type="button"
                    className={styles.btnOutline}
                    disabled={page === 0}
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                  >
                    上一頁
                  </button>
                  <button
                    type="button"
                    className={styles.btnOutline}
                    disabled={page + 1 >= totalPages}
                    onClick={() => setPage((p) => (p + 1 >= totalPages ? p : p + 1))}
                  >
                    下一頁
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* ── Delete dialog ── */}
      {deletingItem && (
        <DeleteDialog
          item={deletingItem}
          onClose={() => setDeletingItem(null)}
          onDone={load}
        />
      )}
    </div>
  );
}
