import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./AdminPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import { UsersService } from "../../../services/users";

const ROLE_OPTIONS = [
  { value: "student", label: "學生" },
  { value: "teacher", label: "教師" },
  { value: "admin", label: "管理者" },
];

const ROLE_META = {
  student: { label: "學生", icon: "school" },
  teacher: { label: "教師", icon: "co_present" },
  admin: { label: "管理者", icon: "admin_panel_settings" },
};

function initialForm(user = null) {
  return {
    email: user?.email ?? "",
    full_name: user?.full_name ?? "",
    password: "",
    role: user?.role ?? "student",
    is_active: user?.is_active ?? true,
    is_superuser: user?.is_superuser ?? false,
  };
}

function userDisplayName(user) {
  return user.full_name || user.email;
}

function formatDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function EmptyState({ hasQuery }) {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name={hasQuery ? "search_off" : "manage_accounts"} size={40} />
      </div>
      <h2 className={styles.emptyTitle}>{hasQuery ? "找不到使用者" : "尚無使用者"}</h2>
      <p className={styles.emptyDesc}>
        {hasQuery ? "請調整搜尋關鍵字或清除篩選。" : "點擊新增使用者建立第一個帳戶。"}
      </p>
    </div>
  );
}

function UserModal({ mode, user, loading, onClose, onSubmit }) {
  const [form, setForm] = useState(() => initialForm(user));
  const isEdit = mode === "edit";

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
      is_superuser: form.is_superuser,
    };
    if (form.password.trim()) payload.password = form.password;
    onSubmit(payload);
  }

  return (
    <div className={styles.modalOverlay} onMouseDown={onClose}>
      <form className={styles.modal} onSubmit={submit} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>{isEdit ? "編輯使用者" : "新增使用者"}</h2>
            <p>{isEdit ? "調整帳戶狀態與角色。" : "建立可登入 Campus Cloud 的帳戶。"}</p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label="關閉">
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
            <span>姓名</span>
            <input
              value={form.full_name}
              onChange={(e) => setField("full_name", e.target.value)}
              maxLength={255}
              placeholder="可留空"
            />
          </label>

          <label className={styles.field}>
            <span>{isEdit ? "新密碼" : "密碼"}</span>
            <input
              type="password"
              value={form.password}
              onChange={(e) => setField("password", e.target.value)}
              minLength={8}
              maxLength={128}
              required={!isEdit}
              placeholder={isEdit ? "留空表示不變更" : "至少 8 個字元"}
            />
          </label>

          <label className={styles.field}>
            <span>角色</span>
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
            <span>啟用帳戶</span>
          </label>
          <label className={styles.checkRow}>
            <input
              type="checkbox"
              checked={form.is_superuser}
              onChange={(e) => setField("is_superuser", e.target.checked)}
            />
            <span>超級管理者</span>
          </label>
        </div>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            取消
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading}>
            {loading ? "儲存中..." : "儲存"}
          </button>
        </div>
      </form>
    </div>
  );
}

function ConfirmDelete({ user, loading, onClose, onConfirm }) {
  return (
    <div className={styles.modalOverlay} onMouseDown={onClose}>
      <div className={styles.confirm} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.confirmIcon}>
          <MIcon name="warning" size={24} />
        </div>
        <h2>刪除使用者</h2>
        <p>
          確定要刪除 <strong>{userDisplayName(user)}</strong> 嗎？此操作會一併清理該使用者的申請紀錄；若仍持有已開通資源，後端會拒絕刪除。
        </p>
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            取消
          </button>
          <button type="button" className={styles.btnDanger} disabled={loading} onClick={onConfirm}>
            {loading ? "刪除中..." : "刪除"}
          </button>
        </div>
      </div>
    </div>
  );
}

