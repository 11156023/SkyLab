import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./AdminPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { UsersService } from "../../../services/users";
import PageHeader from "../../../components/PageHeader/PageHeader";
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

function UserModal({ mode, user, loading, closing = false, onClose, onSubmit }) {
  const { t } = useTranslation("system");
  const [form, setForm] = useState(() => initialForm(user));
  const isEdit = mode === "edit";
  /* LDAP 帳號的密碼歸目錄管：本地密碼欄位鎖住（後端也會擋），稽核 #9 */
  const isLdap = isEdit && user?.auth_source === "ldap";
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
    };
    if (!isLdap && form.password.trim()) payload.password = form.password;
    onSubmit(payload);
  }

  return (
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onMouseDown={onClose}
    >
      <form className={styles.modal} onSubmit={submit} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>{isEdit ? t("AdminPage.modalEditTitle") : t("AdminPage.modalCreateTitle")}</h2>
            <p>{isEdit ? t("AdminPage.modalEditSubtitle") : t("AdminPage.modalCreateSubtitle")}</p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label={t("AdminPage.close")}>
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
            <input
              type="password"
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
        </div>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            {t("AdminPage.cancel")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading}>
            {loading ? t("AdminPage.saving") : t("AdminPage.save")}
          </button>
        </div>
      </form>
    </div>
  );
}

function ConfirmDelete({ user, loading, closing = false, onClose, onConfirm }) {
  const { t } = useTranslation("system");
  return (
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onMouseDown={onClose}
    >
      <div className={styles.confirm} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.confirmIcon}>
          <MIcon name="warning" size={24} />
        </div>
        <h2>{t("AdminPage.deleteUserTitle")}</h2>
        <p>
          {t("AdminPage.deleteUserConfirm", { name: userDisplayName(user) })}
        </p>
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            {t("AdminPage.cancel")}
          </button>
          <button type="button" className={styles.btnDanger} disabled={loading} onClick={onConfirm}>
            {loading ? t("AdminPage.deleting") : t("AdminPage.delete")}
          </button>
        </div>
      </div>
    </div>
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
        </span>
        <span className={styles.rowMeta}>{user.email}</span>
      </div>
      <span className={`${styles.badge} ${styles[`badge_${user.role}`]}`}>
        <MIcon name={role.icon} size={13} />
        {role.label}
      </span>
      <span className={`${styles.statusBadge} ${user.is_active ? styles.statusActive : styles.statusInactive}`}>
        {user.is_active ? t("AdminPage.statusActive") : t("AdminPage.statusInactive")}
      </span>
      <span className={styles.createdAt}>{formatDate(user.created_at)}</span>
      <div className={styles.rowActions}>
        <button type="button" className={styles.actionBtn} title={t("AdminPage.editTitle")} onClick={() => onEdit(user)}>
          <MIcon name="edit" size={16} />
        </button>
        <button
          type="button"
          className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
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
  const [users, setUsers] = useState([]);
  const [count, setCount] = useState(0);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [modal, setModal] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const modalPresence  = useDialogPresence(modal);
  const deletePresence = useDialogPresence(deleteTarget);

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

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await UsersService.delete(deleteTarget.id);
      setUsers((prev) => prev.filter((item) => item.id !== deleteTarget.id));
      setCount((prev) => Math.max(prev - 1, 0));
      toast.success(t("AdminPage.toastDeleted"));
      setDeleteTarget(null);
    } catch (err) {
      toast.error(err?.message ?? t("AdminPage.toastDeleteFailed"));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("AdminPage.pageTitle")} subtitle={t("AdminPage.pageSubtitle")}>
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
                  onDelete={setDeleteTarget}
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
        />
      )}

      {deletePresence.open && (
        <ConfirmDelete
          user={deletePresence.item}
          loading={deleting}
          closing={deletePresence.closing}
          onClose={() => setDeleteTarget(null)}
          onConfirm={handleDelete}
        />
      )}
    </div>
  );
}
