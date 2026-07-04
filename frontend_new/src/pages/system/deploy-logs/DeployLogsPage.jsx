import { useCallback, useEffect, useState } from "react";
import styles from "./DeployLogsPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import { ScriptDeployLogsService } from "../../../services/scriptDeployLogs";

const PAGE_SIZE = 50;

const STATUS_META = {
  pending:   { label: "排隊中", badge: "badge_info" },
  running:   { label: "部署中", badge: "badge_info" },
  completed: { label: "成功",   badge: "badge_success" },
  success:   { label: "成功",   badge: "badge_success" },
  failed:    { label: "失敗",   badge: "badge_danger" },
};

const STATUS_FILTERS = [
  { value: "", label: "全部" },
  { value: "pending", label: "排隊中" },
  { value: "running", label: "部署中" },
  { value: "completed", label: "成功" },
  { value: "failed", label: "失敗" },
];

function formatTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-TW", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function DetailModal({ taskId, onClose }) {
  const toast = useToast();
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    ScriptDeployLogsService.detail(taskId)
      .then(setDetail)
      .catch((err) => toast.error(err?.message ?? "載入部署詳情失敗"))
      .finally(() => setLoading(false));
  }, [taskId, toast]);

  const meta = STATUS_META[detail?.status] ?? { label: detail?.status, badge: "badge_muted" };

  return (
    <div className={styles.modalOverlay} onMouseDown={onClose}>
      <div className={styles.modal} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>部署詳情</h2>
            <p>Task ID：{taskId}</p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label="關閉">
            <MIcon name="close" size={18} />
          </button>
        </div>

        {loading ? (
          <div className={styles.loading}>載入中...</div>
        ) : !detail ? (
          <p className={styles.modalEmpty}>找不到此部署紀錄</p>
        ) : (
          <>
            <div className={styles.detailMeta}>
              <span className={`${styles.badge} ${styles[meta.badge]}`}>{meta.label}</span>
              <span>{detail.template_name ?? detail.template_slug}</span>
              {detail.hostname && <span>主機：{detail.hostname}</span>}
              {detail.vmid != null && <span>VMID：{detail.vmid}</span>}
              <span>建立：{formatTime(detail.created_at)}</span>
              {detail.completed_at && <span>完成：{formatTime(detail.completed_at)}</span>}
            </div>

            {detail.message && <p className={styles.detailMessage}>{detail.message}</p>}

            {detail.error && (
              <div>
                <h3 className={styles.blockTitle}>錯誤</h3>
                <pre className={`${styles.logBlock} ${styles.logBlockError}`}>{detail.error}</pre>
              </div>
            )}

            {detail.output && (
              <div>
                <h3 className={styles.blockTitle}>輸出</h3>
                <pre className={styles.logBlock}>{detail.output}</pre>
              </div>
            )}
          </>
        )}

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            關閉
          </button>
        </div>
      </div>
    </div>
  );
}

export default function DeployLogsPage() {
  const toast = useToast();
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(0);
  const [detailTaskId, setDetailTaskId] = useState(null);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await ScriptDeployLogsService.list({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        status: statusFilter || undefined,
      });
      setItems(res?.items ?? []);
      setTotal(res?.total ?? 0);
    } catch (err) {
      toast.error(err?.message ?? "載入部署日誌失敗");
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter, toast]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  const totalPages = Math.max(Math.ceil(total / PAGE_SIZE), 1);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>部署日誌</h1>
          <p className={styles.pageSubtitle}>快速模板一鍵部署的執行紀錄與輸出</p>
        </div>
        <button type="button" className={styles.btnSecondary} onClick={fetchLogs} disabled={loading}>
          <MIcon name="refresh" size={16} />
          重新整理
        </button>
      </div>

      <div className={styles.filterTabs}>
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.value}
            type="button"
            className={statusFilter === f.value ? styles.filterTabActive : styles.filterTab}
            onClick={() => {
              setStatusFilter(f.value);
              setPage(0);
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className={styles.content}>
        {loading && items.length === 0 ? (
          <div className={styles.loading}>載入部署日誌...</div>
        ) : items.length === 0 ? (
          <div className={styles.empty}>
            <div className={styles.emptyIcon}>
              <MIcon name="terminal" size={40} />
            </div>
            <h2 className={styles.emptyTitle}>
              {statusFilter ? "沒有符合的紀錄" : "尚無部署紀錄"}
            </h2>
            <p className={styles.emptyDesc}>
              {statusFilter ? "請切換其他狀態篩選。" : "透過快速模板部署服務後，執行紀錄會顯示在這裡"}
            </p>
          </div>
        ) : (
          <>
            <div className={styles.list}>
              {items.map((item) => {
                const meta = STATUS_META[item.status] ?? { label: item.status, badge: "badge_muted" };
                return (
                  <button
                    key={item.id}
                    type="button"
                    className={styles.row}
                    onClick={() => setDetailTaskId(item.task_id)}
                  >
                    <div className={styles.rowIcon}>
                      <MIcon name="terminal" size={20} />
                    </div>
                    <div className={styles.rowMain}>
                      <span className={styles.rowName}>
                        {item.template_name ?? item.template_slug}
                        {item.hostname ? ` · ${item.hostname}` : ""}
                      </span>
                      <span className={styles.rowMeta}>
                        {item.vmid != null ? `VMID ${item.vmid} · ` : ""}
                        {formatTime(item.created_at)}
                        {item.progress ? ` · ${item.progress}` : ""}
                        {item.message ? ` · ${item.message}` : ""}
                      </span>
                    </div>
                    <span className={`${styles.badge} ${styles[meta.badge]}`}>{meta.label}</span>
                  </button>
                );
              })}
            </div>

            {totalPages > 1 && (
              <div className={styles.pagination}>
                <span className={styles.paginationInfo}>
                  共 {total} 筆 · 第 {page + 1} / {totalPages} 頁
                </span>
                <div className={styles.paginationBtns}>
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    disabled={page === 0}
                    onClick={() => setPage((p) => Math.max(p - 1, 0))}
                  >
                    上一頁
                  </button>
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    disabled={page + 1 >= totalPages}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    下一頁
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {detailTaskId && (
        <DetailModal taskId={detailTaskId} onClose={() => setDetailTaskId(null)} />
      )}
    </div>
  );
}