function UserRow({ user, currentUserId, onEdit, onDelete }) {
  const role = ROLE_META[user.role] ?? ROLE_META.student;
  const isSelf = user.id === currentUserId;

  return (
    <div className={styles.row}>
      <div className={styles.rowAvatar}>{userDisplayName(user).slice(0, 1).toUpperCase()}</div>
      <div className={styles.rowMain}>
        <span className={styles.rowName}>{userDisplayName(user)}</span>
        <span className={styles.rowMeta}>{user.email}</span>
      </div>
      <span className={`${styles.badge} ${styles[`badge_${user.role}`]}`}>
        <MIcon name={role.icon} size={13} />
        {role.label}
      </span>
      {user.is_superuser && <span className={`${styles.badge} ${styles.badge_super}`}>Superuser</span>}
      <span className={`${styles.statusBadge} ${user.is_active ? styles.statusActive : styles.statusInactive}`}>
        {user.is_active ? "啟用" : "停用"}
      </span>
      <span className={styles.createdAt}>{formatDate(user.created_at)}</span>
      <div className={styles.rowActions}>
        <button type="button" className={styles.actionBtn} title="編輯" onClick={() => onEdit(user)}>
          <MIcon name="edit" size={16} />
        </button>
        <button
          type="button"
          className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
          title={isSelf ? "不能刪除自己" : "刪除"}
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

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await UsersService.list({ limit: 100 });
      setUsers(res?.data ?? []);
      setCount(res?.count ?? 0);
    } catch (err) {
      toast.error(err?.message ?? "載入使用者失敗");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const visibleUsers = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return users;
    return users.filter((item) =>
      [item.email, item.full_name, item.role]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword)),
    );
  }, [query, users]);

  const stats = useMemo(() => ({
    active: users.filter((item) => item.is_active).length,
    admins: users.filter((item) => item.role === "admin" || item.is_superuser).length,
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
        toast.success("使用者已更新");
      } else {
        const created = await UsersService.create(payload);
        setUsers((prev) => [created, ...prev]);
        setCount((prev) => prev + 1);
        toast.success("使用者已建立");
      }
      setModal(null);
    } catch (err) {
      toast.error(err?.message ?? "儲存失敗");
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
      toast.success("使用者已刪除");
      setDeleteTarget(null);
    } catch (err) {
      toast.error(err?.message ?? "刪除失敗");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>使用者管理</h1>
          <p className={styles.pageSubtitle}>管理使用者帳戶、角色與登入狀態</p>
        </div>
        <button type="button" className={styles.btnPrimary} onClick={() => setModal({ mode: "create" })}>
          <MIcon name="person_add" size={16} />
          新增使用者
        </button>
      </div>

      <div className={styles.summaryGrid}>
        <div className={styles.summaryItem}>
          <span>總使用者</span>
          <strong>{count}</strong>
        </div>
        <div className={styles.summaryItem}>
          <span>啟用中</span>
          <strong>{stats.active}</strong>
        </div>
        <div className={styles.summaryItem}>
          <span>教師</span>
          <strong>{stats.teachers}</strong>
        </div>
        <div className={styles.summaryItem}>
          <span>管理者</span>
          <strong>{stats.admins}</strong>
        </div>
      </div>

      <div className={styles.toolbar}>
        <div className={styles.searchBox}>
          <MIcon name="search" size={16} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜尋姓名、Email 或角色"
          />
        </div>
        <button type="button" className={styles.btnSecondary} onClick={fetchUsers} disabled={loading}>
          <MIcon name="refresh" size={16} />
          重新整理
        </button>
      </div>

      <div className={styles.content}>
        {loading ? (
          <div className={styles.loading}>載入使用者...</div>
        ) : visibleUsers.length === 0 ? (
          <EmptyState hasQuery={Boolean(query.trim())} />
        ) : (
          <div className={styles.list}>
            {visibleUsers.map((item) => (
              <UserRow
                key={item.id}
                user={item}
                currentUserId={currentUser?.id}
                onEdit={(target) => setModal({ mode: "edit", user: target })}
                onDelete={setDeleteTarget}
              />
            ))}
          </div>
        )}
      </div>

      {modal && (
        <UserModal
          mode={modal.mode}
          user={modal.user}
          loading={saving}
          onClose={() => setModal(null)}
          onSubmit={handleSubmit}
        />
      )}

      {deleteTarget && (
        <ConfirmDelete
          user={deleteTarget}
          loading={deleting}
          onClose={() => setDeleteTarget(null)}
          onConfirm={handleDelete}
        />
      )}
    </div>
  );
}
