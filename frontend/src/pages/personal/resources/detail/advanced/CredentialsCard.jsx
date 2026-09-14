/**
 * CredentialsCard — 登入憑證
 * 重設密碼、重新產生平台金鑰、匯入／移除自己的公鑰。
 * 新密碼與新私鑰只在產生後顯示一次；要再看請到總覽分頁。
 */

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import styles from "../ResourceDetailPage.module.scss";
import MIcon from "../../../../../components/MIcon";
import LoadingState from "../../../../../components/LoadingState/LoadingState";
import useDialogPresence from "../../../../../hooks/useDialogPresence";
import { useToast } from "../../../../../hooks/useToast";
import { useConfirm } from "../../../../../components/ConfirmDialog/ConfirmProvider";
import { ResourcesService } from "../../../../../services/resources";
import { downloadBlob } from "../../../../../services/api";

function keyIdentity(key) {
  const parts = String(key).trim().split(/\s+/);
  return parts.slice(0, 2).join(" ");
}

function PasswordModal({ closing, loading, onClose, onSubmit }) {
  const { t } = useTranslation("personal");
  const [custom, setCustom] = useState(false);
  const [password, setPassword] = useState("");
  const invalid = custom && (password.length < 8 || /\s/.test(password));

  function submit(e) {
    e.preventDefault();
    if (invalid) return;
    onSubmit(custom ? password : null);
  }

  return (
    <div className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`} onMouseDown={onClose}>
      <form className={styles.modal} onSubmit={submit} onMouseDown={(e) => e.stopPropagation()}>
        <h2 className={styles.modalTitle}>{t("CredentialsCard.resetPasswordTitle")}</h2>
        <p className={styles.modalDesc}>{t("CredentialsCard.resetPasswordDesc")}</p>
        <label className={styles.checkRow}>
          <input type="checkbox" checked={custom} onChange={(e) => setCustom(e.target.checked)} />
          <span>{t("CredentialsCard.useCustomPassword")}</span>
        </label>
        {custom && (
          <div className={`${styles.field} ${invalid && password ? styles.fieldInvalid : ""}`}>
            <label htmlFor="cred-pw">{t("CredentialsCard.newPasswordLabel")}</label>
            <input id="cred-pw" type="text" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off" />
            <span className={styles.fieldHint}>{t("CredentialsCard.newPasswordHint")}</span>
          </div>
        )}
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
            {t("CredentialsCard.cancel")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading || invalid}>
            {loading ? t("CredentialsCard.processing") : t("CredentialsCard.resetPassword")}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function CredentialsCard({ vmid, canManage }) {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const confirm = useConfirm();
  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const passwordPresence = useDialogPresence(showPassword);
  const [secret, setSecret] = useState(null); // { kind: "password"|"key", value, message }
  const [newKey, setNewKey] = useState("");
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setInfo(await ResourcesService.getCredentials(vmid));
    } catch (err) {
      toast.error(err?.message ?? t("CredentialsCard.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [vmid, toast, t]);

  useEffect(() => {
    load();
  }, [load]);

  const copy = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error(t("CredentialsCard.copyFailed"));
    }
  };

  async function handleResetPassword(password) {
    setBusy(true);
    try {
      const res = await ResourcesService.resetPassword(vmid, password);
      setShowPassword(false);
      setSecret({ kind: "password", value: res.password, message: res.message });
      toast.success(res.message);
      await load();
    } catch (err) {
      toast.error(err?.message ?? t("CredentialsCard.resetFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleRegenerateKey() {
    const ok = await confirm({
      title: t("CredentialsCard.regenerateTitle"),
      message: t("CredentialsCard.regenerateMessage"),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await ResourcesService.regenerateSshKey(vmid);
      setSecret({ kind: "key", value: res.ssh_private_key, message: res.message });
      toast.success(res.message);
      await load();
    } catch (err) {
      toast.error(err?.message ?? t("CredentialsCard.regenerateFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleAddKey(e) {
    e.preventDefault();
    const key = newKey.trim();
    if (!key) return;
    setBusy(true);
    try {
      const res = await ResourcesService.addAuthorizedKey(vmid, key);
      setInfo((prev) => (prev ? { ...prev, authorized_keys: res.authorized_keys } : prev));
      setNewKey("");
      toast.success(res.message);
    } catch (err) {
      toast.error(err?.message ?? t("CredentialsCard.addKeyFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleRemoveKey(key) {
    const ok = await confirm({
      title: t("CredentialsCard.removeKeyTitle"),
      message: t("CredentialsCard.removeKeyMessage"),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await ResourcesService.removeAuthorizedKey(vmid, key);
      setInfo((prev) => (prev ? { ...prev, authorized_keys: res.authorized_keys } : prev));
      toast.success(res.message);
    } catch (err) {
      toast.error(err?.message ?? t("CredentialsCard.removeKeyFailed"));
    } finally {
      setBusy(false);
    }
  }

  const platformIdentity = info?.platform_public_key ? keyIdentity(info.platform_public_key) : null;
  const requiresRunning = info?.requires_running && !info?.running;

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <div>
          <h2 className={styles.cardTitle}>
            <MIcon name="key" size={18} />
            {t("CredentialsCard.title")}
          </h2>
          <p className={styles.cardDesc}>{t("CredentialsCard.desc")}</p>
        </div>
        {canManage && info && (
          <div className={styles.headerActions}>
            <button type="button" className={styles.btnSecondary} disabled={busy || requiresRunning} onClick={() => setShowPassword(true)}>
              <MIcon name="password" size={16} />
              {t("CredentialsCard.resetPassword")}
            </button>
            <button type="button" className={styles.btnSecondary} disabled={busy || requiresRunning} onClick={handleRegenerateKey}>
              <MIcon name="autorenew" size={16} />
              {t("CredentialsCard.regenerateKey")}
            </button>
          </div>
        )}
      </div>
      <div className={styles.cardBody}>
        {loading || !info ? (
          <LoadingState text={t("CredentialsCard.loading")} />
        ) : (
          <>
            {requiresRunning && (
              <p className={`${styles.hintLine} ${styles.hintWarn}`}>
                <MIcon name="info" size={14} />
                {t("CredentialsCard.requiresRunning")}
              </p>
            )}
            {info.resource_type === "qemu" && (
              <p className={styles.hintLine}>
                <MIcon name="info" size={14} />
                {t("CredentialsCard.cloudInitNote")}
              </p>
            )}

            <div className={styles.factGrid}>
              <div className={styles.fact}>
                <span className={styles.factLabel}>{t("CredentialsCard.usernameLabel")}</span>
                <span className={`${styles.factValue} ${styles.monoText}`}>
                  {info.username ?? t("CredentialsCard.usernameDefault")}
                </span>
              </div>
              <div className={styles.fact}>
                <span className={styles.factLabel}>{t("CredentialsCard.passwordLabel")}</span>
                <span className={styles.factValue}>
                  {info.has_login_password ? t("CredentialsCard.passwordStored") : t("CredentialsCard.passwordUnknown")}
                </span>
                <span className={styles.mutedText}>{t("CredentialsCard.passwordWhere")}</span>
              </div>
            </div>

            {secret && (
              <div className={styles.secretBox}>
                <span className={styles.noteBoxTitle}>
                  <MIcon name="visibility" size={14} />
                  {secret.kind === "password" ? t("CredentialsCard.newPasswordOnce") : t("CredentialsCard.newKeyOnce")}
                </span>
                <pre className={styles.keyPre}>{secret.value}</pre>
                <span className={styles.mutedText}>{secret.message}</span>
                <div className={styles.keyActions}>
                  <button type="button" className={styles.ghostBtn} onClick={() => copy(secret.value)}>
                    <MIcon name={copied ? "check" : "content_copy"} size={14} />
                    {copied ? t("CredentialsCard.copied") : t("CredentialsCard.copy")}
                  </button>
                  {secret.kind === "key" && (
                    <button
                      type="button"
                      className={styles.ghostBtn}
                      onClick={() => downloadBlob(new Blob([secret.value], { type: "text/plain" }), `id_ed25519_vm${vmid}`)}
                    >
                      <MIcon name="download" size={14} />
                      {t("CredentialsCard.download")}
                    </button>
                  )}
                  <button type="button" className={styles.ghostBtn} onClick={() => setSecret(null)}>
                    <MIcon name="close" size={14} />
                    {t("CredentialsCard.dismiss")}
                  </button>
                </div>
              </div>
            )}

            <div className={styles.rowStack}>
              <span className={styles.factLabel}>{t("CredentialsCard.authorizedKeysLabel")}</span>
              {info.authorized_keys.length === 0 ? (
                <p className={styles.mutedText}>
                  {requiresRunning ? t("CredentialsCard.keysUnavailableStopped") : t("CredentialsCard.noKeys")}
                </p>
              ) : (
                <div className={styles.keyList}>
                  {info.authorized_keys.map((key) => {
                    const isPlatform = platformIdentity && keyIdentity(key) === platformIdentity;
                    return (
                      <div key={key} className={styles.keyItem}>
                        <MIcon name={isPlatform ? "verified_user" : "vpn_key"} size={16} />
                        <span className={styles.keyText} title={key}>{key}</span>
                        {isPlatform ? (
                          <span className={`${styles.badge} ${styles.badge_info}`}>{t("CredentialsCard.platformKey")}</span>
                        ) : (
                          canManage && (
                            <button
                              type="button"
                              className={`${styles.rpIconBtn} ${styles.rpIconBtnDanger}`}
                              disabled={busy || requiresRunning}
                              title={t("CredentialsCard.removeKey")}
                              onClick={() => handleRemoveKey(key)}
                            >
                              <MIcon name="delete" size={16} />
                            </button>
                          )
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
              {canManage && (
                <form className={styles.inlineForm} onSubmit={handleAddKey}>
                  <textarea
                    rows={2}
                    value={newKey}
                    onChange={(e) => setNewKey(e.target.value)}
                    placeholder={t("CredentialsCard.addKeyPlaceholder")}
                    disabled={busy || requiresRunning}
                  />
                  <button type="submit" className={styles.btnSecondary} disabled={busy || requiresRunning || !newKey.trim()}>
                    <MIcon name="add" size={16} />
                    {t("CredentialsCard.addKey")}
                  </button>
                </form>
              )}
            </div>
          </>
        )}
      </div>

      {/* portal 到 body：卡片的 overflow:hidden + backdrop-filter 會把 fixed modal 困在卡片裡 */}
      {passwordPresence.open &&
        createPortal(
          <PasswordModal
            closing={passwordPresence.closing}
            loading={busy}
            onClose={() => setShowPassword(false)}
            onSubmit={handleResetPassword}
          />,
          document.body,
        )}
    </div>
  );
}
