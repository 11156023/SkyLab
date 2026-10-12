import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import PasswordInput from "../../components/PasswordInput/PasswordInput";
import PasswordRules from "../../components/PasswordRules/PasswordRules";
import { AccountService } from "../../services/account";
import { isPasswordStrong } from "../../utils/passwordPolicy";
import PageShell from "./PageShell";
import styles from "./LoginPage.module.scss";

export default function VerifySignupPage() {
  const { t } = useTranslation("login");
  const [token, setToken] = useState(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [status, setStatus] = useState("ready");
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    const url = new URL(window.location.href);
    setToken(new URLSearchParams(url.hash.slice(1)).get("token") || "");
    url.hash = "";
    window.history.replaceState(window.history.state, "", url);
  }, []);

  const submit = async (event) => {
    event.preventDefault();
    setSubmitted(true);
    if (!isPasswordStrong(password)) {
      setError(t("PasswordRules.notMet", { ns: "common" }));
      return;
    }
    if (password !== confirm) {
      setError(t("LoginPage.verifySignupMismatch"));
      return;
    }
    setStatus("checking");
    setError("");
    try {
      const result = await AccountService.confirmSignup(token, password);
      setStatus(result.message === "pending_review" ? "pending_review" : "success");
    } catch (err) {
      setError(err?.message || t("LoginPage.verifySignupFailed"));
      setStatus("ready");
    }
  };

  return (
    <PageShell>
      <h1 className={styles.title}>{t("LoginPage.verifySignupTitle")}</h1>
      {status === "success" || status === "pending_review" ? (
        <p className={styles.successBox} role="status">
          {t(status === "pending_review" ? "LoginPage.verifySignupPendingReview" : "LoginPage.verifySignupSuccess")}
        </p>
      ) : token ? (
        <form className={styles.form} onSubmit={submit}>
          <p className={styles.subtitle}>{t("LoginPage.verifySignupSetPassword")}</p>
          <div className={styles.field}>
            <label htmlFor="signup-password">{t("LoginPage.passwordLabel")}</label>
            <PasswordInput id="signup-password" value={password}
              onChange={(event) => setPassword(event.target.value)} required />
            <PasswordRules password={password} invalid={submitted} />
          </div>
          <div className={styles.field}>
            <label htmlFor="signup-confirm">{t("LoginPage.confirmPasswordLabel")}</label>
            <PasswordInput id="signup-confirm" value={confirm}
              onChange={(event) => setConfirm(event.target.value)} required />
          </div>
          {error && <p className={styles.error} role="alert">{error}</p>}
          <button className={styles.btn} type="submit" disabled={status === "checking"}>
            {status === "checking" ? t("LoginPage.verifySignupChecking") : t("LoginPage.verifySignupActivate")}
          </button>
        </form>
      ) : token === null ? (
        <p role="status">{t("LoginPage.verifySignupChecking")}</p>
      ) : (
        <p className={styles.error} role="alert">{t("LoginPage.verifySignupFailed")}</p>
      )}
      <a className={styles.backBtn} href="/login">{t("LoginPage.backToLogin")}</a>
    </PageShell>
  );
}
