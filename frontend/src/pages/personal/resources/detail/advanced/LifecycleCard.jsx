/**
 * LifecycleCard — 生命週期
 * 固定顯示到期日、自動關機、閒置偵測與預定刪除，並提供「申請延長到期日」（走管理員審核）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import styles from "../ResourceDetailPage.module.scss";
import MIcon from "../../../../../components/MIcon";
import useDialogPresence from "../../../../../hooks/useDialogPresence";
import { useToast } from "../../../../../hooks/useToast";
import { SpecChangeRequestsService } from "../../../../../services/specChangeRequests";
import { focusInvalidField } from "../../../../../utils/focusField";

const AUTO_STOP_REASON_KEYS = {
  window_grace: "LifecycleCard.reasonWindowGrace",
  practice_quota: "LifecycleCard.reasonPracticeQuota",
  ttl_expired: "LifecycleCard.reasonTtlExpired",
  idle: "LifecycleCard.reasonIdle",
};

function formatDateTime(value, lang) {
  if (!value) return null;
  return new Date(value).toLocaleString(lang, {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function formatDate(value, lang) {
  if (!value) return null;
  return new Date(value).toLocaleDateString(lang, { year: "numeric", month: "2-digit", day: "2-digit" });
}

function tomorrowIso() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

function ExtendModal({ resource, closing, loading, onClose, onSubmit }) {
  const { t } = useTranslation("personal");
  const minDate = resource.expiry_date && resource.expiry_date >= tomorrowIso()
    ? new Date(new Date(resource.expiry_date).getTime() + 86400000).toISOString().slice(0, 10)
    : tomorrowIso();
  const [date, setDate] = useState(minDate);
  const [reason, setReason] = useState("");
  const [reasonInvalid, setReasonInvalid] = useState(false);
  const reasonRef = useRef(null);

  function submit(e) {
    e.preventDefault();
    if (reason.trim().length < 10) {
      setReasonInvalid(true);
      focusInvalidField(reasonRef.current);
      return;
    }
    onSubmit({ requested_expiry_date: date, reason: reason.trim() });
  }

  return (
    <div className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`} onMouseDown={onClose}>
      <form className={styles.modal} onSubmit={submit} onMouseDown={(e) => e.stopPropagation()}>
        <h2 className={styles.modalTitle}>{t("LifecycleCard.extendTitle")}</h2>
        <p className={styles.modalDesc}>{t("LifecycleCard.extendDesc")}</p>
        <div className={styles.field}>
          <label htmlFor="ext-date">{t("LifecycleCard.newExpiryLabel")}</label>
          <input id="ext-date" type="date" min={minDate} value={date} onChange={(e) => setDate(e.target.value)} required />
          <span className={styles.fieldHint}>{t("LifecycleCard.newExpiryHint")}</span>
        </div>
        <div className={`${styles.field} ${reasonInvalid ? styles.fieldInvalid : ""}`}>
          <label htmlFor="ext-reason">{t("LifecycleCard.reasonLabel")}</label>
          <textarea
            id="ext-reason"
            ref={reasonRef}
            rows={4}
            value={reason}
            aria-invalid={reasonInvalid}
            placeholder={t("LifecycleCard.reasonPlaceholder")}
            onChange={(e) => { setReason(e.target.value); setReasonInvalid(false); }}
          />
          <span className={styles.fieldHint}>{t("LifecycleCard.reasonHint")}</span>
        </div>
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
            {t("LifecycleCard.cancel")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading}>
            {loading ? t("LifecycleCard.submitting") : t("LifecycleCard.submitRequest")}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function LifecycleCard({ vmid, resource, canManage, onChanged }) {
  const { t, i18n } = useTranslation("personal");
  const toast = useToast();
  const lang = i18n.language || "zh-TW";
  const [openRequest, setOpenRequest] = useState(null);
  const [showExtend, setShowExtend] = useState(false);
  const [busy, setBusy] = useState(false);
  const presence = useDialogPresence(showExtend);

  const loadRequests = useCallback(async () => {
    try {
      const res = await SpecChangeRequestsService.listMy();
      const pending = (res?.data ?? []).find(
        (r) => r.vmid === vmid && r.change_type === "expiry" && r.status === "pending",
      );
      setOpenRequest(pending ?? null);
    } catch {
      /* 申請進度載入失敗不影響卡片本身 */
    }
  }, [vmid]);

  useEffect(() => {
    loadRequests();
  }, [loadRequests]);

  async function handleSubmit({ requested_expiry_date, reason }) {
    setBusy(true);
    try {
      const created = await SpecChangeRequestsService.create({
        vmid,
        change_type: "expiry",
        reason,
        requested_expiry_date,
      });
      setOpenRequest(created);
      setShowExtend(false);
      toast.success(t("LifecycleCard.requestSubmitted"));
    } catch (err) {
      toast.error(err?.message ?? t("LifecycleCard.submitFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (!openRequest) return;
    setBusy(true);
    try {
      await SpecChangeRequestsService.cancel(openRequest.id);
      setOpenRequest(null);
      toast.success(t("LifecycleCard.requestCancelled"));
      onChanged?.();
    } catch (err) {
      toast.error(err?.message ?? t("LifecycleCard.cancelFailed"));
    } finally {
      setBusy(false);
    }
  }

  const canExtend = canManage && resource?.can_extend !== false && resource?.allocation_scope !== "teaching_class";
  const reasonKey = resource?.auto_stop_reason ? AUTO_STOP_REASON_KEYS[resource.auto_stop_reason] : null;

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <div>
          <h2 className={styles.cardTitle}>
            <MIcon name="schedule" size={18} />
            {t("LifecycleCard.title")}
          </h2>
          <p className={styles.cardDesc}>{t("LifecycleCard.desc")}</p>
        </div>
        {canExtend && (
          <div className={styles.headerActions}>
            <button
              type="button"
              className={styles.btnPrimary}
              disabled={Boolean(openRequest) || busy}
              title={openRequest ? t("LifecycleCard.requestPendingHint") : ""}
              onClick={() => setShowExtend(true)}
            >
              <MIcon name="more_time" size={16} />
              {t("LifecycleCard.requestExtension")}
            </button>
          </div>
        )}
      </div>
      <div className={styles.cardBody}>
        {openRequest && (
          <div className={styles.noteBox}>
            <span className={styles.noteBoxTitle}>
              <MIcon name="hourglass_top" size={14} />
              {t("LifecycleCard.pendingTitle")}
            </span>
            <span className={styles.noteBoxLine}>
              {t("LifecycleCard.pendingLine", {
                from: openRequest.current_expiry_date ?? t("LifecycleCard.unlimited"),
                to: openRequest.requested_expiry_date,
              })}
            </span>
            <div className={styles.noteActions}>
              <button type="button" className={styles.btnDangerOutline} disabled={busy} onClick={handleCancel}>
                {t("LifecycleCard.cancelRequest")}
              </button>
            </div>
          </div>
        )}

        <div className={styles.factGrid}>
          <div className={styles.fact}>
            <span className={styles.factLabel}>{t("LifecycleCard.expiryLabel")}</span>
            <span className={styles.factValue}>
              {formatDate(resource?.expiry_date, lang) ?? t("LifecycleCard.unlimited")}
            </span>
            {resource?.expiry_date && <span className={styles.mutedText}>{t("LifecycleCard.expiryHint")}</span>}
          </div>
          <div className={styles.fact}>
            <span className={styles.factLabel}>{t("LifecycleCard.autoStopLabel")}</span>
            <span className={styles.factValue}>
              {formatDateTime(resource?.auto_stop_at, lang) ?? t("LifecycleCard.none")}
            </span>
            {reasonKey && <span className={styles.mutedText}>{t(reasonKey)}</span>}
          </div>
          <div className={styles.fact}>
            <span className={styles.factLabel}>{t("LifecycleCard.idleLabel")}</span>
            <span className={styles.factValue}>
              {formatDateTime(resource?.idle_since, lang) ?? t("LifecycleCard.notIdle")}
            </span>
            {resource?.idle_since && <span className={styles.mutedText}>{t("LifecycleCard.idleHint")}</span>}
          </div>
          <div className={styles.fact}>
            <span className={styles.factLabel}>{t("LifecycleCard.deletionLabel")}</span>
            <span className={styles.factValue}>
              {formatDateTime(resource?.scheduled_deletion_at, lang) ?? t("LifecycleCard.none")}
            </span>
            {resource?.scheduled_deletion_at && <span className={styles.mutedText}>{t("LifecycleCard.deletionHint")}</span>}
          </div>
        </div>
        <p className={styles.hintLine}>
          <MIcon name="info" size={14} />
          {t("LifecycleCard.policyNote")}
        </p>
      </div>

      {/* 卡片有 overflow:hidden + backdrop-filter，會把 position:fixed 的 modal 困在卡片裡，
          所以 portal 到 body 讓它覆蓋整個頁面。 */}
      {presence.open &&
        createPortal(
          <ExtendModal
            resource={resource ?? {}}
            closing={presence.closing}
            loading={busy}
            onClose={() => setShowExtend(false)}
            onSubmit={handleSubmit}
          />,
          document.body,
        )}
    </div>
  );
}
