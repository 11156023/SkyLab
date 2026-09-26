/**
 * SessionWarningDialog
 * 練習階段警告對話框，依 warn_reason 分兩種：
 * - auto_stop：VM 即將自動關機（課程時段緩衝或練習額度）。
 *   練習額度與課堂時段型都可由機器擁有者自助延長。
 * - expiry：VM 即將到期停用。無法自助延長，須向管理員申請。
 */
import { useState } from "react";
import { createPortal } from "react-dom";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { ResourcesService } from "../../services/resources";
import useDialogPresence from "../../hooks/useDialogPresence";
import styles from "./SessionWarningDialog.module.scss";

export default function SessionWarningDialog({ status, onClose, onDismissPermanent }) {
  const { t } = useTranslation("common");
  const [doNotShow, setDoNotShow] = useState(false);
  const [extending, setExtending] = useState(false);
  // 關閉時保留最後一筆狀態，先播放離場動畫再卸載
  const presence = useDialogPresence(status);
  const shown = presence.item;

  if (!presence.open) return null;

  const isExpiry = shown.warn_reason === "expiry";

  const handleClose = () => {
    if (doNotShow) onDismissPermanent();
    else onClose();
    setDoNotShow(false);
  };

  const handleExtend = async () => {
    setExtending(true);
    try {
      const result = await ResourcesService.extendSession(shown.vmid);
      toast.success(t("SessionWarningDialog.extended", { hours: result.extended_minutes / 60 }));
      onClose();
    } catch (e) {
      toast.error(e?.message ?? t("SessionWarningDialog.extendFailed"));
    } finally {
      setExtending(false);
    }
  };

  return createPortal(
    <div
      className={`${styles.overlay} ${presence.closing ? styles.overlayOut : ""}`}
      onClick={handleClose}
    >
      <div
        className={styles.dialog}
        role="alertdialog"
        aria-modal="true"
        aria-label={isExpiry ? t("SessionWarningDialog.expiryTitle") : t("SessionWarningDialog.autoStopTitle")}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.title}>
          <span className={isExpiry ? styles.iconExpiry : styles.iconAutoStop}>
            <MIcon name={isExpiry ? "event_busy" : "schedule"} size={20} />
          </span>
          {isExpiry ? t("SessionWarningDialog.expiryTitle") : t("SessionWarningDialog.autoStopTitle")}
        </div>

        <p className={styles.desc}>
          {isExpiry ? (
            <>
              {t("SessionWarningDialog.expiryPrefix", { vmid: shown.vmid })}
              <strong>{t("SessionWarningDialog.expiryHours", { hours: shown.hours_until_expiry ?? "?" })}</strong>
              {t("SessionWarningDialog.expirySuffix")}
            </>
          ) : (
            <>
              {t("SessionWarningDialog.autoStopPrefix", { vmid: shown.vmid })}
              <strong>{t("SessionWarningDialog.autoStopMinutes", { minutes: shown.minutes_until_stop ?? "?" })}</strong>
              {t("SessionWarningDialog.autoStopSuffix")}
            </>
          )}
        </p>

        <label className={styles.doNotShow}>
          <input
            type="checkbox"
            checked={doNotShow}
            onChange={(e) => setDoNotShow(e.target.checked)}
          />
          <span>{t("SessionWarningDialog.doNotShowAgain")}</span>
        </label>

        <div className={styles.actions}>
          <button type="button" className={styles.btnSecondary} onClick={handleClose}>
            {isExpiry ? t("SessionWarningDialog.gotIt") : t("SessionWarningDialog.later")}
          </button>
          {!isExpiry && (
            <button
              type="button"
              className={styles.btnPrimary}
              disabled={!shown.can_extend || extending}
              onClick={handleExtend}
            >
              <MIcon name="autorenew" size={16} spin={extending} />
              {t("SessionWarningDialog.extendUsageTime")}
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
