import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../../components/MIcon";
import { useAuth } from "../../contexts/AuthContext";
import { apiPost } from "../../services/api";
import { getLoginMethods } from "../../services/auth";
import styles from "./LoginPage.module.scss";

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? "";
const ENABLE_SIGNUP = import.meta.env.ENABLE_SIGNUP !== "false";
let googleIdentityScriptPromise;

function loadGoogleIdentityScript() {
  if (typeof window === "undefined")
    return Promise.reject(new Error("Browser unavailable"));
  if (window.google?.accounts?.id) return Promise.resolve();

  if (!googleIdentityScriptPromise) {
    googleIdentityScriptPromise = new Promise((resolve, reject) => {
      const existingScript = document.getElementById(
        "google-identity-services",
      );
      if (existingScript) {
        existingScript.addEventListener("load", resolve, { once: true });
        existingScript.addEventListener("error", reject, { once: true });
        return;
      }

      const script = document.createElement("script");
      script.id = "google-identity-services";
      script.src = "https://accounts.google.com/gsi/client";
      script.async = true;
      script.defer = true;
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild(script);
    });
  }

  return googleIdentityScriptPromise;
}

function readResetTokenFromUrl() {
  if (typeof window === "undefined") return "";
  const params = new URLSearchParams(window.location.search);
  return params.get("token") ?? "";
}

function readDeviceCodeFromUrl() {
  if (typeof window === "undefined") return "";
  const params = new URLSearchParams(window.location.search);
  return params.get("device_code") ?? "";
}

function clearResetTokenFromUrl() {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  url.searchParams.delete("token");
  url.pathname = "/";
  window.history.replaceState(null, "", url.toString());
}

function clearDeviceCodeFromUrl() {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  url.searchParams.delete("device_code");
  window.history.replaceState(null, "", url.toString());
}

/* ─── 共用元件 ─────────────────────────────────────────── */

function PasswordField({ id, label, value, onChange, disabled, placeholder }) {
  const { t } = useTranslation("login");
  const [show, setShow] = useState(false);
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{label}</label>
      <div className={styles.passwordWrap}>
        <input
          id={id}
          type={show ? "text" : "password"}
          placeholder={placeholder ?? t("LoginPage.passwordPlaceholder")}
          value={value}
          onChange={onChange}
          disabled={disabled}
          required
        />
        <button
          type="button"
          className={styles.eyeBtn}
          onClick={() => setShow((v) => !v)}
          tabIndex={-1}
          aria-label={
            show ? t("LoginPage.passwordHide") : t("LoginPage.passwordShow")
          }
        >
          <MIcon name={show ? "visibility_off" : "visibility"} />
        </button>
      </div>
    </div>
  );
}

function GoogleSignInButton({ onCredential, onError }) {
  const { t } = useTranslation("login");
  const buttonRef = useRef(null);

  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return;

    let cancelled = false;
    loadGoogleIdentityScript()
      .then(() => {
        if (cancelled || !buttonRef.current) return;

        window.google.accounts.id.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: (response) => {
            const credential = response?.credential;
            if (!credential) {
              onError(t("LoginPage.googleNoCredential"));
              return;
            }
            onCredential(credential);
          },
          ux_mode: "popup",
        });

        buttonRef.current.innerHTML = "";
        window.google.accounts.id.renderButton(buttonRef.current, {
          theme: "outline",
          size: "large",
          type: "standard",
          text: "signin_with",
          shape: "rectangular",
          logo_alignment: "left",
          width: Math.min(buttonRef.current.clientWidth || 360, 400),
        });
      })
      .catch(() => {
        if (!cancelled) onError(t("LoginPage.googleLoadFailed"));
      });

    return () => {
      cancelled = true;
    };
  }, [onCredential, onError, t]);

  if (!GOOGLE_CLIENT_ID) return null;
  return (
    <div
      ref={buttonRef}
      className={styles.googleButton}
      aria-label={t("LoginPage.googleSignInAriaLabel")}
    />
  );
}

function formatGoogleLoginError(err, t) {
  const message = err?.message ?? "";
  if (message === "Google account is not registered") {
    return t("LoginPage.googleErrorNotRegistered");
  }
  if (message === "Inactive user") {
    return t("LoginPage.googleErrorInactiveUser");
  }
  if (message === "Invalid Google token audience") {
    return t("LoginPage.googleErrorAudienceMismatch");
  }
  if (message === "Invalid Google token") {
    return t("LoginPage.googleErrorInvalidToken");
  }
  return message || t("LoginPage.googleErrorGeneric");
}

