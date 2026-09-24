import { useTranslation } from "react-i18next";
import MIcon from "../../components/MIcon";
import TotpEnrollment from "../../components/TotpEnrollment/TotpEnrollment";
import { useAuth } from "../../contexts/AuthContext";
import { useToast } from "../../hooks/useToast";
import styles from "./LoginPage.module.scss";

/**
 * 強制綁定兩步驟驗證（管理員在「登入安全」開啟強制後，尚未綁定的使用者登入即到這裡）。
 * 沿用登入頁的外框樣式；完成綁定後 updateUser 清掉旗標，App 就會切回正常路由。
 */
export default function TotpEnrollPage() {
  const { t } = useTranslation("login");
  const { user, updateUser, logout } = useAuth();
  const toast = useToast();

  function handleConfirmed() {
    updateUser({ totp_enabled: true, totp_setup_required: false });
    toast.success(t("TotpEnrollPage.enabledToast"));
  }

  return (
    <div className={styles.page}>
      <div className={styles.glow} aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div className={`${styles.card} ${styles.cardWide}`}>
        <button type="button" className={styles.backBtn} onClick={logout}>
          <MIcon name="logout" size={18} />
          {t("TotpEnrollPage.logout")}
        </button>
        <h1 className={styles.title}>{t("TotpEnrollPage.title")}</h1>
        <p className={styles.subtitle}>
          {t("TotpEnrollPage.subtitle", { email: user?.email ?? "" })}
        </p>
        <div className={styles.deviceNotice}>
          <MIcon name="security" size={20} />
          <span>{t("TotpEnrollPage.notice")}</span>
        </div>
        <div className={styles.enrollBody}>
          <TotpEnrollment
            onConfirmed={handleConfirmed}
            confirmLabel={t("TotpEnrollPage.confirm")}
          />
        </div>
      </div>
    </div>
  );
}
