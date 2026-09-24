import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import QRCode from "qrcode";
import MIcon from "../MIcon";
import { AccountService } from "../../services/account";
import styles from "./TotpEnrollment.module.scss";

/**
 * TotpEnrollment
 * 綁定兩步驟驗證的完整流程（帳號設定的對話框與「強制綁定」畫面共用）：
 *   1. 掛載時向後端取金鑰（待確認狀態）→ 畫 QR code、顯示手動輸入金鑰
 *   2. 使用者用 Google Authenticator 掃描後輸入 6 位數驗證碼
 *   3. 確認成功 → onConfirmed()
 *
 * @param {() => void} onConfirmed  驗證碼確認成功（後端已啟用）
 * @param {() => void} [onCancel]   取消按鈕（不傳就不顯示）
 * @param {string} [confirmLabel]   確認按鈕文字（預設「啟用兩步驟驗證」）
 */
export default function TotpEnrollment({ onConfirmed, onCancel, confirmLabel }) {
  const { t } = useTranslation("components");
  const [setup, setSetup] = useState(null); // { secret, otpauth_uri, account, issuer }
  const [qrDataUrl, setQrDataUrl] = useState("");
  const [loadError, setLoadError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setSetup(null);
    setQrDataUrl("");
    setLoadError("");
    AccountService.setupTotp()
      .then(async (data) => {
        if (cancelled) return;
        /* QR 在瀏覽器端產生：otpauth URI 含金鑰，不經第三方服務 */
        const url = await QRCode.toDataURL(data.otpauth_uri, {
          margin: 1,
          width: 224,
          errorCorrectionLevel: "M",
        });
        if (cancelled) return;
        setSetup(data);
        setQrDataUrl(url);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err?.message ?? t("TotpEnrollment.loadFailed"));
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey, t]);

  useEffect(() => {
    if (setup) inputRef.current?.focus();
  }, [setup]);

  const digits = code.replace(/\D/g, "");
  /* 金鑰四個一組顯示，對照 App 手動輸入時比較不會看錯行 */
  const groupedSecret = setup?.secret?.match(/.{1,4}/g)?.join(" ") ?? "";

  async function copySecret() {
    if (!setup?.secret) return;
    try {
      await navigator.clipboard.writeText(setup.secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 無剪貼簿權限時使用者仍可手動選取金鑰文字 */
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (digits.length !== 6) {
      setError(t("TotpEnrollment.codeLength"));
      inputRef.current?.focus();
      return;
    }
    setBusy(true);
    setError("");
    try {
      await AccountService.confirmTotp(digits);
      onConfirmed?.();
    } catch (err) {
      setError(err?.message ?? t("TotpEnrollment.codeInvalid"));
      setCode("");
      inputRef.current?.focus();
    } finally {
      setBusy(false);
    }
  }

  if (loadError) {
    return (
      <div className={styles.loadError}>
        <MIcon name="error_outline" size={20} />
        <span>{loadError}</span>
        <button
          type="button"
          className={styles.btnSecondary}
          onClick={() => setReloadKey((n) => n + 1)}
        >
          <MIcon name="refresh" size={16} />
          {t("TotpEnrollment.retry")}
        </button>
      </div>
    );
  }

  return (
    <form className={styles.root} onSubmit={handleSubmit}>
      <ol className={styles.steps}>
        <li>{t("TotpEnrollment.step1")}</li>
        <li>{t("TotpEnrollment.step2")}</li>
        <li>{t("TotpEnrollment.step3")}</li>
      </ol>

      <div className={styles.qrBox} aria-busy={!setup}>
        {qrDataUrl ? (
          <img
            className={styles.qrImg}
            src={qrDataUrl}
            alt={t("TotpEnrollment.qrAlt")}
            width={224}
            height={224}
          />
        ) : (
          <div className={styles.qrPlaceholder}>
            <MIcon name="qr_code_2" size={40} />
            <span>{t("TotpEnrollment.generating")}</span>
          </div>
        )}
      </div>

      {setup && (
        <div className={styles.secretBlock}>
          <span className={styles.secretLabel}>{t("TotpEnrollment.manualKeyLabel")}</span>
          <div className={styles.secretRow}>
            <code className={styles.secret}>{groupedSecret}</code>
            <button
              type="button"
              className={styles.copyBtn}
              onClick={copySecret}
              title={t("TotpEnrollment.copy")}
              aria-label={t("TotpEnrollment.copy")}
            >
              <MIcon name={copied ? "check" : "content_copy"} size={16} />
            </button>
          </div>
          <span className={styles.secretMeta}>
            {t("TotpEnrollment.accountMeta", { issuer: setup.issuer, account: setup.account })}
          </span>
        </div>
      )}

      <label className={styles.codeField}>
        <span>{t("TotpEnrollment.codeLabel")}</span>
        <input
          ref={inputRef}
          className={styles.codeInput}
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9 ]*"
          maxLength={7}
          placeholder="000000"
          value={code}
          onChange={(e) => {
            setCode(e.target.value.replace(/[^\d ]/g, ""));
            setError("");
          }}
          disabled={!setup || busy}
        />
        {error && <em className={styles.error}>{error}</em>}
      </label>

      <div className={styles.actions}>
        {onCancel && (
          <button type="button" className={styles.btnSecondary} onClick={onCancel} disabled={busy}>
            {t("TotpEnrollment.cancel")}
          </button>
        )}
        <button
          type="submit"
          className={styles.btnPrimary}
          disabled={!setup || busy || digits.length !== 6}
        >
          {busy ? t("TotpEnrollment.confirming") : confirmLabel ?? t("TotpEnrollment.confirm")}
        </button>
      </div>
    </form>
  );
}
