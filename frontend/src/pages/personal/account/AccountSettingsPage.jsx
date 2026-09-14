import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import styles from "./AccountSettingsPage.module.scss";
import MIcon from "../../../components/MIcon";
import Avatar from "../../../components/Avatar/Avatar";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { AccountService } from "../../../services/account";
import { focusInvalidField } from "../../../utils/focusField";
import { downscaleImage } from "../../../utils/image/downscaleImage";
import AppearanceTab from "./AppearanceTab";
import PageHeader from "../../../components/PageHeader/PageHeader";

const TABS = [
  { key: "profile",    labelKey: "AccountSettingsPage.tabProfile", icon: "person" },
  { key: "password",   labelKey: "AccountSettingsPage.tabPassword", icon: "lock" },
  { key: "appearance", labelKey: "AccountSettingsPage.tabAppearance", icon: "palette" },
  { key: "danger",     labelKey: "AccountSettingsPage.tabDanger", icon: "warning" },
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
  const avatarFileRef = useRef(null);

  function set(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  async function handleAvatarFile(e) {
    const file = e.target.files?.[0];
    e.target.value = ""; // 允許重選同一個檔案
    if (!file) return;
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
          <div className={styles.avatarHint}>
            <p className={styles.rowName}>{t("ProfileTab.avatarLabel")}</p>
            <p className={styles.rowMeta}>{t("ProfileTab.avatarHint")}</p>
          </div>
          <input
            ref={avatarFileRef}
            type="file"
            accept="image/*"
            hidden
            onChange={handleAvatarFile}
          />
          <button
            type="button"
            className={styles.btnSecondary}
            onClick={() => avatarFileRef.current?.click()}
            disabled={uploading}
          >
            <MIcon name="upload" size={16} />
            {uploading ? t("ProfileTab.uploading") : t("ProfileTab.uploadImage")}
          </button>
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

function PasswordTab() {
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
          <input
            ref={fieldRefs.current}
            className={invalid.current ? styles.fieldInvalid : undefined}
            type="password"
            value={form.current}
            onChange={(e) => set("current", e.target.value)}
            placeholder="••••••••"
          />
        </label>

        <label className={styles.field}>
          <span>{t("PasswordTab.newLabel")}</span>
          <input
            ref={fieldRefs.next}
            className={invalid.next ? styles.fieldInvalid : undefined}
            type="password"
            value={form.next}
            onChange={(e) => set("next", e.target.value)}
            placeholder={t("PasswordTab.newPlaceholder")}
          />
          {tooShort && <em className={styles.fieldError}>{t("PasswordTab.newTooShort")}</em>}
        </label>

        <label className={styles.field}>
          <span>{t("PasswordTab.confirmLabel")}</span>
          <input
            ref={fieldRefs.confirm}
            className={invalid.confirm ? styles.fieldInvalid : undefined}
            type="password"
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

/* ── 危險區域 ───────────────────────────────────────── */

function DangerZoneTab() {
  const { t } = useTranslation("personal");
  const { logout } = useAuth();
  const toast = useToast();
  const [showConfirm, setShowConfirm] = useState(false);
  const confirmDialog = useDialogPresence(showConfirm);
  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const confirmWord = t("DangerZoneTab.confirmWord");

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
          onMouseDown={() => !deleting && setShowConfirm(false)}
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
                onClick={() => setShowConfirm(false)}
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
      <PageHeader title={t("AccountSettingsPage.title")} subtitle={t("AccountSettingsPage.subtitle")} />

      <div className={styles.tabs}>
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={activeTab === tab.key ? styles.tabActive : styles.tab}
            onClick={() => setActiveTab(tab.key)}
          >
            <MIcon name={tab.icon} size={16} />
            {t(tab.labelKey)}
          </button>
        ))}
      </div>

      <div className={styles.content}>
        {activeTab === "profile" && <ProfileTab />}
        {activeTab === "password" && <PasswordTab />}
        {activeTab === "appearance" && <AppearanceTab />}
        {activeTab === "danger" && <DangerZoneTab />}
      </div>
    </div>
  );
}
