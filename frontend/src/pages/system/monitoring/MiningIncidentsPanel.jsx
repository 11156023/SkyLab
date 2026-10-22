import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./MonitoringPage.module.scss";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import DataTable, { DataTableCells } from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { MiningIncidentsService } from "../../../services/miningIncidents";
import { useMiningIncidentCacheUpdater, useMiningIncidentsQuery } from "../../../queries/miningIncidents";
import { useToast } from "../../../hooks/useToast";
import { useLoadError } from "../../../hooks/useQueryLoadState";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { formatDateTime } from "../../../utils/formatDate";
import { joinClauses } from "../../../utils/joinList";

/** 待處理＝detected／suspended（與後端 open 定義相同）；banned／dismissed 為已結案 */
const isOpenIncident = (status) => status === "detected" || status === "suspended";

const EMPTY = Object.freeze([]);

/** 待處理顯示紅色，已結案中性 */
function statusBadgeClass(status) {
  return isOpenIncident(status) ? "badge_danger" : "badge_muted";
}

/**
 * 誤判解除後要顯示的提示。
 * 後端恢復 VM／刪存證快照失敗時仍會結案，只把失敗原因附進 review_note，
 * 所以不能只看 status（永遠是 dismissed）就報成功。
 * 優先用回應的 warnings 陣列；舊版後端沒有這個欄位時，review_note 與送出的備註不同
 * 就代表後端附加了失敗原因（沒有失敗時後端會原樣存入送出的備註）。
 */
export function dismissOutcome(incident, result, submittedNote) {
  let warnings;
  if (Array.isArray(result?.warnings)) {
    warnings = result.warnings.filter(Boolean);
  } else {
    const stored = result?.review_note ?? null;
    warnings = stored !== (submittedNote ?? null) && stored ? [stored] : [];
  }
  if (warnings.length > 0) {
    return { level: "warning", key: "MiningIncidentsPanel.toastDismissedWithWarnings", message: joinClauses(warnings) };
  }
  return {
    level: "success",
    key: incident?.status === "suspended"
      ? "MiningIncidentsPanel.toastDismissedAndRecovered"
      : "MiningIncidentsPanel.toastDismissed",
  };
}

/** 表頭文字（header 是元件，可以直接用 hook） */
function ColTitle({ name }) {
  const { t } = useTranslation("system");
  return t(`MiningIncidentsPanel.${name}`);
}

function VmidCell({ vmid }) {
  const { t } = useTranslation("system");
  /* 判斷誤判要看機器用途、CPU 曲線與存證快照，直接連到資源詳情 */
  return (
    <Link
      to={`/resource-mgmt/${vmid}`}
      className={styles.vmLink}
      title={t("MiningIncidentsPanel.openResourceDetail", { vmid })}
    >
      {vmid}
      <MIcon name="open_in_new" size={12} />
    </Link>
  );
}

function OwnerCell({ incident }) {
  const { t } = useTranslation("system");
  return incident.owner_email ? (
    <div className={styles.ownerCell}>
      <span>{incident.owner_full_name || incident.owner_email}</span>
      {incident.owner_full_name && (
        <span className={styles.mutedCell}>{incident.owner_email}</span>
      )}
    </div>
  ) : (
    <span className={styles.mutedCell}>{t("MiningIncidentsPanel.ownerUnknown")}</span>
  );
}

function WindowCell({ hours }) {
  const { t } = useTranslation("system");
  return t("MiningIncidentsPanel.hoursValue", { hours });
}

function SnapshotCell({ name }) {
  const { t } = useTranslation("system");
  return name ? (
    <span className={`${styles.monoCell} ${styles.snapCell}`}>
      <MIcon name="photo_camera" size={12} />
      {name}
    </span>
  ) : (
    <span className={styles.mutedCell}>{t("MiningIncidentsPanel.snapshotFailed")}</span>
  );
}

function StatusCell({ incident }) {
  const { t } = useTranslation("system");
  const STATUS_LABELS = {
    detected: t("MiningIncidentsPanel.statusDetected"),
    suspended: t("MiningIncidentsPanel.statusSuspended"),
    banned: t("MiningIncidentsPanel.statusBanned"),
    dismissed: t("MiningIncidentsPanel.statusDismissed"),
  };
  return (
    <>
      <span className={`${styles.badge} ${styles[statusBadgeClass(incident.status)]}`}>
        {STATUS_LABELS[incident.status] ?? incident.status}
      </span>
      {/* 結案備註含後端附加的失敗原因（例如 VM 恢復失敗要手動開機），重新整理後仍要看得到 */}
      {!isOpenIncident(incident.status) && incident.review_note && (
        <div className={`${styles.mutedCell} ${styles.reviewNote}`} title={incident.review_note}>
          {incident.review_note}
        </div>
      )}
    </>
  );
}

