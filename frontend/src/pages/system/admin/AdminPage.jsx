import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import styles from "./AdminPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { UsersService } from "../../../services/users";
import PageHeader from "../../../components/PageHeader/PageHeader";
import PasswordInput from "../../../components/PasswordInput/PasswordInput";
import { formatDate } from "../../../utils/formatDate";

const ROLE_ICONS = {
  student: "school",
  teacher: "co_present",
  admin: "admin_panel_settings",
};

const PAGE_SIZE = 50;

function initialForm(user = null) {
  return {
    email: user?.email ?? "",
    full_name: user?.full_name ?? "",
    password: "",
    role: user?.role ?? "student",
    is_active: user?.is_active ?? true,
    totp_required: user?.totp_required ?? false,
  };
}

function userDisplayName(user) {
  return user.full_name || user.email;
}

function EmptyState({ hasQuery }) {
  const { t } = useTranslation("system");
  return (
    <SharedEmptyState
      icon={hasQuery ? "search_off" : "manage_accounts"}
      title={hasQuery ? t("AdminPage.emptyNoResult") : t("AdminPage.emptyNone")}
    />
  );
}

function UserModal({ mode, user, loading, closing = false, onClose, onSubmit, onResetTotp }) {
  const { t } = useTranslation("system");
  const [form, setForm] = useState(() => initialForm(user));
  const isEdit = mode === "edit";
  const [resettingTotp, setResettingTotp] = useState(false);

  /* 手機遺失救援：解除對方的兩步驟驗證，對方既有登入全部失效、下次登入只需密碼 */
  async function handleResetTotp() {
    setResettingTotp(true);
    try {
      await onResetTotp(user);
    } finally {
      setResettingTotp(false);
    }
  }
  /* LDAP 帳號的密碼歸目錄管：本地密碼欄位鎖住（後端也會擋），稽核 #9 */
  const isLdap = isEdit && user?.auth_source === "ldap";
  const titleId = useId();

  /* Esc 關閉（Dialog 標準行為）；送出中不關，跟關閉鈕的行為一致 */
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === "Escape" && !loading) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [loading, onClose]);
  const ROLE_OPTIONS = [
    { value: "student", label: t("AdminPage.roleStudent") },
    { value: "teacher", label: t("AdminPage.roleTeacher") },
    { value: "admin", label: t("AdminPage.roleAdmin") },
  ];

  function setField(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  function submit(e) {
    e.preventDefault();
    const payload = {
      email: form.email.trim(),
      full_name: form.full_name.trim() || null,
      role: form.role,
      is_active: form.is_active,
      totp_required: form.totp_required,
    };
    if (!isLdap && form.password.trim()) payload.password = form.password;
    onSubmit(payload);
  }

  /* 掛到 document.body：外層若加了 backdrop-filter／transform，
     position: fixed 的遮罩會被困在那一層、蓋不滿整個畫面 */
  return createPortal(
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onMouseDown={onClose}
    >
      <form
        className={styles.modal}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onSubmit={submit}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h2 id={titleId}>{isEdit ? t("AdminPage.modalEditTitle") : t("AdminPage.modalCreateTitle")}</h2>
          <button type="button" className={styles.dialogClose} onClick={onClose} aria-label={t("AdminPage.close")}>
            <MIcon name="close" size={18} />
          </button>
        </div>

        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span>Email</span>
            <input
              type="email"
              value={form.email}
              onChange={(e) => setField("email", e.target.value)}
              required
              maxLength={255}
            />
          </label>

          <label className={styles.field}>
            <span>{t("AdminPage.fieldName")}</span>
            <input
              value={form.full_name}
              onChange={(e) => setField("full_name", e.target.value)}
              maxLength={255}
              placeholder={t("AdminPage.fieldNameOptional")}
            />
          </label>

          <label className={styles.field}>
            <span>{isEdit ? t("AdminPage.fieldNewPassword") : t("AdminPage.fieldPassword")}</span>
            <PasswordInput
              value={form.password}
              onChange={(e) => setField("password", e.target.value)}
              minLength={8}
              maxLength={128}
              required={!isEdit}
              disabled={isLdap}
              placeholder={
                isLdap
                  ? t("AdminPage.ldapManagedPlaceholder")
                  : isEdit ? t("AdminPage.passwordUnchangedHint") : t("AdminPage.passwordMinHint")
              }
            />
            {isLdap && <em className={styles.fieldHint}>{t("AdminPage.ldapManagedHint")}</em>}
          </label>

          <label className={styles.field}>
            <span>{t("AdminPage.fieldRole")}</span>
            <select value={form.role} onChange={(e) => setField("role", e.target.value)}>
              {ROLE_OPTIONS.map((role) => (
                <option key={role.value} value={role.value}>{role.label}</option>
              ))}
            </select>
          </label>
        </div>

        <div className={styles.toggleGrid}>
          <label className={styles.checkRow}>
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={(e) => setField("is_active", e.target.checked)}
            />
            <span>{t("AdminPage.fieldActive")}</span>
          </label>
          <label className={styles.checkRow} title={t("AdminPage.fieldTotpRequiredHint")}>
            <input
              type="checkbox"
              checked={form.totp_required}
              onChange={(e) => setField("totp_required", e.target.checked)}
            />
            <span>{t("AdminPage.fieldTotpRequired")}</span>
          </label>
        </div>
        {form.totp_required && (
          <em className={styles.fieldHint}>{t("AdminPage.fieldTotpRequiredHint")}</em>
        )}

        {isEdit && (
          <div className={styles.totpRow}>
            <div className={styles.totpRowText}>
              <strong>{t("AdminPage.totpLabel")}</strong>
              <span>
                {user?.totp_enabled ? t("AdminPage.totpStatusOn") : t("AdminPage.totpStatusOff")}
              </span>
            </div>
            {user?.totp_enabled && (
              <button
                type="button"
                className={styles.btnSecondary}
                onClick={handleResetTotp}
                disabled={loading || resettingTotp}
              >
                <MIcon name="phonelink_erase" size={16} />
                {resettingTotp ? t("AdminPage.totpResetting") : t("AdminPage.totpReset")}
              </button>
            )}
          </div>
        )}

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            {t("AdminPage.cancel")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading}>
            {loading ? t("AdminPage.saving") : t("AdminPage.save")}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

function UserRow({ user, currentUserId, onEdit, onDelete }) {
  const { t } = useTranslation("system");
  const ROLE_META = {
    student: { label: t("AdminPage.roleStudent"), icon: ROLE_ICONS.student },
    teacher: { label: t("AdminPage.roleTeacher"), icon: ROLE_ICONS.teacher },
    admin: { label: t("AdminPage.roleAdmin"), icon: ROLE_ICONS.admin },
  };
  const role = ROLE_META[user.role] ?? ROLE_META.student;
  const isSelf = user.id === currentUserId;

  return (
    <div className={styles.row}>
      <div className={styles.rowAvatar}>{userDisplayName(user).slice(0, 1).toUpperCase()}</div>
      <div className={styles.rowMain}>
        <span className={styles.rowName}>
          {userDisplayName(user)}
          {user.auth_source === "ldap" && (
            <span className={styles.ldapTag} title={t("AdminPage.ldapManagedHint")}>LDAP</span>
          )}
          {user.totp_enabled && (
            <span className={styles.ldapTag} title={t("AdminPage.totpEnabledHint")}>2FA</span>
          )}
        </span>
        <span className={styles.rowMeta}>{user.email}</span>
      </div>
      <div className={styles.rowTags}>
        <span className={`${styles.badge} ${styles[`badge_${user.role}`]}`}>
          <MIcon name={role.icon} size={13} />
          {role.label}
        </span>
        <span className={`${styles.statusBadge} ${user.is_active ? styles.statusActive : styles.statusInactive}`}>
          {user.is_active ? t("AdminPage.statusActive") : t("AdminPage.statusInactive")}
        </span>
      </div>
      <span className={styles.createdAt}>{formatDate(user.created_at)}</span>
      <div className={styles.rowActions}>
        <button type="button" className={styles.actionBtn} title={t("AdminPage.editTitle")} onClick={() => onEdit(user)}>
          <MIcon name="edit" size={16} />
        </button>
        <button
          type="button"
          className={styles.actionBtnDanger}
          title={isSelf ? t("AdminPage.deleteSelfTitle") : t("AdminPage.deleteTitle")}
          disabled={isSelf}
          onClick={() => onDelete(user)}
        >
          <MIcon name="delete" size={16} />
        </button>
      </div>
    </div>
  );
}

export default function AdminPage() {
  const { t } = useTranslation("system");
  const { user: currentUser } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const [users, setUsers] = useState([]);
  const [count, setCount] = useState(0);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [modal, setModal] = useState(null);
  const modalPresence  = useDialogPresence(modal);

  /** silent = true 時不觸發 loading 與錯誤提示，供背景自動刷新使用。
      逐頁取回全部使用者（原本寫死 limit 100，第 101 位之後在這頁根本管不到）；
      清單改由前端分頁呈現，搜尋因此能掃到全部帳號。 */
  const fetchUsers = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const data = await UsersService.listAll();
      setUsers(data);
      setCount(data.length);
    } catch (err) {
      if (!silent) toast.error(err?.message ?? t("AdminPage.toastLoadFailed"));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [toast, t]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);
  useAutoRefresh(() => fetchUsers(true));

  const visibleUsers = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return users;
    return users.filter((item) =>
      [item.email, item.full_name, item.role]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword)),
    );
  }, [query, users]);

  /* 前端分頁：搜尋過濾後再切頁；條件一改就回到第一頁 */
  const [page, setPage] = useState(0);
  useEffect(() => { setPage(0); }, [query]);
  const totalPages = Math.max(1, Math.ceil(visibleUsers.length / PAGE_SIZE));
  const pagedUsers = useMemo(
    () => visibleUsers.slice(Math.min(page, totalPages - 1) * PAGE_SIZE, (Math.min(page, totalPages - 1) + 1) * PAGE_SIZE),
    [visibleUsers, page, totalPages],
  );

  const stats = useMemo(() => ({
    active: users.filter((item) => item.is_active).length,
    admins: users.filter((item) => item.role === "admin").length,
    teachers: users.filter((item) => item.role === "teacher").length,
  }), [users]);

  async function handleSubmit(payload) {
    setSaving(true);
    try {
      if (modal?.mode === "edit") {
        const body = { ...payload };
        if (!body.password) delete body.password;
        const updated = await UsersService.update(modal.user.id, body);
        setUsers((prev) => prev.map((item) => item.id === updated.id ? updated : item));
        toast.success(t("AdminPage.toastUpdated"));
      } else {
        const created = await UsersService.create(payload);
        setUsers((prev) => [created, ...prev]);
        setCount((prev) => prev + 1);
        toast.success(t("AdminPage.toastCreated"));
      }
      setModal(null);
    } catch (err) {
      toast.error(err?.message ?? t("AdminPage.toastSaveFailed"));
    } finally {
      setSaving(false);
    }
  }

  /* 刪除確認走共用 useConfirm（樣式規範：勿自建本地 ConfirmModal），
     按下確認即關閉彈窗，結果以 toast 呈現 */
  async function handleDelete(user) {
    const ok = await confirm({
      title: t("AdminPage.deleteUserTitle"),
      message: t("AdminPage.deleteUserConfirm", { name: userDisplayName(user) }),
      confirmText: t("AdminPage.delete"),
      danger: true,
    });
    if (!ok) return;
    try {
      await UsersService.delete(user.id);
      setUsers((prev) => prev.filter((item) => item.id !== user.id));
      setCount((prev) => Math.max(prev - 1, 0));
      toast.success(t("AdminPage.toastDeleted"));
    } catch (err) {
      toast.error(err?.message ?? t("AdminPage.toastDeleteFailed"));
    }
  }

  /* 重設兩步驟驗證：對方會被登出、下次登入不再要求驗證碼（走共用 useConfirm 確認） */
  async function handleResetTotp(user) {
    const ok = await confirm({
      title: t("AdminPage.totpResetTitle"),
      message: t("AdminPage.totpResetConfirm", { name: userDisplayName(user) }),
      confirmText: t("AdminPage.totpReset"),
      danger: true,
    });
    if (!ok) return;
    try {
      await UsersService.resetTotp(user.id);
      setUsers((prev) =>
        prev.map((item) => (item.id === user.id ? { ...item, totp_enabled: false } : item)),
      );
      setModal((prev) =>
        prev?.user?.id === user.id ? { ...prev, user: { ...prev.user, totp_enabled: false } } : prev,
      );
      toast.success(t("AdminPage.toastTotpReset"));
    } catch (err) {
      toast.error(err?.message ?? t("AdminPage.toastTotpResetFailed"));
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("AdminPage.pageTitle")}>
        <button type="button" className={styles.btnPrimary} onClick={() => setModal({ mode: "create" })}>
          <MIcon name="person_add" size={16} />
          {t("AdminPage.addUser")}
        </button>
      </PageHeader>

      <div className={styles.summaryGrid}>
        <div className={styles.summaryItem}>
          <span>{t("AdminPage.statTotal")}</span>
          <strong>{count}</strong>
        </div>
        <div className={styles.summaryItem}>
          <span>{t("AdminPage.statActive")}</span>
          <strong>{stats.active}</strong>
        </div>
        <div className={styles.summaryItem}>
          <span>{t("AdminPage.statTeachers")}</span>
          <strong>{stats.teachers}</strong>
        </div>
        <div className={styles.summaryItem}>
          <span>{t("AdminPage.statAdmins")}</span>
          <strong>{stats.admins}</strong>
        </div>
      </div>

      <div className={styles.toolbar}>
        <div className={styles.searchBox}>
          <MIcon name="search" size={16} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("AdminPage.searchPlaceholder")}
          />
        </div>
      </div>

      <div className={styles.content}>
        {loading ? (
          <LoadingState fullPage text={t("AdminPage.loading")} />
        ) : visibleUsers.length === 0 ? (
          <EmptyState hasQuery={Boolean(query.trim())} />
        ) : (
          <>
            <div className={styles.list}>
              {pagedUsers.map((item) => (
                <UserRow
                  key={item.id}
                  user={item}
                  currentUserId={currentUser?.id}
                  onEdit={(target) => setModal({ mode: "edit", user: target })}
                  onDelete={handleDelete}
                />
              ))}
            </div>
            {totalPages > 1 && (
              <div className={styles.pagination}>
                <span className={styles.paginationInfo}>
                  {t("AdminPage.paginationInfo", { count: visibleUsers.length, page: Math.min(page, totalPages - 1) + 1, totalPages })}
                </span>
                <div className={styles.paginationBtns}>
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    disabled={page === 0}
                    onClick={() => setPage((p) => Math.max(p - 1, 0))}
                  >
                    <MIcon name="chevron_left" size={16} />
                    {t("AdminPage.prevPage")}
                  </button>
                  <button
                    type="button"
                    className={styles.btnSecondary}
                    disabled={page + 1 >= totalPages}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    {t("AdminPage.nextPage")}
                    <MIcon name="chevron_right" size={16} />
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {modalPresence.open && (
        <UserModal
          mode={modalPresence.item.mode}
          user={modalPresence.item.user}
          loading={saving}
          closing={modalPresence.closing}
          onClose={() => setModal(null)}
          onSubmit={handleSubmit}
          onResetTotp={handleResetTotp}
        />
      )}
    </div>
  );
}