/* ─── 登入 ──────────────────────────────────────────────── */

function LoginView({ onForgot, onRegister, deviceApproval = false }) {
  const { t } = useTranslation("login");
  const { login, googleLogin, ldapLogin } = useAuth();
  const [mode, setMode] = useState("password"); // "password" | "ldap"
  const [ldapEnabled, setLdapEnabled] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [ldapUsername, setLdapUsername] = useState("");
  const [ldapPassword, setLdapPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);

  // 依後端啟用的登入方式決定是否顯示「校園帳號」分頁（公開端點；取不到就只顯示 Email）
  useEffect(() => {
    let cancelled = false;
    getLoginMethods()
      .then((methods) => {
        if (!cancelled) setLdapEnabled(Boolean(methods?.ldap));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const switchMode = (next) => {
    setMode(next);
    setError("");
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
    } catch (err) {
      setError(err?.message ?? t("LoginPage.loginErrorDefault"));
    } finally {
      setLoading(false);
    }
  };

  const handleLdapSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await ldapLogin(ldapUsername, ldapPassword);
    } catch (err) {
      setError(err?.message ?? t("LoginPage.ldapLoginErrorDefault"));
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleCredential = useCallback(
    async (credential) => {
      setError("");
      setGoogleLoading(true);
      try {
        await googleLogin(credential);
      } catch (err) {
        setError(formatGoogleLoginError(err, t));
      } finally {
        setGoogleLoading(false);
      }
    },
    [googleLogin, t],
  );

  const handleGoogleError = useCallback((message) => {
    setError(message);
  }, []);

  const passwordForm = (
    <form className={styles.form} onSubmit={handleSubmit}>
      <div className={styles.field}>
        <label htmlFor="username">{t("LoginPage.usernameLabel")}</label>
        <input
          id="username"
          type="text"
          placeholder={t("LoginPage.usernamePlaceholder")}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          disabled={loading}
          required
        />
      </div>

      <PasswordField
        id="password"
        label={t("LoginPage.passwordLabel")}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        disabled={loading}
      />

      <button
        type="button"
        className={styles.linkRight}
        onClick={onForgot}
        tabIndex={0}
      >
        {t("LoginPage.forgotPasswordLink")}
      </button>

      {error && <p className={styles.error}>{error}</p>}

      <button type="submit" className={styles.btn} disabled={loading}>
        {loading ? t("LoginPage.loggingIn") : t("LoginPage.login")}
      </button>
    </form>
  );

  const ldapForm = (
    <form className={styles.form} onSubmit={handleLdapSubmit}>
      <div className={styles.field}>
        <label htmlFor="ldap-username">{t("LoginPage.campusAccount")}</label>
        <input
          id="ldap-username"
          type="text"
          placeholder={t("LoginPage.campusAccountPlaceholder")}
          autoComplete="username"
          value={ldapUsername}
          onChange={(e) => setLdapUsername(e.target.value)}
          disabled={loading}
          required
        />
      </div>

      <PasswordField
        id="ldap-password"
        label={t("LoginPage.passwordLabel")}
        value={ldapPassword}
        onChange={(e) => setLdapPassword(e.target.value)}
        disabled={loading}
      />

      {error && <p className={styles.error}>{error}</p>}

      <button type="submit" className={styles.btn} disabled={loading}>
        {loading ? t("LoginPage.loggingIn") : t("LoginPage.login")}
      </button>
    </form>
  );

  return (
    <>
      <h1 className={styles.title}>{t("LoginPage.appTitle")}</h1>
      <p className={styles.subtitle}>
        {deviceApproval
          ? t("LoginPage.subtitleDeviceApproval")
          : t("LoginPage.subtitleDefault")}
      </p>

      {deviceApproval && (
        <div className={styles.deviceNotice}>
          <MIcon name="devices" size={22} />
          <span>{t("LoginPage.deviceNotice")}</span>
        </div>
      )}

      {ldapEnabled && (
        <div
          className={styles.loginTabs}
          role="tablist"
          aria-label={t("LoginPage.loginMethodsAriaLabel")}
        >
          <button
            type="button"
            role="tab"
            aria-selected={mode === "password"}
            className={`${styles.loginTab} ${mode === "password" ? styles.loginTabActive : ""}`}
            onClick={() => switchMode("password")}
          >
            {t("LoginPage.emailTab")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "ldap"}
            className={`${styles.loginTab} ${mode === "ldap" ? styles.loginTabActive : ""}`}
            onClick={() => switchMode("ldap")}
          >
            {t("LoginPage.campusAccount")}
          </button>
        </div>
      )}

      {mode === "ldap" && ldapEnabled ? ldapForm : passwordForm}

      {GOOGLE_CLIENT_ID && (
        <div className={styles.oauthArea}>
          <div className={styles.divider}>
            <span>{t("LoginPage.orDivider")}</span>
          </div>
          <div className={googleLoading ? styles.googleBusy : undefined}>
            <GoogleSignInButton
              onCredential={handleGoogleCredential}
              onError={handleGoogleError}
            />
          </div>
          {googleLoading && (
            <p className={styles.oauthHint}>{t("LoginPage.googleLoggingIn")}</p>
          )}
        </div>
      )}

      {ENABLE_SIGNUP && (
        <p className={styles.footerText}>
          {t("LoginPage.noAccountYet")}{" "}
          <button type="button" className={styles.link} onClick={onRegister}>
            {t("LoginPage.registerNow")}
          </button>
        </p>
      )}
    </>
  );
}

function DeviceApprovalView({ status, error, user, onApprove, onDecline }) {
  const { t } = useTranslation("login");

  // 同意頁：授權必須由使用者明確按下，不可自動核准（防止釣魚連結）
  if (status === "idle") {
    return (
      <>
        <h1 className={styles.title}>{t("LoginPage.deviceConsentTitle")}</h1>
        <p className={styles.subtitle}>
          {t("LoginPage.deviceConsentSubtitle")}
        </p>
        <div className={styles.deviceNotice}>
          <MIcon name="devices" size={20} />
          <span>
            {t("LoginPage.deviceConsentIdentity", {
              email: user?.email ?? t("LoginPage.deviceConsentCurrentAccount"),
            })}
          </span>
        </div>
        <p className={styles.deviceHelp}>{t("LoginPage.deviceConsentHelp")}</p>
        <button type="button" className={styles.btn} onClick={onApprove}>
          {t("LoginPage.deviceConsentApprove")}
        </button>
        <button type="button" className={styles.backBtn} onClick={onDecline}>
          {t("LoginPage.deviceConsentDecline")}
        </button>
      </>
    );
  }

  if (status === "approved") {
    return (
      <>
        <h1 className={styles.title}>{t("LoginPage.deviceApprovedTitle")}</h1>
        <p className={styles.subtitle}>
          {t("LoginPage.deviceApprovedSubtitle")}
        </p>
        <div className={styles.successBox}>
          <MIcon name="check_circle" size={40} />
          <p>{t("LoginPage.deviceApprovedMessage")}</p>
        </div>
      </>
    );
  }

  if (status === "error") {
    return (
      <>
        <h1 className={styles.title}>{t("LoginPage.deviceErrorTitle")}</h1>
        <p className={styles.subtitle}>{t("LoginPage.deviceErrorSubtitle")}</p>
        <p className={styles.error}>{error}</p>
        <p className={styles.deviceHelp}>{t("LoginPage.deviceErrorHelp")}</p>
        <a className={styles.btnLink} href="/dashboard">
          {t("LoginPage.backToSkyLab")}
        </a>
      </>
    );
  }

  return (
    <>
      <h1 className={styles.title}>{t("LoginPage.deviceConnectingTitle")}</h1>
      <p className={styles.subtitle}>
        {t("LoginPage.deviceConnectingSubtitle")}
      </p>
      <div className={styles.deviceProgress} aria-live="polite">
        <MIcon name="sync" size={40} className={styles.spin} />
      </div>
    </>
  );
}

/* ─── 忘記密碼 ──────────────────────────────────────────── */

function ForgotView({ onBack }) {
  const { t } = useTranslation("login");
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await apiPost(
        `/api/v1/password-recovery/${encodeURIComponent(email)}`,
        null,
      );
      setSuccess(true);
    } catch (err) {
      setError(err?.message ?? t("LoginPage.forgotPasswordErrorDefault"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <button type="button" className={styles.backBtn} onClick={onBack}>
        <MIcon name="arrow_back" size={18} />
        <span>{t("LoginPage.backToLogin")}</span>
      </button>

      <h1 className={styles.title}>{t("LoginPage.forgotPasswordTitle")}</h1>
      <p className={styles.subtitle}>
        {t("LoginPage.forgotPasswordSubtitle")}
      </p>

      {success ? (
        <div className={styles.successBox}>
          <MIcon name="mark_email_read" size={32} />
          <p>{t("LoginPage.resetLinkSent")}</p>
        </div>
      ) : (
        <form className={styles.form} onSubmit={handleSubmit}>
          <div className={styles.field}>
            <label htmlFor="forgot-email">{t("LoginPage.emailLabel")}</label>
            <input
              id="forgot-email"
              type="email"
              placeholder={t("LoginPage.emailPlaceholderExample")}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={loading}
              required
            />
          </div>

          {error && <p className={styles.error}>{error}</p>}

          <button type="submit" className={styles.btn} disabled={loading}>
            {loading ? t("LoginPage.sending") : t("LoginPage.sendResetLink")}
          </button>
        </form>
      )}
    </>
  );
}

/* ─── 重設密碼 ──────────────────────────────────────────── */

function ResetView({ token, onDone }) {
  const { t } = useTranslation("login");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");

    if (password.length < 8) {
      setError(t("LoginPage.passwordMinLength"));
      return;
    }
    if (password !== confirm) {
      setError(t("LoginPage.passwordMismatch"));
      return;
    }

    setLoading(true);
    try {
      await apiPost("/api/v1/reset-password/", {
        new_password: password,
        token,
      });
      setSuccess(true);
    } catch (err) {
      setError(err?.message ?? t("LoginPage.resetPasswordErrorDefault"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <h1 className={styles.title}>{t("LoginPage.resetPasswordTitle")}</h1>
      <p className={styles.subtitle}>{t("LoginPage.resetPasswordSubtitle")}</p>

      {success ? (
        <div className={styles.successBox}>
          <MIcon name="check_circle" size={32} />
          <p>{t("LoginPage.resetPasswordSuccess")}</p>
          <button
            type="button"
            className={styles.btn}
            onClick={onDone}
            style={{ marginTop: "8px" }}
          >
            {t("LoginPage.goToLogin")}
          </button>
        </div>
      ) : (
        <form className={styles.form} onSubmit={handleSubmit}>
          <PasswordField
            id="reset-password"
            label={t("LoginPage.newPasswordLabel")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={loading}
          />

          <PasswordField
            id="reset-confirm"
            label={t("LoginPage.confirmNewPasswordLabel")}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            disabled={loading}
            placeholder={t("LoginPage.confirmNewPasswordPlaceholder")}
          />

          {error && <p className={styles.error}>{error}</p>}

          <button type="submit" className={styles.btn} disabled={loading}>
            {loading ? t("LoginPage.updating") : t("LoginPage.updatePassword")}
          </button>
        </form>
      )}
    </>
  );
}

/* ─── 註冊 ──────────────────────────────────────────────── */

function RegisterView({ onBack }) {
  const { t } = useTranslation("login");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");

    if (password.length < 8) {
      setError(t("LoginPage.passwordMinLength"));
      return;
    }
    if (password !== confirm) {
      setError(t("LoginPage.passwordMismatch"));
      return;
    }

    setLoading(true);
    try {
      await apiPost("/api/v1/users/signup", {
        email,
        full_name: fullName,
        password,
      });
      setSuccess(true);
    } catch (err) {
      setError(err?.message ?? t("LoginPage.registerErrorDefault"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <button type="button" className={styles.backBtn} onClick={onBack}>
        <MIcon name="arrow_back" size={18} />
        <span>{t("LoginPage.backToLogin")}</span>
      </button>

      <h1 className={styles.title}>{t("LoginPage.registerTitle")}</h1>
      <p className={styles.subtitle}>{t("LoginPage.registerSubtitle")}</p>

      {success ? (
        <div className={styles.successBox}>
          <MIcon name="check_circle" size={32} />
          <p>{t("LoginPage.registerSuccess")}</p>
          <button
            type="button"
            className={styles.btn}
            onClick={onBack}
            style={{ marginTop: "8px" }}
          >
            {t("LoginPage.backToLogin")}
          </button>
        </div>
      ) : (
        <form className={styles.form} onSubmit={handleSubmit}>
          <div className={styles.field}>
            <label htmlFor="full-name">{t("LoginPage.fullNameLabel")}</label>
            <input
              id="full-name"
              type="text"
              placeholder={t("LoginPage.fullNamePlaceholder")}
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              disabled={loading}
              required
            />
          </div>

          <div className={styles.field}>
            <label htmlFor="reg-email">{t("LoginPage.emailLabel")}</label>
            <input
              id="reg-email"
              type="email"
              placeholder={t("LoginPage.emailPlaceholderExample")}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={loading}
              required
            />
          </div>

          <PasswordField
            id="reg-password"
            label={t("LoginPage.passwordWithMinLengthLabel")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={loading}
          />

          <PasswordField
            id="reg-confirm"
            label={t("LoginPage.confirmPasswordLabel")}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            disabled={loading}
            placeholder={t("LoginPage.confirmPasswordPlaceholder")}
          />

          {error && <p className={styles.error}>{error}</p>}

          <button type="submit" className={styles.btn} disabled={loading}>
            {loading ? t("LoginPage.creating") : t("LoginPage.createAccount")}
          </button>
        </form>
      )}
    </>
  );
}

/* ─── 主元件 ─────────────────────────────────────────────── */

export default function LoginPage() {
  const { t } = useTranslation("login");
  const { user } = useAuth();
  const [resetToken, setResetToken] = useState(() => readResetTokenFromUrl());
  const [deviceCode, setDeviceCode] = useState(() => readDeviceCodeFromUrl());
  const [deviceApproval, setDeviceApproval] = useState({
    status: "idle",
    error: "",
  });
  const approvalKeyRef = useRef("");
  const [view, setView] = useState(() =>
    readResetTokenFromUrl() ? "reset" : "login",
  ); // "login" | "forgot" | "register" | "reset"

  useEffect(() => {
    const onPop = () => {
      const token = readResetTokenFromUrl();
      setDeviceCode(readDeviceCodeFromUrl());
      setResetToken(token);
      setView(token ? "reset" : "login");
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // 裝置授權必須由使用者明確按下「授權此裝置」，不可在頁面載入時自動核准：
  // 否則任何人把 /login?device_code=... 丟給已登入的使用者，就能拿到對方
  // 帳號的桌面 App token（釣魚）。
  const approveDevice = async () => {
    if (!deviceCode || !user) return;

    const approvalKey = `${deviceCode}:${user.id ?? user.email ?? "current"}`;
    if (approvalKeyRef.current === approvalKey) return;
    approvalKeyRef.current = approvalKey;

    setDeviceApproval({ status: "approving", error: "" });
    try {
      await apiPost("/api/v1/desktop-client/auth/approve", {
        device_code: deviceCode,
      });
      setDeviceApproval({ status: "approved", error: "" });
    } catch (err) {
      approvalKeyRef.current = "";
      setDeviceApproval({
        status: "error",
        error: err?.message ?? t("LoginPage.deviceApprovalError"),
      });
    }
  };

  const declineDevice = () => {
    clearDeviceCodeFromUrl();
    setDeviceCode("");
    setDeviceApproval({ status: "idle", error: "" });
    // 已登入使用者回到首頁（整頁重載讓 App 重新判斷路由）
    window.location.replace("/");
  };

  const goLogin = () => {
    clearResetTokenFromUrl();
    setResetToken("");
    setView("login");
  };

  const showRegister = ENABLE_SIGNUP && view === "register";

  if (deviceCode && user) {
    return (
      <div className={styles.page}>
        <div className={styles.card}>
          <DeviceApprovalView
            status={deviceApproval.status}
            error={deviceApproval.error}
            user={user}
            onApprove={approveDevice}
            onDecline={declineDevice}
          />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.card}>
        {view === "login" && (
          <LoginView
            deviceApproval={Boolean(deviceCode)}
            onForgot={() => setView("forgot")}
            onRegister={() => setView("register")}
          />
        )}
        {view === "forgot" && <ForgotView onBack={() => setView("login")} />}
        {showRegister && <RegisterView onBack={() => setView("login")} />}
        {view === "reset" && <ResetView token={resetToken} onDone={goLogin} />}
        {view === "register" && !ENABLE_SIGNUP && (
          <LoginView
            deviceApproval={Boolean(deviceCode)}
            onForgot={() => setView("forgot")}
            onRegister={() => setView("login")}
          />
        )}
      </div>
    </div>
  );
}