/** 停權／誤判解除；處理函式在點擊當下才從 table meta 取，拿到的一定是面板最新的那一份 */
function IncidentActions({ incident, busy, table }) {
  const { t } = useTranslation("system");
  if (!isOpenIncident(incident.status)) return null;
  return (
    <>
      <button
        type="button"
        className={styles.btnDangerOutline}
        disabled={busy}
        onClick={() => table.options.meta.onBan(incident)}
      >
        <MIcon name="block" size={14} />
        {t("MiningIncidentsPanel.ban")}
      </button>
      <button
        type="button"
        className={styles.btnSecondary}
        disabled={busy}
        onClick={() => table.options.meta.onDismiss(incident)}
      >
        <MIcon name="undo" size={14} />
        {t("MiningIncidentsPanel.dismissMisjudged")}
      </button>
    </>
  );
}

const columnHelper = createColumnHelper();

/* 欄位定義放模組層級：FlexRender 把函式型的 header／cell 當元件掛載，render 裡新建會整格重新掛載 */
const incidentColumns = columnHelper.columns([
  columnHelper.accessor("vmid", {
    header: "VMID",
    cell: ({ row }) => <VmidCell vmid={row.original.vmid} />,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.monoCell}` },
  }),
  columnHelper.display({
    id: "owner",
    header: () => <ColTitle name="colOwner" />,
    cell: ({ row }) => <OwnerCell incident={row.original} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("avg_cpu", {
    header: () => <ColTitle name="colAvgCpu" />,
    cell: ({ row }) => <>{row.original.avg_cpu.toFixed(1)}%</>,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.monoCell}` },
  }),
  columnHelper.accessor("window_hours", {
    header: () => <ColTitle name="colWindow" />,
    cell: ({ row }) => <WindowCell hours={row.original.window_hours} />,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.mutedCell}` },
  }),
  columnHelper.accessor("snapshot_name", {
    header: () => <ColTitle name="colSnapshot" />,
    cell: ({ row }) => <SnapshotCell name={row.original.snapshot_name} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("status", {
    header: () => <ColTitle name="colStatus" />,
    cell: ({ row }) => <StatusCell incident={row.original} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("detected_at", {
    header: () => <ColTitle name="colDetectedAt" />,
    cell: ({ row }) => formatDateTime(row.original.detected_at),
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.mutedCell}` },
  }),
  columnHelper.display({
    id: "actions",
    header: () => <ColTitle name="colActions" />,
    /* busy 是面板的狀態：經 rowState 傳進來（只改 table meta 不會讓儲存格重繪） */
    cell: ({ row, table, rowState }) => <IncidentActions incident={row.original} busy={rowState.busy} table={table} />,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.tdActions}` },
  }),
]);

export default function MiningIncidentsPanel({ onCountChange }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const [dismissTarget, setDismissTarget] = useState(null);
  const [dismissExempt, setDismissExempt] = useState(false);
  const [dismissNote, setDismissNote] = useState("");
  const [busy, setBusy] = useState(false);
  const dismissDialog = useDialogPresence(dismissTarget);
  /* 30 秒背景刷新、分頁隱藏時暫停。首次就抓不到時當作沒有事件（同原本，不擋住面板）；
     背景刷新失敗沿用上一份清單 */
  const incidentsQuery = useMiningIncidentsQuery();
  const incidentCache = useMiningIncidentCacheUpdater();
  /* 首載失敗看 useLoadError（沒資料時 v5 每次重抓都把 status 改回 pending、清掉 error，看 isError 會在背景重試時閃回載入中） */
  const incidentsLoadError = useLoadError(incidentsQuery).error;
  const incidents = incidentsQuery.data !== undefined
    ? incidentsQuery.data ?? EMPTY
    : incidentsLoadError ? EMPTY : null;

  const open = (incidents ?? []).filter((i) => isOpenIncident(i.status));
  const closed = (incidents ?? []).filter((i) => !isOpenIncident(i.status));

  /* 分頁角標顯示「待處理」筆數，載入後回報給監控頁 */
  useEffect(() => {
    if (incidents !== null) onCountChange?.(open.length);
  }, [incidents, open.length, onCountChange]);

  /* 停權不需要填理由，直接用共用確認框；送出期間 busy 擋住重複點擊 */
  const handleBan = async (incident) => {
    if (busy) return;
    const ok = await confirm({
      title: t("MiningIncidentsPanel.banConfirmTitle"),
      message: t("MiningIncidentsPanel.banConfirmMessage", { vmid: incident.vmid }),
      confirmText: t("MiningIncidentsPanel.confirmBan"),
      cancelText: t("MiningIncidentsPanel.cancel"),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await MiningIncidentsService.ban(incident.id);
      toast.success(t("MiningIncidentsPanel.toastBanSuccess"));
      /* 重抓完才解除 busy（同原本 await load()）；管理首頁的待處理數也一起過期 */
      await incidentCache.invalidate();
    } catch (e) {
      toast.error(t("MiningIncidentsPanel.toastBanFailed", { message: e?.message ?? t("MiningIncidentsPanel.unknownError") }));
    } finally {
      setBusy(false);
    }
  };

  const closeDismiss = () => {
    setDismissTarget(null);
    setDismissExempt(false);
    setDismissNote("");
  };

  const handleDismiss = async () => {
    setBusy(true);
    try {
      const note = dismissNote || null;
      const result = await MiningIncidentsService.dismiss(dismissTarget.id, {
        exempt: dismissExempt,
        note,
      });
      const outcome = dismissOutcome(dismissTarget, result, note);
      if (outcome.level === "warning") {
        toast.warning(t(outcome.key, { message: outcome.message }));
      } else {
        toast.success(t(outcome.key));
      }
      closeDismiss();
      await incidentCache.invalidate();
    } catch (e) {
      toast.error(t("MiningIncidentsPanel.toastDismissFailed", { message: e?.message ?? t("MiningIncidentsPanel.unknownError") }));
    } finally {
      setBusy(false);
    }
  };

  /* 待處理排前面、已結案在後 */
  const table = useTable({
    features: baseFeatures,
    columns: incidentColumns,
    data: [...open, ...closed],
    getRowId: (incident) => String(incident.id),
    meta: { onBan: handleBan, onDismiss: setDismissTarget },
  });

  return (
    <div className={styles.card}>
      {/* 標題由外層頁籤承擔，卡內只留說明 */}
      <div className={styles.cardHeader}>
        <p className={styles.cardDesc}>{t("MiningIncidentsPanel.desc")}</p>
        {open.length > 0 && <span className={styles.alertCount}>{t("MiningIncidentsPanel.pendingCount", { count: open.length })}</span>}
      </div>

      {incidents === null ? (
        <LoadingState />
      ) : incidents.length === 0 ? (
        <EmptyState icon="verified_user" title={t("MiningIncidentsPanel.emptyNone")} />
      ) : (
        <DataTable
          table={table}
          className={styles.table}
          renderRow={(row) => (
            <tr key={row.id} className={styles.tr}>
              <DataTableCells row={row} rowState={{ busy }} />
            </tr>
          )}
        />
      )}

      {/* 誤判解除：要勾選豁免與填備註，不能用 useConfirm；送出中 Esc／點遮罩都不關 */}
      {dismissDialog.open && (
        <Modal
          closing={dismissDialog.closing}
          onClose={closeDismiss}
          busy={busy}
          title={t("MiningIncidentsPanel.dismissTitle")}
          description={t("MiningIncidentsPanel.dismissMessage", { vmid: dismissDialog.item.vmid })}
          actions={
            <>
              <button type="button" className={styles.btnSecondary} disabled={busy} onClick={closeDismiss}>
                {t("MiningIncidentsPanel.cancel")}
              </button>
              <button
                type="button"
                className={styles.btnPrimary}
                disabled={busy}
                onClick={handleDismiss}
              >
                {busy ? t("MiningIncidentsPanel.processing") : t("MiningIncidentsPanel.confirmDismiss")}
              </button>
            </>
          }
        >
          <label className={styles.checkLine}>
            <input
              type="checkbox"
              checked={dismissExempt}
              onChange={(e) => setDismissExempt(e.target.checked)}
            />
            {t("MiningIncidentsPanel.exemptLabel")}
          </label>
          <div className={styles.field}>
            <label htmlFor="mining-note">{t("MiningIncidentsPanel.noteLabel")}</label>
            <textarea
              id="mining-note"
              rows={3}
              placeholder={t("MiningIncidentsPanel.notePlaceholder")}
              value={dismissNote}
              onChange={(e) => setDismissNote(e.target.value)}
            />
          </div>
        </Modal>
      )}
    </div>
  );
}
