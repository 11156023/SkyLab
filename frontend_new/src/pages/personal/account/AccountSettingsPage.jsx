import { useState } from "react";
import { createPortal } from "react-dom";
import styles from "./AccountSettingsPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import { AccountService } from "../../../services/account";

const TABS = [
  { key: "profile",  label: "個人資料", icon: "person" },
  { key: "password", label: "密碼",     icon: "lock" },
  { key: "danger",   label: "危險區域", icon: "warning" },
];

function initials(user) {
  const source = user?.full_name || user?.email || "U";
  return source.slice(0, 1).toUpperCase();
}

/* ── 個人資料 ───────────────────────────────────────── */

function ProfileTab() {
  const { user, updateUser } = useAuth();
  const toast = useToast();
  const [editMode, setEditMode] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    full_name: user?.full_name ?? "",
    email: user?.email ?? "",
    avatar_url: user?.avatar_url ?? "",
  });
  const [avatarFailed, setAvatarFailed] = useState(false);

  function set(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  function startEdit() {
    setForm({
      full_name: user?.full_name ?? "",
      email: user?.email ?? "",
      avatar_url: user?.avatar_url ?? "",
    });
    setAvatarFailed(false);
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
      toast.success("個人資料已更新");
      setEditMode(false);
    } catch (err) {
      toast.error(err?.message ?? "更新失敗");
    } finally {
      setSaving(false);
    }
  }

  const previewAvatarUrl = editMode ? form.avatar_url : user?.avatar_url;
  const showImage = previewAvatarUrl && !avatarFailed;

  return (
    <div className={styles.card}>
      <h2 className={styles.cardTitle}>個人資料</h2>

      <form className={styles.form} onSubmit={handleSubmit}>
        <div className={styles.avatarRow}>
          <div className={styles.avatarPreview}>
            {showImage ? (
              <img src={previewAvatarUrl} alt="頭像預覽" onError={() => setAvatarFailed(true)} />
            ) : (
              initials(user)
            )}
          </div>
          <div className={styles.avatarHint}>
            <p className={styles.rowName}>頭像</p>
            <p className={styles.rowMeta}>貼上一個圖片網址作為你的頭像，留空則顯示姓名縮寫</p>
          </div>
        </div>

        <label className={styles.field}>
          <span>姓名</span>
          {editMode ? (
            <input
              value={form.full_name}
              onChange={(e) => set("full_name", e.target.value)}
              maxLength={30}
              placeholder="你的顯示名稱"
            />
          ) : (
            <p className={styles.readValue}>{user?.full_name || "未設定"}</p>
          )}
        </label>

        <label className={styles.field}>
          <span>Email</span>
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
          <span>頭像網址</span>
          {editMode ? (
            <input
              type="url"
              value={form.avatar_url}
              onChange={(e) => set("avatar_url", e.target.value)}
              placeholder="https://example.com/avatar.png"
            />
          ) : (
            <p className={styles.readValue}>{user?.avatar_url || "未設定"}</p>
          )}
        </label>

        <div className={styles.formActions}>
          {editMode ? (
            <>
              <button type="button" className={styles.btnSecondary} onClick={cancelEdit} disabled={saving}>
                取消
              </button>
              <button type="submit" className={styles.btnPrimary} disabled={saving}>
                {saving ? "儲存中..." : "儲存"}
              </button>
            </>
          ) : (
            <button type="button" className={styles.btnPrimary} onClick={startEdit}>
              <MIcon name="edit" size={16} />
              編輯
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

/* ── 密碼 ───────────────────────────────────────────── */

function PasswordTab() {
  const toast = useToast();
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [saving, setSaving] = useState(false);

  function set(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  const mismatch = form.confirm.length > 0 && form.next !== form.confirm;
  const tooShort = form.next.length > 0 && form.next.length < 8;
  const canSubmit = form.current && form.next.length >= 8 && form.next === form.confirm;

  async function handleSubmit(e) {
    e.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    try {
      await AccountService.updatePassword(form.current, form.next);
      toast.success("密碼已更新");
      setForm({ current: "", next: "", confirm: "" });
    } catch (err) {
      toast.error(err?.message ?? "密碼更新失敗");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={styles.card}>
      <h2 className={styles.cardTitle}>變更密碼</h2>

      <form className={styles.form} onSubmit={handleSubmit}>
        <label className={styles.field}>
          <span>目前密碼</span>
          <input
            type="password"
            value={form.current}
            onChange={(e) => set("current", e.target.value)}
            placeholder="••••••••"
            required
          />
        </label>

        <label className={styles.field}>
          <span>新密碼</span>
          <input
            type="password"
            value={form.next}
            onChange={(e) => set("next", e.target.value)}
            placeholder="至少 8 個字元"
            required
          />
          {tooShort && <em className={styles.fieldError}>新密碼至少需要 8 個字元</em>}
        </label>

        <label className={styles.field}>
          <span>確認新密碼</span>
          <input
            type="password"
            value={form.confirm}
            onChange={(e) => set("confirm", e.target.value)}
            placeholder="再輸入一次新密碼"
            required
          />
          {mismatch && <em className={styles.fieldError}>兩次輸入的密碼不一致</em>}
        </label>

        <div className={styles.formActions}>
          <button type="submit" className={styles.btnPrimary} disabled={!canSubmit || saving}>
            {saving ? "更新中..." : "更新密碼"}
          </button>
        </div>
      </form>
    </div>
  );
}

/* ── 危險區域 ───────────────────────────────────────── */

function DangerZoneTab() {
  const { logout } = useAuth();
  const toast = useToast();
  const [showConfirm, setShowConfirm] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);

  async function handleDelete() {
    setDeleting(true);
    try {
      await AccountService.delete();
      toast.success("帳號已刪除");
      logout();
    } catch (err) {
      toast.error(err?.message ?? "刪除失敗");
      setDeleting(false);
    }
  }

  return (
    <>
      <div className={`${styles.card} ${styles.dangerCard}`}>
        <h2 className={styles.cardTitle}>刪除帳號</h2>
        <p className={styles.dangerDesc}>
          你的帳號與所有相關資料將被<strong>永久刪除</strong>，此操作無法復原。若你仍持有已開通的資源，後端會拒絕刪除，請先清除資源。
        </p>
        <div className={styles.formActions}>
          <button type="button" className={styles.btnDanger} onClick={() => setShowConfirm(true)}>
            <MIcon name="delete_forever" size={16} />
            刪除帳號
          </button>
        </div>
      </div>

      {showConfirm && createPortal(
        /* 用 portal 掛到 document.body：避免 Modal 巢狀在有 backdrop-filter 的 .dangerCard
           底下 —— backdrop-filter 會讓後代的 position:fixed 失去「相對整個視窗定位」的能力，
           變成只覆蓋卡片自己的範圍（CSS containing block 陷阱）。 */
        <div className={styles.modalOverlay} onMouseDown={() => !deleting && setShowConfirm(false)}>
          <div className={styles.confirm} onMouseDown={(e) => e.stopPropagation()}>
            <div className={styles.confirmIcon}>
              <MIcon name="warning" size={24} />
            </div>
            <h2>確定要刪除帳號嗎？</h2>
            <p>
              此操作<strong>無法復原</strong>。請輸入 <code>刪除</code> 以確認。
            </p>
            <input
              className={styles.confirmInput}
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder="輸入「刪除」以確認"
              disabled={deleting}
            />
            <div className={styles.modalActions}>
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={() => setShowConfirm(false)}
                disabled={deleting}
              >
                取消
              </button>
              <button
                type="button"
                className={styles.btnDanger}
                disabled={confirmText !== "刪除" || deleting}
                onClick={handleDelete}
              >
                {deleting ? "刪除中..." : "確定刪除"}
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
  const [activeTab, setActiveTab] = useState("profile");

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>帳號設定</h1>
          <p className={styles.pageSubtitle}>管理你的個人資料、密碼與帳號安全</p>
        </div>
      </div>

      <div className={styles.tabs}>
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={activeTab === tab.key ? styles.tabActive : styles.tab}
            onClick={() => setActiveTab(tab.key)}
          >
            <MIcon name={tab.icon} size={16} />
            {tab.label}
          </button>
        ))}
      </div>

      <div className={styles.content}>
        {activeTab === "profile" && <ProfileTab />}
        {activeTab === "password" && <PasswordTab />}
        {activeTab === "danger" && <DangerZoneTab />}
      </div>
    </div>
  );
}
