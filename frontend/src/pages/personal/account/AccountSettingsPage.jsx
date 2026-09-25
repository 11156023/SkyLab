import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import styles from "./AccountSettingsPage.module.scss";
import MIcon from "../../../components/MIcon";
import Avatar from "../../../components/Avatar/Avatar";
import PasswordInput from "../../../components/PasswordInput/PasswordInput";
import FileDropzone from "../../../components/FileDropzone/FileDropzone";
import TotpEnrollment from "../../../components/TotpEnrollment/TotpEnrollment";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { AccountService } from "../../../services/account";
import { focusInvalidField } from "../../../utils/focusField";
import { downscaleImage } from "../../../utils/image/downscaleImage";
import AppearanceTab from "./AppearanceTab";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";

/* 密碼與刪除帳號都屬「帳號本身」的事，跟個人資料同一個分頁直向堆疊
   （危險區域照慣例壓底），分頁只留「個人資料／外觀」兩個 */
const TABS = [
  { key: "profile",    labelKey: "AccountSettingsPage.tabProfile" },
  { key: "appearance", labelKey: "AccountSettingsPage.tabAppearance" },
];

/* ── 個人資料 ───────────────────────────────────────── */

function ProfileTab() {
  const { t } = useTranslation("personal");
  const { user, updateUser } = useAuth();
  const toast = useToast();
  const [editMode, setEditMode] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    full_name: user?.full_name ?? "",
    email: user?.email ?? "",
    avatar_url: user?.avatar_url ?? "",
  });
  const [uploading, setUploading] = useState(false);

  function set(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  async function handleAvatarFile(file) {
    // 拖放不受 accept 限制，非圖片先擋下，免得縮圖時才冒出看不懂的錯誤
    if (!file.type.startsWith("image/")) {
      toast.error(t("common:FileDropzone.notAnImage"));
      return;
    }
    setUploading(true);
    try {
      // 頭像顯示尺寸小，縮到 256px 再上傳
      const { blob } = await downscaleImage(file, { maxSize: 256, quality: 0.85 });
      const updated = await AccountService.uploadAvatar(blob);
      updateUser(updated);
      setForm((prev) => ({ ...prev, avatar_url: updated?.avatar_url ?? "" }));
      toast.success(t("ProfileTab.avatarUpdated"));
    } catch (err) {
      toast.error(err?.message ?? t("ProfileTab.avatarUploadFailed"));
    } finally {
      setUploading(false);
    }
  }

  function startEdit() {
    setForm({
      full_name: user?.full_name ?? "",
      email: user?.email ?? "",
      avatar_url: user?.avatar_url ?? "",
    });
    setEditMode(true);
  }

  function cancelEdit() {
    setEditMode(false);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    const payload = {};
    if (form.full_name !== (user?.full_name ?? "")) payload.full_name = form.full_name || null;
    if (form.email !== (user?.email ?? "")) payload.email = form.email;
    if (form.avatar_url !== (user?.avatar_url ?? "")) payload.avatar_url = form.avatar_url || null;

    if (Object.keys(payload).length === 0) {
      setEditMode(false);
      return;
    }

    setSaving(true);
    try {
      const updated = await AccountService.update(payload);
      updateUser(updated);
      toast.success(t("ProfileTab.profileUpdated"));
      setEditMode(false);
    } catch (err) {
      toast.error(err?.message ?? t("ProfileTab.updateFailed"));
    } finally {
      setSaving(false);
    }
  }

  const previewAvatarUrl = editMode ? form.avatar_url : user?.avatar_url;

  return (
    <div className={styles.card}>
      <h2 className={styles.cardTitle}>{t("ProfileTab.title")}</h2>

      <form className={styles.form} onSubmit={handleSubmit}>
        <div className={styles.avatarRow}>
          <Avatar user={user} src={previewAvatarUrl} size={56} />
          <FileDropzone
            compact
            accept="image/*"
            uploading={uploading}
            title={t("common:FileDropzone.titleImage")}
            onFiles={([file]) => handleAvatarFile(file)}
          />
        </div>

        <label className={styles.field}>
          <span>{t("ProfileTab.nameLabel")}</span>
          {editMode ? (
            <input
              value={form.full_name}
              onChange={(e) => set("full_name", e.target.value)}
              maxLength={30}
              placeholder={t("ProfileTab.namePlaceholder")}
            />
          ) : (
            <p className={styles.readValue}>{user?.full_name || t("ProfileTab.notSet")}</p>
          )}
        </label>

        <label className={styles.field}>
          <span>{t("ProfileTab.emailLabel")}</span>
          {editMode ? (
            <input
              type="email"
              value={form.email}
              onChange={(e) => set("email", e.target.value)}
              required
            />
          ) : (
            <p className={styles.readValue}>{user?.email}</p>
          )}
        </label>

        <label className={styles.field}>
          <span>{t("ProfileTab.avatarUrlLabel")}</span>
          {editMode ? (
            <input
              type="url"
              value={form.avatar_url}
              onChange={(e) => set("avatar_url", e.target.value)}
              placeholder="https://example.com/avatar.png"
            />
          ) : (
            <p className={styles.readValue}>{user?.avatar_url || t("ProfileTab.notSet")}</p>
          )}
        </label>

        <div className={styles.formActions}>
          {editMode ? (
            <>
              <button type="button" className={styles.btnSecondary} onClick={cancelEdit} disabled={saving}>
                {t("ProfileTab.cancel")}
              </button>
              <button type="submit" className={styles.btnPrimary} disabled={saving}>
                {saving ? t("ProfileTab.saving") : t("ProfileTab.save")}
              </button>
            </>
          ) : (
            <button type="button" className={styles.btnPrimary} onClick={startEdit}>
              <MIcon name="edit" size={16} />
              {t("ProfileTab.edit")}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

/* ── 密碼 ───────────────────────────────────────────── */

function PasswordSection() {
  const { t } = useTranslation("personal");
  const toast = useToast();
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [saving, setSaving] = useState(false);
  const [invalid, setInvalid] = useState({});
  const fieldRefs = { current: useRef(null), next: useRef(null), confirm: useRef(null) };

  function set(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
    setInvalid((prev) => ({ ...prev, [name]: false }));
  }

  const mismatch = form.confirm.length > 0 && form.next !== form.confirm;
  const tooShort = form.next.length > 0 && form.next.length < 8;

  async function handleSubmit(e) {
    e.preventDefault();
    const missing = {
      current: !form.current,
      next: form.next.length < 8,
      confirm: !form.confirm || form.next !== form.confirm,
    };
    if (missing.current || missing.next || missing.confirm) {
      setInvalid(missing);
      const key = ["current", "next", "confirm"].find((name) => missing[name]);
      focusInvalidField(fieldRefs[key].current);
      return;
    }
    setSaving(true);
    try {
      await AccountService.updatePassword(form.current, form.next);
      toast.success(t("PasswordTab.passwordUpdated"));
      setForm({ current: "", next: "", confirm: "" });
    } catch (err) {
      toast.error(err?.message ?? t("PasswordTab.updateFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={styles.card}>
      <h2 className={styles.cardTitle}>{t("PasswordTab.title")}</h2>

      <form className={styles.form} onSubmit={handleSubmit}>
        <label className={styles.field}>
          <span>{t("PasswordTab.currentLabel")}</span>
          <PasswordInput
            ref={fieldRefs.current}
            className={invalid.current ? styles.fieldInvalid : undefined}
            value={form.current}
            onChange={(e) => set("current", e.target.value)}
            placeholder="••••••••"
          />
        </label>

        <label className={styles.field}>
          <span>{t("PasswordTab.newLabel")}</span>
          <PasswordInput
            ref={fieldRefs.next}
            className={invalid.next ? styles.fieldInvalid : undefined}
            value={form.next}
            onChange={(e) => set("next", e.target.value)}
            placeholder={t("PasswordTab.newPlaceholder")}
          />
          {tooShort && <em className={styles.fieldError}>{t("PasswordTab.newTooShort")}</em>}
        </label>

        <label className={styles.field}>
          <span>{t("PasswordTab.confirmLabel")}</span>
          <PasswordInput
            ref={fieldRefs.confirm}
            className={invalid.confirm ? styles.fieldInvalid : undefined}
            value={form.confirm}
            onChange={(e) => set("confirm", e.target.value)}
            placeholder={t("PasswordTab.confirmPlaceholder")}
          />
          {mismatch && <em className={styles.fieldError}>{t("PasswordTab.mismatch")}</em>}
        </label>

        <div className={styles.formActions}>
          <button type="submit" className={styles.btnPrimary} disabled={saving}>
            {saving ? t("PasswordTab.updating") : t("PasswordTab.updatePassword")}
          </button>
        </div>
      </form>
    </div>
  );
}

/* ── 兩步驟驗證 ─────────────────────────────────────── */

function TwoFactorSection() {
  const { t } = useTranslation("personal");
  const { user, updateUser } = useAuth();
  const toast = useToast();
  const enabled = Boolean(user?.totp_enabled);
  /* 管理員在使用者資料勾了「強制兩步驟驗證」時不能自行停用 */
  const enforced = Boolean(user?.totp_required);
  const [dialog, setDialog] = useState(null); // null | "enable" | "disable"
  const presence = useDialogPresence(dialog);
  const [code, setCode] = useState("");
  const [disabling, setDisabling] = useState(false);
  const codeRef = useRef(null);

  function closeDialog() {
    if (disabling) return;
    setDialog(null);
    setCode("");
  }

  function handleEnabled() {
    updateUser({ totp_enabled: true, totp_setup_required: false });
    toast.success(t("TwoFactorSection.enabledToast"));
    setDialog(null);
  }

  async function handleDisable(e) {
    e.preventDefault();
    const digits = code.replace(/\D/g, "");
    if (digits.length !== 6) {
      codeRef.current?.focus();
      return;
    }
    setDisabling(true);
    try {
      await AccountService.disableTotp(digits);
      updateUser({ totp_enabled: false });
      toast.success(t("TwoFactorSection.disabledToast"));
      setDialog(null);
      setCode("");
    } catch (err) {
      toast.error(err?.message ?? t("TwoFactorSection.codeInvalid"));
      setCode("");
      codeRef.current?.focus();
    } finally {
      setDisabling(false);
    }
  }

  const activeDialog = presence.item;

  return (
    <>
      <div className={styles.card}>
        <h2 className={styles.cardTitle}>{t("TwoFactorSection.title")}</h2>

        <div className={styles.twoFactorStatus}>
          <span className={`${styles.twoFactorIcon} ${enabled ? styles.twoFactorIconOn : ""}`}>
            <MIcon name={enabled ? "verified_user" : "shield"} size={22} />
          </span>
          <div className={styles.twoFactorText}>
            <strong>
              {enabled ? t("TwoFactorSection.statusOn") : t("TwoFactorSection.statusOff")}
            </strong>
            <span>
              {enabled ? t("TwoFactorSection.descOn") : t("TwoFactorSection.descOff")}
            </span>
          </div>
        </div>

        <div className={styles.formActions}>
          {enabled ? (
            <button
              type="button"
              className={styles.btnSecondary}
              onClick={() => setDialog("disable")}
              disabled={enforced}
              title={enforced ? t("TwoFactorSection.enforcedHint") : undefined}
            >
              {t("TwoFactorSection.disable")}
            </button>
          ) : (
            <button type="button" className={styles.btnPrimary} onClick={() => setDialog("enable")}>
              <MIcon name="qr_code_2" size={16} />
              {t("TwoFactorSection.enable")}
            </button>
          )}
        </div>
        {enabled && enforced && (
          <p className={styles.twoFactorHint}>{t("TwoFactorSection.enforcedHint")}</p>
        )}
      </div>

      {activeDialog && createPortal(
        /* portal 到 body：理由同 DangerZoneSection（backdrop-filter 的 containing block 陷阱） */
        <div
          className={`${styles.modalOverlay} ${presence.closing ? styles.modalOverlayOut : ""}`}
          onMouseDown={closeDialog}
        >
          <div
            className={`${styles.confirm} ${activeDialog === "enable" ? styles.confirmWide : ""}`}
            onMouseDown={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            {activeDialog === "enable" ? (
              <>
                <h2>{t("TwoFactorSection.enableTitle")}</h2>
                <TotpEnrollment onConfirmed={handleEnabled} onCancel={closeDialog} />
              </>
            ) : (
              <form onSubmit={handleDisable} className={styles.form}>
                <h2>{t("TwoFactorSection.disableTitle")}</h2>
                <p>{t("TwoFactorSection.disableDesc")}</p>
                <input
                  ref={codeRef}
                  className={`${styles.confirmInput} ${styles.otpInput}`}
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={7}
                  placeholder="000000"
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/[^\d ]/g, ""))}
                  disabled={disabling}
                  autoFocus
                />
                <div className={styles.modalActions}>
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    onClick={closeDialog}
                    disabled={disabling}
                  >
                    {t("TwoFactorSection.cancel")}
                  </button>
                  <button
                    type="submit"
                    className={styles.btnDanger}
                    disabled={disabling || code.replace(/\D/g, "").length !== 6}
                  >
                    {disabling ? t("TwoFactorSection.disabling") : t("TwoFactorSection.confirmDisable")}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}

/* ── 危險區域 ───────────────────────────────────────── */

function DangerZoneSection() {
  const { t } = useTranslation("personal");
  const { logout } = useAuth();
  const toast = useToast();
  const [showConfirm, setShowConfirm] = useState(false);
  const confirmDialog = useDialogPresence(showConfirm);
  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const confirmWord = t("DangerZoneTab.confirmWord");

  /* 關閉一律清掉輸入：否則打完確認字再取消，下次開啟按鈕已是可按狀態 */
  function closeConfirm() {
    if (deleting) return;
    setShowConfirm(false);
    setConfirmText("");
  }

  async function handleDelete() {
    setDeleting(true);
    try {
      await AccountService.delete();
      toast.success(t("DangerZoneTab.accountDeleted"));
      logout();
    } catch (err) {
      toast.error(err?.message ?? t("DangerZoneTab.deleteFailed"));
      setDeleting(false);
    }
  }

  return (
    <>
      <div className={`${styles.card} ${styles.dangerCard}`}>
        <h2 className={styles.cardTitle}>{t("DangerZoneTab.title")}</h2>
        <p className={styles.dangerDesc}>
          {t("DangerZoneTab.descPart1")}<strong>{t("DangerZoneTab.descBold")}</strong>{t("DangerZoneTab.descPart2")}
        </p>
        <div className={styles.formActions}>
          <button type="button" className={styles.btnDanger} onClick={() => setShowConfirm(true)}>
            <MIcon name="delete_forever" size={16} />
            {t("DangerZoneTab.deleteAccount")}
          </button>
        </div>
      </div>

      {confirmDialog.open && createPortal(
        /* 用 portal 掛到 document.body：避免 Modal 巢狀在有 backdrop-filter 的 .dangerCard
           底下 —— backdrop-filter 會讓後代的 position:fixed 失去「相對整個視窗定位」的能力，
           變成只覆蓋卡片自己的範圍（CSS containing block 陷阱）。 */
        <div
          className={`${styles.modalOverlay} ${confirmDialog.closing ? styles.modalOverlayOut : ""}`}
          onMouseDown={closeConfirm}
        >
          <div className={styles.confirm} onMouseDown={(e) => e.stopPropagation()}>
            <div className={styles.confirmIcon}>
              <MIcon name="warning" size={24} />
            </div>
            <h2>{t("DangerZoneTab.confirmTitle")}</h2>
            <p>
              {t("DangerZoneTab.confirmDescPart1")}<strong>{t("DangerZoneTab.confirmDescBold")}</strong>{t("DangerZoneTab.confirmDescPart2")} <code>{confirmWord}</code> {t("DangerZoneTab.confirmDescPart3")}
            </p>
            <input
              className={styles.confirmInput}
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder={t("DangerZoneTab.confirmPlaceholder", { word: confirmWord })}
              disabled={deleting}
            />
            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={closeConfirm}
                disabled={deleting}
              >
                {t("DangerZoneTab.cancel")}
              </button>
              <button
                type="button"
                className={styles.btnDanger}
                disabled={confirmText !== confirmWord || deleting}
                onClick={handleDelete}
              >
                {deleting ? t("DangerZoneTab.deleting") : t("DangerZoneTab.confirmDelete")}
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}

/* ── Page ───────────────────────────────────────────── */

export default function AccountSettingsPage() {
  const { t } = useTranslation("personal");
  const [activeTab, setActiveTab] = useState("profile");

  return (
    <div className={styles.page}>
      <PageHeader title={t("AccountSettingsPage.title")} />

      <SegmentedControl
        className={styles.tabs}
        options={TABS.map((tab) => ({ value: tab.key, label: t(tab.labelKey) }))}
        value={activeTab}
        onChange={setActiveTab}
        ariaLabel={t("AccountSettingsPage.title")}
      />

      <div className={styles.content}>
        {activeTab === "profile" && (
          <div className={styles.profileGrid}>
            <ProfileTab />
            <div className={styles.profileSide}>
              <PasswordSection />
              <TwoFactorSection />
              <DangerZoneSection />
            </div>
          </div>
        )}
        {activeTab === "appearance" && <AppearanceTab />}
      </div>
    </div>
  );
}
