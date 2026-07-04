import { useEffect, useState } from "react";
import styles from "./ResourceDetailPage.module.scss";
import { AuditLogsService } from "../../../../services/auditLogs";

/** 依動作類型決定 badge 色系（僅使用四種語意色） */
function actionBadgeClass(action) {
  if (action.includes("create")) return "badge_ok";
  if (action.includes("delete")) return "badge_err";
  return "badge_info";
}

export default function AuditLogsTab({ vmid }) {
  const [logs, setLogs] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    AuditLogsService.listForResource(vmid, { skip: 0, limit: 100 })
      .then((res) => !cancelled && setLogs(res))
      .catch(() => !cancelled && setError(true));
    return () => {
      cancelled = true;
    };
  }, [vmid]);

  if (error) return <p className={styles.stateText}>無法載入操作紀錄</p>;
  if (!logs) return <p className={styles.stateText}>載入中…</p>;

  return (
    <div className={styles.tabStack}>
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>操作紀錄</h2>
            <p className={styles.cardDesc}>此資源的所有操作（共 {logs.count} 筆）</p>
          </div>
        </div>
        {logs.data.length === 0 ? (
          <p className={styles.stateText}>尚無紀錄</p>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>時間</th>
                <th className={styles.th}>操作者</th>
                <th className={styles.th}>動作</th>
                <th className={styles.th}>詳細</th>
              </tr>
            </thead>
            <tbody>
              {logs.data.map((log) => (
                <tr key={log.id} className={styles.tr}>
                  <td className={`${styles.td} ${styles.nowrapCell}`}>
                    {new Date(log.created_at).toLocaleString("zh-TW")}
                  </td>
                  <td className={styles.td}>
                    <div className={styles.userCell}>
                      <span className={styles.userName}>
                        {log.user_full_name || "Unknown"}
                      </span>
                      <span className={styles.userEmail}>{log.user_email}</span>
                    </div>
                  </td>
                  <td className={styles.td}>
                    <span className={`${styles.badge} ${styles[actionBadgeClass(log.action)]}`}>
                      {log.action}
                    </span>
                  </td>
                  <td className={`${styles.td} ${styles.detailCell}`} title={log.details}>
                    {log.details}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
