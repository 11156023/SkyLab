import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./GatewayPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import { GatewayService } from "../../../services/gateway";
import {
  CERT_WARN_DAYS,
  SUGGESTED_CERT_PATH,
  SUGGESTED_KEY_PATH,
  daysUntil,
  isCertificateFormDirty,
  toCertificateForm,
  toCertificatePayload,
  validateCertificateForm,
} from "./certificateForm";

function formatDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString();
}

/* ── Gateway 上的憑證檢查結果 ───────────────────────── */
function CertificateStatusCard({ status, error, loading, onRefresh }) {
  const { t } = useTranslation("system");

  const warnings = [];
  const details = [];
  if (status && !status.configured) {
    warnings.push(t("GatewayPage.certWarnNotConfigured"));
  } else if (status) {
    if (status.cert_readable === false || status.key_readable === false) warnings.push(t("GatewayPage.certWarnUnreadable"));
    else if (status.cert_valid === false || status.key_valid === false) warnings.push(t("GatewayPage.certWarnInvalid"));
    else if (status.key_matches === false) warnings.push(t("GatewayPage.certWarnKeyMismatch"));

    const days = daysUntil(status.expires_at);
    if (days !== null && days < 0) warnings.push(t("GatewayPage.certWarnExpired"));
    else if (days !== null && days < CERT_WARN_DAYS) warnings.push(t("GatewayPage.certWarnExpiring", { days }));

    const date = formatDate(status.expires_at);
    details.push(
      [t("GatewayPage.certStatusExpires"), date ? t("GatewayPage.certStatusExpiresValue", { date, days: Math.max(days ?? 0, 0) }) : "—"],
      [t("GatewayPage.certStatusKey"), status.key_matches === true ? t("GatewayPage.certStatusKeyOk") : t("GatewayPage.certStatusKeyBad")],
      [t("GatewayPage.certStatusNames"), status.dns_names?.length ? status.dns_names.join(", ") : "—"],
      [t("GatewayPage.certStatusCovered"), status.covered_domains?.length ? status.covered_domains.join(", ") : "—"],
    );
  }
  if (status?.uncovered_domains?.length) {
    warnings.push(t("GatewayPage.certWarnUncovered", { domains: status.uncovered_domains.join(", ") }));
  }

  return (
    <div className={styles.card}>
      <div className={styles.cardHead}>
        <h2 className={styles.cardTitle}>{t("GatewayPage.certStatusTitle")}</h2>
        <button type="button" className={styles.btnSecondary} onClick={onRefresh} disabled={loading}>
          <MIcon name="refresh" size={16} spin={loading} />
          {t("GatewayPage.installRefresh")}
        </button>
      </div>

      {!status && loading && <LoadingState />}

      {!status && !loading && error && (
        <div className={styles.warningNote}>
          <MIcon name="warning" size={18} />
          <div className={styles.noteText}>
            <strong>{t("GatewayPage.certStatusError")}</strong>
            <span>{error}</span>
          </div>
        </div>
      )}

      {status && (
        <>
          {details.length > 0 && (
            <dl className={styles.detailGrid}>
              {details.map(([label, value]) => (
                <div className={styles.detailItem} key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
          )}
          {warnings.map((text) => (
            <div className={styles.warningNote} key={text}>
              <MIcon name="warning" size={18} />
              {text}
            </div>
          ))}
          {warnings.length === 0 && (
            <div className={styles.successNote}>
              <MIcon name="check_circle" size={18} />
              {t("GatewayPage.certStatusAllGood")}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ── HTTPS 憑證 Tab ─────────────────────────────────── */
export default function GatewayCertificateTab({ gatewayReady, onGoToConnection, onDirtyChange }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const [config, setConfig] = useState(null);
  const [form, setForm] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const statusInFlightRef = useRef(false);

  const refreshStatus = useCallback(async () => {
    if (statusInFlightRef.current) return;
    statusInFlightRef.current = true;
    setStatusLoading(true);
    try {
      setStatus(await GatewayService.getCertificateStatus());
      setStatusError(null);
    } catch (err) {
      setStatus(null);
      setStatusError(err?.message ?? t("Error.generic", { ns: "common" }));
    } finally {
      statusInFlightRef.current = false;
      setStatusLoading(false);
    }
  }, [t]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await GatewayService.getCertificate();
      setConfig(next);
      setForm(toCertificateForm(next));
      setLoadFailed(false);
      refreshStatus();
    } catch (err) {
      setLoadFailed(true);
      toast.error(err?.message ?? t("GatewayPage.toastCertLoadFailed"));
    } finally {
      setLoading(false);
    }
  }, [refreshStatus, t, toast]);

  useEffect(() => {
    if (gatewayReady) load();
    else setLoading(false);
  }, [gatewayReady, load]);

  const dirty = Boolean(form && config && isCertificateFormDirty(form, config));

  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  function setField(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  /* 表單沒改時按「重新套用」：同一路徑換了新檔案，重新檢查並 reload nginx */
  async function handleSave(e) {
    e?.preventDefault();
    if (validateCertificateForm(form)) return;
    const payload = toCertificatePayload(form);
    const clearing = config.configured && !payload.ssl_certificate_path;
    if (clearing) {
      const ok = await confirm({
        title: t("GatewayPage.certClearConfirmTitle"),
        message: t("GatewayPage.certClearConfirmMessage"),
        confirmText: t("GatewayPage.certClearConfirmButton"),
        danger: true,
      });
      if (!ok) return;
    }
    setSaving(true);
    try {
      const next = await GatewayService.updateCertificate(payload);
      setConfig(next);
      setForm(toCertificateForm(next));
      toast.success(clearing ? t("GatewayPage.toastCertCleared") : t("GatewayPage.toastCertSaved"));
      refreshStatus();
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastCertSaveFailed"));
    } finally {
      setSaving(false);
    }
  }

  if (!gatewayReady) {
    return <EmptyState icon="lock" title={t("GatewayPage.emptyNotConfigured")} action={<button type="button" className={styles.btnPrimary} onClick={onGoToConnection}><MIcon name="settings_ethernet" size={16} />{t("GatewayPage.goToConnection")}</button>} />;
  }

  if (loading && !config) {
    return <LoadingState text={t("GatewayPage.certLoading")} />;
  }

  if (!config || !form) {
    return loadFailed ? <ErrorState onRetry={load} /> : null;
  }

  const formError = validateCertificateForm(form);
  const canSave = !saving && !formError && dirty;
  const canReapply = !saving && !dirty && config.configured;

  return (
    <div className={styles.panelStack}>
      <form className={styles.card} onSubmit={handleSave}>
        <div className={styles.cardHead}>
          <div className={styles.statusRow}>
            <h2 className={styles.cardTitle}>{t("GatewayPage.certTitle")}</h2>
            <span className={`${styles.badge} ${config.configured ? styles.badge_success : styles.badge_muted}`}>
              <MIcon name={config.configured ? "check_circle" : "radio_button_unchecked"} size={13} />
              {config.configured ? t("GatewayPage.certBadgeConfigured") : t("GatewayPage.certBadgeNotConfigured")}
            </span>
          </div>
          <div className={styles.cardHeadActions}>
            {canReapply && (
              <button type="button" className={styles.btnSecondary} onClick={() => handleSave()}>
                <MIcon name="sync" size={16} />
                {t("GatewayPage.certReapply")}
              </button>
            )}
            <button type="submit" className={styles.btnPrimary} disabled={!canSave}>
              {saving ? t("GatewayPage.certSaving") : t("GatewayPage.certSave")}
            </button>
          </div>
        </div>

        <div className={styles.installGrid}>
          <label className={styles.field}>
            <span>{t("GatewayPage.certPath")}</span>
            <input
              value={form.ssl_certificate_path}
              onChange={(e) => setField("ssl_certificate_path", e.target.value)}
              placeholder={SUGGESTED_CERT_PATH}
              spellCheck={false}
              disabled={saving}
            />
          </label>
          <label className={styles.field}>
            <span>{t("GatewayPage.certKeyPath")}</span>
            <input
              value={form.ssl_certificate_key_path}
              onChange={(e) => setField("ssl_certificate_key_path", e.target.value)}
              placeholder={SUGGESTED_KEY_PATH}
              spellCheck={false}
              disabled={saving}
            />
          </label>
        </div>

        {formError && (
          <div className={styles.warningNote}>
            <MIcon name="error_outline" size={18} />
            {t(`GatewayPage.${formError}`)}
          </div>
        )}

        <div className={styles.securityNote}>
          <MIcon name="info" size={20} />
          <div>
            <strong>{t("GatewayPage.certHowToTitle")}</strong>
            <span>{t("GatewayPage.certHowToHint")}</span>
          </div>
        </div>
      </form>

      <CertificateStatusCard
        status={status}
        error={statusError}
        loading={statusLoading}
        onRefresh={refreshStatus}
      />
    </div>
  );
}
