import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./settings.module.scss";
import MIcon from "../../../components/MIcon";
import EmptyState from "../../../components/EmptyState/EmptyState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { AuthPolicyService } from "../../../services/authPolicy";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";

/**
 * 登入安全（系統管理 → 登入安全）：全站登入政策。
 * 目前只有一項：強制所有使用者啟用兩步驟驗證。開關即時生效，不需另外儲存。
 */
function SecurityForm() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const { user, updateUser } = useAuth();
  const [policy, setPolicy] = useState(null);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    AuthPolicyService.get()
      .then((data) => {
        if (!cancelled) setPolicy(data);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err?.message ?? t("SecurityPage.loadFailed"));
      });
    return () => {
      cancelled = true;
    };
  }, [t, reloadKey]);

  const retryLoad = useCallback(() => setReloadKey((n) => n + 1), []);

  async function toggleTotpRequired(next) {
    setSaving(true);
    try {
      const updated = await AuthPolicyService.update({ totp_required: next });
      setPolicy(updated);
      /* 管理員自己也適用：更新本機的 user 旗標，帳號設定頁的停用按鈕才會同步鎖住／解鎖 */
      updateUser({
        totp_policy_required: updated.totp_required,
        totp_setup_required: updated.totp_required && !user?.totp_enabled,
      });
      toast.success(
        updated.totp_required
          ? t("SecurityPage.totpRequiredOnToast")
          : t("SecurityPage.totpRequiredOffToast"),
      );
    } catch (err) {
      toast.error(err?.message ?? t("SecurityPage.saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  if (!policy && loadError) {
    return (
      <EmptyState
        icon="error_outline"
        title={t("SecurityPage.loadFailed")}
        description={loadError}
        action={
          <button type="button" className={styles.btnSecondary} onClick={retryLoad}>
            <MIcon name="refresh" size={16} />
            {t("SecurityPage.retry")}
          </button>
        }
      />
    );
  }
  if (!policy) return <LoadingState text={t("SecurityPage.loading")} />;

  return (
    <div className={styles.panelStack}>
      <div className={styles.card}>
        <div className={styles.cardHead}>
          <h2 className={styles.cardTitle}>{t("SecurityPage.totpSectionTitle")}</h2>
          <span className={`${styles.badge} ${policy.totp_required ? styles.badge_success : styles.badge_muted}`}>
            {policy.totp_required ? t("SecurityPage.badgeEnforced") : t("SecurityPage.badgeOptional")}
          </span>
        </div>
        <p className={styles.cardDesc}>{t("SecurityPage.totpSectionDesc")}</p>
        <label className={styles.checkRow}>
          <input
            type="checkbox"
            checked={Boolean(policy.totp_required)}
            disabled={saving}
            onChange={(e) => toggleTotpRequired(e.target.checked)}
          />
          <span>{t("SecurityPage.totpRequiredLabel")}</span>
        </label>
        <p className={styles.cardHint}>{t("SecurityPage.totpRequiredHint")}</p>
        {policy.totp_required && !user?.totp_enabled && (
          <p className={styles.cardHint}>
            <MIcon name="warning" size={14} /> {t("SecurityPage.selfNotEnrolledHint")}
          </p>
        )}
      </div>
    </div>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function SecurityPage() {
  const { t } = useTranslation("system");
  return (
    <div className={styles.page}>
      <PageHeader title={t("SecurityPage.title")} />
      <div className={styles.content}>
        <SecurityForm />
      </div>
    </div>
  );
}
