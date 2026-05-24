import { useState } from "react";
import { useAuth } from "../../contexts/AuthContext";
import { apiPost } from "../../services/api";
import styles from "./LoginPage.module.scss";

/* ─── 共用元件 ─────────────────────────────────────────── */

function MIcon({ name, size = 20 }) {
  return (
    <span className="material-icons-outlined" style={{ fontSize: size, lineHeight: 1 }}>
      {name}
    </span>
  );
}

function PasswordField({ id, label, value, onChange, disabled, placeholder }) {
  const [show, setShow] = useState(false);
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{label}</label>
      <div className={styles.passwordWrap}>
        <input
          id={id}
          type={show ? "text" : "password"}
          placeholder={placeholder ?? "請輸入密碼"}
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
          aria-label={show ? "隱藏密碼" : "顯示密碼"}
        >
          <MIcon name={show ? "visibility_off" : "visibility"} />
        </button>
      </div>
    </div>
  );
}

/* ─── 登入 ──────────────────────────────────────────────── */

function LoginView({ onForgot, onRegister }) {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error,    setError]    = useState("");
  const [loading,  setLoading]  = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
    } catch (err) {
      setError(err?.message ?? "登入失敗，請確認帳號與密碼");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <h1 className={styles.title}>SkyLab</h1>
      <p className={styles.subtitle}>雲端校園管理平台</p>

      <form className={styles.form} onSubmit={handleSubmit}>
        <div className={styles.field}>
          <label htmlFor="username">帳號</label>
          <input
            id="username"
            type="text"
            placeholder="請輸入帳號（電子郵件）"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={loading}
            required
          />
        </div>

        <PasswordField
          id="password"
          label="密碼"
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
          忘記密碼？
        </button>

        {error && <p className={styles.error}>{error}</p>}

        <button type="submit" className={styles.btn} disabled={loading}>
          {loading ? "登入中…" : "登入"}
        </button>
      </form>

      <p className={styles.footerText}>
        還沒有帳號？{" "}
        <button type="button" className={styles.link} onClick={onRegister}>
          立即註冊
        </button>
      </p>
    </>
  );
}

/* ─── 忘記密碼 ──────────────────────────────────────────── */

function ForgotView({ onBack }) {
  const [email,   setEmail]   = useState("");
  const [error,   setError]   = useState("");
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await apiPost(`/api/v1/password-recovery/${encodeURIComponent(email)}`, null);
      setSuccess(true);
    } catch (err) {
      setError(err?.message ?? "發送失敗，請確認電子郵件是否正確");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <button type="button" className={styles.backBtn} onClick={onBack}>
        <MIcon name="arrow_back" size={18} />
        <span>返回登入</span>
      </button>

      <h1 className={styles.title}>忘記密碼</h1>
      <p className={styles.subtitle}>輸入您的電子郵件，我們將寄送重設連結</p>

      {success ? (
        <div className={styles.successBox}>
          <MIcon name="mark_email_read" size={32} />
          <p>重設連結已寄出，請至信箱查收</p>
        </div>
      ) : (
        <form className={styles.form} onSubmit={handleSubmit}>
          <div className={styles.field}>
            <label htmlFor="forgot-email">電子郵件</label>
            <input
              id="forgot-email"
              type="email"
              placeholder="user@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={loading}
              required
            />
          </div>

          {error && <p className={styles.error}>{error}</p>}

          <button type="submit" className={styles.btn} disabled={loading}>
            {loading ? "發送中…" : "發送重設連結"}
          </button>
        </form>
      )}
    </>
  );
}

/* ─── 註冊 ──────────────────────────────────────────────── */

function RegisterView({ onBack }) {
  const [fullName,  setFullName]  = useState("");
  const [email,     setEmail]     = useState("");
  const [password,  setPassword]  = useState("");
  const [confirm,   setConfirm]   = useState("");
  const [error,     setError]     = useState("");
  const [success,   setSuccess]   = useState(false);
  const [loading,   setLoading]   = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");

    if (password.length < 8) {
      setError("密碼至少需要 8 個字元");
      return;
    }
    if (password !== confirm) {
      setError("兩次密碼輸入不一致");
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
      setError(err?.message ?? "註冊失敗，請稍後再試");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <button type="button" className={styles.backBtn} onClick={onBack}>
        <MIcon name="arrow_back" size={18} />
        <span>返回登入</span>
      </button>

      <h1 className={styles.title}>建立帳號</h1>
      <p className={styles.subtitle}>加入 SkyLab 開始使用雲端資源</p>

      {success ? (
        <div className={styles.successBox}>
          <MIcon name="check_circle" size={32} />
          <p>帳號建立成功！請等待管理員審核後即可登入</p>
          <button type="button" className={styles.btn} onClick={onBack} style={{ marginTop: "8px" }}>
            返回登入
          </button>
        </div>
      ) : (
        <form className={styles.form} onSubmit={handleSubmit}>
          <div className={styles.field}>
            <label htmlFor="full-name">姓名</label>
            <input
              id="full-name"
              type="text"
              placeholder="請輸入您的姓名"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              disabled={loading}
              required
            />
          </div>

          <div className={styles.field}>
            <label htmlFor="reg-email">電子郵件</label>
            <input
              id="reg-email"
              type="email"
              placeholder="user@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={loading}
              required
            />
          </div>

          <PasswordField
            id="reg-password"
            label="密碼（至少 8 個字元）"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={loading}
          />

          <PasswordField
            id="reg-confirm"
            label="確認密碼"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            disabled={loading}
            placeholder="再次輸入密碼"
          />

          {error && <p className={styles.error}>{error}</p>}

          <button type="submit" className={styles.btn} disabled={loading}>
            {loading ? "建立中…" : "建立帳號"}
          </button>
        </form>
      )}
    </>
  );
}

/* ─── 主元件 ─────────────────────────────────────────────── */

export default function LoginPage() {
  const [view, setView] = useState("login"); // "login" | "forgot" | "register"

  return (
    <div className={styles.page}>
      <div className={styles.card}>
        {view === "login"    && <LoginView    onForgot={() => setView("forgot")}   onRegister={() => setView("register")} />}
        {view === "forgot"   && <ForgotView   onBack={() => setView("login")} />}
        {view === "register" && <RegisterView onBack={() => setView("login")} />}
      </div>
    </div>
  );
}
