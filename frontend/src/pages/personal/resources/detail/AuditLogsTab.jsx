import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./ResourceDetailPage.module.scss";
import LoadingState from "../../../../components/LoadingState/LoadingState";
import EmptyState from "../../../../components/EmptyState/EmptyState";
import { AuditLogsService } from "../../../../services/auditLogs";
import { formatDateTime } from "../../../../utils/formatDate";

/** 依動作類型決定 badge 色系（僅使用四種語意色） */
function actionBadgeClass(action) {
  if (action.includes("create")) return "badge_ok";
  if (action.includes("delete")) return "badge_err";
  return "badge_info";
}

export default function AuditLogsTab({ vmid }) {
  const { t } = useTranslation("personal");
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

  if (error) return <p className={styles.stateText}>{t("AuditLogsTab.loadFailed")}</p>;
  if (!logs) return <LoadingState />;

  return (
    <div className={styles.tabStack}>
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>{t("AuditLogsTab.title")}</h2>
            <p className={styles.cardDesc}>{t("AuditLogsTab.desc", { count: logs.count })}</p>
          </div>
        </div>
        {logs.data.length === 0 ? (
          <EmptyState icon="receipt_long" title={t("AuditLogsTab.empty")} />
        ) : (
          <div className={styles.tableScroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>{t("AuditLogsTab.colTime")}</th>
                <th className={styles.th}>{t("AuditLogsTab.colOperator")}</th>
                <th className={styles.th}>{t("AuditLogsTab.colAction")}</th>
                <th className={styles.th}>{t("AuditLogsTab.colDetails")}</th>
              </tr>
            </thead>
            <tbody>
              {logs.data.map((log) => (
                <tr key={log.id} className={styles.tr}>
                  <td className={`${styles.td} ${styles.nowrapCell}`}>
                    {formatDateTime(log.created_at)}
                  </td>
                  <td className={styles.td}>
                    <div className={styles.userCell}>
                      <span className={styles.userName}>
                        {log.user_full_name || log.user_email || t("AuditLogsTab.system")}
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
          </div>
        )}
      </div>
    </div>
  );
}
