import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./MonitoringPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { MiningIncidentsService } from "../../../services/miningIncidents";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { formatDateTime } from "../../../utils/formatDate";

/** detected/suspended 視為待處理（紅），其餘中性 */
function statusBadgeClass(status) {
  return status === "detected" || status === "suspended" ? "badge_err" : "badge_muted";
}

export default function MiningIncidentsPanel({ onCountChange }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const STATUS_LABELS = {
    detected: t("MiningIncidentsPanel.statusDetected"),
    suspended: t("MiningIncidentsPanel.statusSuspended"),
    banned: t("MiningIncidentsPanel.statusBanned"),
    dismissed: t("MiningIncidentsPanel.statusDismissed"),
  };
  const [incidents, setIncidents] = useState(null);
  const [banTarget, setBanTarget] = useState(null);
  const [dismissTarget, setDismissTarget] = useState(null);
  const [dismissExempt, setDismissExempt] = useState(false);
  const [dismissNote, setDismissNote] = useState("");
  const [busy, setBusy] = useState(false);
  const banDialog     = useDialogPresence(banTarget);
  const dismissDialog = useDialogPresence(dismissTarget);

  const load = useCallback(async () => {
    try {
      setIncidents(await MiningIncidentsService.list());
    } catch {
      setIncidents((prev) => prev ?? []);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, [load]);

  const open = (incidents ?? []).filter(
    (i) => i.status === "detected" || i.status === "suspended",
  );
  const closed = (incidents ?? []).filter(
    (i) => i.status === "banned" || i.status === "dismissed",
  );

  /* 分頁角標顯示「待處理」筆數，載入後回報給監控頁 */
  useEffect(() => {
    if (incidents !== null) onCountChange?.(open.length);
  }, [incidents, open.length, onCountChange]);

  const handleBan = async () => {
    setBusy(true);
    try {
      await MiningIncidentsService.ban(banTarget.id);
      toast.success(t("MiningIncidentsPanel.toastBanSuccess"));
      setBanTarget(null);
      await load();
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
      const result = await MiningIncidentsService.dismiss(dismissTarget.id, {
        exempt: dismissExempt,
        note: dismissNote || null,
      });
      toast.success(result.status === "dismissed" ? t("MiningIncidentsPanel.toastDismissedAndRecovered") : t("MiningIncidentsPanel.toastDismissed"));
      closeDismiss();
      await load();
    } catch (e) {
      toast.error(t("MiningIncidentsPanel.toastDismissFailed", { message: e?.message ?? t("MiningIncidentsPanel.unknownError") }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <div>
          <h2 className={styles.cardTitle}>
            <MIcon name="gavel" size={18} />
            {t("MiningIncidentsPanel.title")}
          </h2>
          <p className={styles.cardDesc}>
            {t("MiningIncidentsPanel.desc")}
          </p>
        </div>
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
              <th className={styles.th}>{t("MiningIncidentsPanel.colAvgCpu")}</th>
              <th className={styles.th}>{t("MiningIncidentsPanel.colWindow")}</th>
              <th className={styles.th}>{t("MiningIncidentsPanel.colSnapshot")}</th>
              <th className={styles.th}>{t("MiningIncidentsPanel.colStatus")}</th>
              <th className={styles.th}>{t("MiningIncidentsPanel.colDetectedAt")}</th>
              <th className={`${styles.th} ${styles.thRight}`}>{t("MiningIncidentsPanel.colActions")}</th>
            </tr>
          </thead>
          <tbody>
            {[...open, ...closed].map((incident) => (
              <tr key={incident.id} className={styles.tr}>
                <td className={`${styles.td} ${styles.monoCell}`}>{incident.vmid}</td>
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
                </td>
                <td className={`${styles.td} ${styles.mutedCell}`}>
                  {formatDateTime(incident.detected_at)}
                </td>
                <td className={`${styles.td} ${styles.tdRight}`}>
                  {(incident.status === "detected" || incident.status === "suspended") && (
                    <>
                      <button
                        type="button"
                        className={styles.btnDangerOutline}
                        onClick={() => setBanTarget(incident)}
                      >
                        <MIcon name="block" size={14} />
                        {t("MiningIncidentsPanel.ban")}
                      </button>
                      <button
                        type="button"
                        className={styles.btnSecondary}
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

      {/* 停權確認 */}
      {banDialog.open && (
        <div
          className={`${styles.modalOverlay} ${banDialog.closing ? styles.modalOverlayOut : ""}`}
          onClick={() => setBanTarget(null)}
        >
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <span className={styles.modalTitle}>{t("MiningIncidentsPanel.banConfirmTitle")}</span>
            <p className={styles.modalDesc}>
              {t("MiningIncidentsPanel.banConfirmMessage", { vmid: banDialog.item.vmid })}
            </p>
            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={() => setBanTarget(null)}
              >
                {t("MiningIncidentsPanel.cancel")}
              </button>
              <button
                type="button"
                className={styles.btnDanger}
                disabled={busy}
                onClick={handleBan}
              >
                {busy ? t("MiningIncidentsPanel.processing") : t("MiningIncidentsPanel.confirmBan")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 誤判解除 */}
      {dismissDialog.open && (
        <div
          className={`${styles.modalOverlay} ${dismissDialog.closing ? styles.modalOverlayOut : ""}`}
          onClick={closeDismiss}
        >
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <span className={styles.modalTitle}>{t("MiningIncidentsPanel.dismissTitle")}</span>
            <p className={styles.modalDesc}>
              {t("MiningIncidentsPanel.dismissMessage", { vmid: dismissDialog.item.vmid })}
            </p>
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
            <div className={styles.modalActions}>
              <button type="button" className={styles.btnSecondary} onClick={closeDismiss}>
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
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
