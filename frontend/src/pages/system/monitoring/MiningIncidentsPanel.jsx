import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import styles from "./MonitoringPage.module.scss";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { MiningIncidentsService } from "../../../services/miningIncidents";
import { useMiningIncidentCacheUpdater, useMiningIncidentsQuery } from "../../../queries/miningIncidents";
import { useToast } from "../../../hooks/useToast";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { formatDateTime } from "../../../utils/formatDate";

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
    return { level: "warning", key: "MiningIncidentsPanel.toastDismissedWithWarnings", message: warnings.join("；") };
  }
  return {
    level: "success",
    key: incident?.status === "suspended"
      ? "MiningIncidentsPanel.toastDismissedAndRecovered"
      : "MiningIncidentsPanel.toastDismissed",
  };
}

export default function MiningIncidentsPanel({ onCountChange }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const STATUS_LABELS = {
    detected: t("MiningIncidentsPanel.statusDetected"),
    suspended: t("MiningIncidentsPanel.statusSuspended"),
    banned: t("MiningIncidentsPanel.statusBanned"),
    dismissed: t("MiningIncidentsPanel.statusDismissed"),
  };
  const [dismissTarget, setDismissTarget] = useState(null);
  const [dismissExempt, setDismissExempt] = useState(false);
  const [dismissNote, setDismissNote] = useState("");
  const [busy, setBusy] = useState(false);
  const dismissDialog = useDialogPresence(dismissTarget);
  /* 30 秒背景刷新、分頁隱藏時暫停。首次就抓不到時當作沒有事件（同原本，不擋住面板）；
     背景刷新失敗沿用上一份清單 */
  const incidentsQuery = useMiningIncidentsQuery();
  const incidentCache = useMiningIncidentCacheUpdater();
  const incidents = incidentsQuery.data !== undefined
    ? incidentsQuery.data ?? EMPTY
    : incidentsQuery.isError ? EMPTY : null;

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
        <table className={styles.table}>
          <thead>
            <tr>
              <th className={styles.th}>VMID</th>
              <th className={styles.th}>{t("MiningIncidentsPanel.colOwner")}</th>
              <th className={styles.th}>{t("MiningIncidentsPanel.colAvgCpu")}</th>
              <th className={styles.th}>{t("MiningIncidentsPanel.colWindow")}</th>
              <th className={styles.th}>{t("MiningIncidentsPanel.colSnapshot")}</th>
              <th className={styles.th}>{t("MiningIncidentsPanel.colStatus")}</th>
              <th className={styles.th}>{t("MiningIncidentsPanel.colDetectedAt")}</th>
              <th className={styles.th}>{t("MiningIncidentsPanel.colActions")}</th>
            </tr>
          </thead>
          <tbody>
            {[...open, ...closed].map((incident) => (
              <tr key={incident.id} className={styles.tr}>
                <td className={`${styles.td} ${styles.monoCell}`}>
                  {/* 判斷誤判要看機器用途、CPU 曲線與存證快照，直接連到資源詳情 */}
                  <Link
                    to={`/resource-mgmt/${incident.vmid}`}
                    className={styles.vmLink}
                    title={t("MiningIncidentsPanel.openResourceDetail", { vmid: incident.vmid })}
                  >
                    {incident.vmid}
                    <MIcon name="open_in_new" size={12} />
                  </Link>
                </td>
                <td className={styles.td}>
                  {incident.owner_email ? (
                    <div className={styles.ownerCell}>
                      <span>{incident.owner_full_name || incident.owner_email}</span>
                      {incident.owner_full_name && (
                        <span className={styles.mutedCell}>{incident.owner_email}</span>
                      )}
                    </div>
                  ) : (
                    <span className={styles.mutedCell}>{t("MiningIncidentsPanel.ownerUnknown")}</span>
                  )}
                </td>
                <td className={`${styles.td} ${styles.monoCell}`}>
                  {incident.avg_cpu.toFixed(1)}%
                </td>
                <td className={`${styles.td} ${styles.mutedCell}`}>
                  {t("MiningIncidentsPanel.hoursValue", { hours: incident.window_hours })}
                </td>
                <td className={styles.td}>
                  {incident.snapshot_name ? (
                    <span className={`${styles.monoCell} ${styles.snapCell}`}>
                      <MIcon name="photo_camera" size={12} />
                      {incident.snapshot_name}
                    </span>
                  ) : (
                    <span className={styles.mutedCell}>{t("MiningIncidentsPanel.snapshotFailed")}</span>
                  )}
                </td>
                <td className={styles.td}>
                  <span className={`${styles.badge} ${styles[statusBadgeClass(incident.status)]}`}>
                    {STATUS_LABELS[incident.status] ?? incident.status}
                  </span>
                  {/* 結案備註含後端附加的失敗原因（例如 VM 恢復失敗要手動開機），重新整理後仍要看得到 */}
                  {!isOpenIncident(incident.status) && incident.review_note && (
                    <div className={`${styles.mutedCell} ${styles.reviewNote}`} title={incident.review_note}>
                      {incident.review_note}
                    </div>
                  )}
                </td>
                <td className={`${styles.td} ${styles.mutedCell}`}>
                  {formatDateTime(incident.detected_at)}
                </td>
                <td className={`${styles.td} ${styles.tdActions}`}>
                  {isOpenIncident(incident.status) && (
                    <>
                      <button
                        type="button"
                        className={styles.btnDangerOutline}
                        disabled={busy}
                        onClick={() => handleBan(incident)}
                      >
                        <MIcon name="block" size={14} />
                        {t("MiningIncidentsPanel.ban")}
                      </button>
                      <button
                        type="button"
                        className={styles.btnSecondary}
                        disabled={busy}
                        onClick={() => setDismissTarget(incident)}
                      >
                        <MIcon name="undo" size={14} />
                        {t("MiningIncidentsPanel.dismissMisjudged")}
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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
