import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./ResourceDetailPage.module.scss";
import DataTable from "../../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../../components/DataTable/features";
import LoadingState from "../../../../components/LoadingState/LoadingState";
import EmptyState from "../../../../components/EmptyState/EmptyState";
import ErrorState from "../../../../components/ErrorState/ErrorState";
import NotFoundState from "../../../../components/ErrorState/NotFoundState";
import { isNotFound } from "../../../../services/api";
import { useResourceAuditLogsQuery } from "../../../../queries/auditLogs";
import { useLoadError } from "../../../../hooks/useQueryLoadState";
import { formatDateTime } from "../../../../utils/formatDate";
import { actionBadgeClass, actionLabel } from "./auditActions";

const LOG_PARAMS = Object.freeze({ skip: 0, limit: 100 });
const EMPTY = Object.freeze([]);

/* ── 欄位定義（模組層級：cell／header 會被當成元件掛載，render 裡新建會整格重掛） ── */
function AuditHeader({ column }) {
  const { t } = useTranslation("personal");
  return t(column.columnDef.meta.labelKey);
}

function AuditOperatorCell({ row }) {
  const { t } = useTranslation("personal");
  const log = row.original;
  return (
    <div className={styles.userCell}>
      <span className={styles.userName}>
        {log.user_full_name || log.user_email || t("AuditLogsTab.system")}
      </span>
      <span className={styles.userEmail}>{log.user_email}</span>
    </div>
  );
}

function AuditActionCell({ row }) {
  const { t } = useTranslation("personal");
  const { action } = row.original;
  return (
    <span className={`${styles.badge} ${styles[actionBadgeClass(action)]}`}>
      {actionLabel(action, t)}
    </span>
  );
}

const columnHelper = createColumnHelper();
const AUDIT_COLUMNS = columnHelper.columns([
  columnHelper.accessor("created_at", {
    header: AuditHeader,
    cell: (info) => formatDateTime(info.getValue()),
    meta: { labelKey: "AuditLogsTab.colTime", thClassName: styles.th, tdClassName: `${styles.td} ${styles.nowrapCell}` },
  }),
  columnHelper.display({
    id: "operator",
    header: AuditHeader,
    cell: AuditOperatorCell,
    meta: { labelKey: "AuditLogsTab.colOperator", thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.display({
    id: "action",
    header: AuditHeader,
    cell: AuditActionCell,
    meta: { labelKey: "AuditLogsTab.colAction", thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("details", {
    header: AuditHeader,
    cell: (info) => info.getValue(),
    /* 詳細內容會被截斷，整段放 td 的 title */
    meta: {
      labelKey: "AuditLogsTab.colDetails",
      thClassName: styles.th,
      tdClassName: `${styles.td} ${styles.detailCell}`,
      tdProps: (row) => ({ title: row.original.details }),
    },
  }),
]);

const getLogId = (log) => String(log.id);
const logRowClassName = () => styles.tr;

export default function AuditLogsTab({ vmid }) {
  const { t } = useTranslation("personal");
  /* 進分頁時抓一次（與原本相同不輪詢）；回到分頁時先顯示快取，過期才在背景重抓 */
  const logsQuery = useResourceAuditLogsQuery(vmid, LOG_PARAMS, { refetchInterval: false });
  const logs = logsQuery.data ?? null;
  /* 從沒載到就失敗才顯示錯誤；切回視窗等重抓期間維持錯誤畫面、不閃轉圈圈（見 useLoadError） */
  const { error } = useLoadError(logsQuery);
  const logTable = useTable({
    features: baseFeatures,
    columns: AUDIT_COLUMNS,
    data: logs?.data ?? EMPTY,
    getRowId: getLogId,
  });

  if (error) return isNotFound(error) ? <NotFoundState /> : <ErrorState />;
  if (!logs) return <LoadingState />;

  return (
    <div className={styles.tabStack}>
      <div className={styles.card}>
        {logs.data.length === 0 ? (
          <EmptyState icon="receipt_long" title={t("AuditLogsTab.empty")} />
        ) : (
          <div className={styles.tableScroll}>
          <DataTable table={logTable} className={styles.table} rowClassName={logRowClassName} />
          </div>
        )}
      </div>
    </div>
  );
}
