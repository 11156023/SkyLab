import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./AdminPage.module.scss";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import { useLoadError, useQueryErrorEffect, useQueryView } from "../../../hooks/useQueryLoadState";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { UsersService } from "../../../services/users";
import { UserGroupsService } from "../../../services/userGroups";
import {
  useAllUsersQuery,
  usePendingRegistrationsQuery,
  useRegistrationPolicyQuery,
  useUserCacheUpdater,
} from "../../../queries/users";
import { LIST_REFRESH_INTERVAL } from "../../../queries/queryClient";
import { useUserGroupCacheUpdater, useUserGroupMembersQuery, useUserGroupsQuery } from "../../../queries/userGroups";
import PageHeader from "../../../components/PageHeader/PageHeader";
import PasswordInput from "../../../components/PasswordInput/PasswordInput";
import PasswordRules from "../../../components/PasswordRules/PasswordRules";
import DataToolbar from "../../../components/DataToolbar/DataToolbar";
import BatchBar, { BatchAction, BatchDivider } from "../../../components/DataToolbar/BatchBar";
import SelectCheckbox from "../../../components/DataToolbar/SelectCheckbox";
import ToolbarSearch from "../../../components/DataToolbar/ToolbarSearch";
import useRowSelection from "../../../hooks/useRowSelection";
import Pagination from "../shared/Pagination";
import { formatDate } from "../../../utils/formatDate";
import { joinList } from "../../../utils/joinList";
import { isPasswordStrong } from "../../../utils/passwordPolicy";
import { runBatch } from "../../../utils/runBatch";
import { GroupManagerDialog, GroupPickDialog } from "./UserGroupDialogs";

const ROLE_ICONS = {
  student: "school",
  teacher: "co_present",
  admin: "admin_panel_settings",
};

const PAGE_SIZE = 50;
/* 批量確認框裡最多列出幾位的名字，其餘寫「等 N 位」 */
const BATCH_NAME_PREVIEW = 5;

const EMPTY = Object.freeze([]);

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

function EmptyState({ hasQuery, onClearSearch, onCreate }) {
  const { t } = useTranslation("system");
  return (
    <SharedEmptyState
      icon={hasQuery ? "search_off" : "manage_accounts"}
      title={hasQuery ? t("AdminPage.emptyNoResult") : t("AdminPage.emptyNone")}
      action={hasQuery
        ? <button type="button" className={styles.btnSecondary} onClick={onClearSearch}><MIcon name="search_off" size={16} />{t("EmptyState.clearSearch", { ns: "common" })}</button>
        : <button type="button" className={styles.btnPrimary} onClick={onCreate}><MIcon name="person_add" size={16} />{t("AdminPage.addUser")}</button>}
    />
  );
}

/**
 * 使用者表單 → API payload。
 * 編輯自己的帳號時不送 role／is_active：比照不能刪除自己，避免管理員把自己停用或降級而被鎖在外面
 * （後端 PATCH 是部分更新，沒送的欄位維持原值）。
 */
export function buildUserPayload(form, { isLdap = false, isSelf = false } = {}) {
  const payload = {
    email: form.email.trim(),
    full_name: form.full_name.trim() || null,
    totp_required: form.totp_required,
  };
  if (!isSelf) {
    payload.role = form.role;
    payload.is_active = form.is_active;
  }
  if (!isLdap && form.password.trim()) payload.password = form.password;
  return payload;
}

function UserModal({ mode, user, isSelf = false, loading, closing = false, onClose, onSubmit, onResetTotp }) {
  const { t } = useTranslation("system");
  const [form, setForm] = useState(() => initialForm(user));
  const isEdit = mode === "edit";
  const [resettingTotp, setResettingTotp] = useState(false);
  /* 送出時密碼不合複雜度規則：規則清單把未滿足的項目標紅 */
  const [rulesFlagged, setRulesFlagged] = useState(false);

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
    const payload = buildUserPayload(form, { isLdap, isSelf: isEdit && isSelf });
    /* 編輯時密碼留空＝不變更，payload 不帶 password 就不檢查 */
    if (payload.password !== undefined && !isPasswordStrong(payload.password)) {
      setRulesFlagged(true);
      return;
    }
    onSubmit(payload);
  }

  /* 外框（遮罩、標題列、Esc、焦點、捲動鎖）交給共用 Modal；送出中 Esc／點遮罩／× 都不關 */
  return (
    <Modal
      as="form"
      onSubmit={submit}
      closing={closing}
      onClose={onClose}
      busy={loading}
      closeButton
      size="md"
      closeProps={{ "data-guide": "admin-user-modal-close" }}
      title={isEdit ? t("AdminPage.modalEditTitle") : t("AdminPage.modalCreateTitle")}
      actions={
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
            {t("AdminPage.cancel")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading}>
            {loading ? t("AdminPage.saving") : t("AdminPage.save")}
          </button>
        </>
      }
    >
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
            maxLength={128}
            required={!isEdit}
            disabled={isLdap}
            placeholder={
              isLdap
                ? t("AdminPage.ldapManagedPlaceholder")
                : isEdit ? t("AdminPage.passwordUnchangedHint") : t("AdminPage.passwordPlaceholder")
            }
          />
          {isLdap && <em className={styles.fieldHint}>{t("AdminPage.ldapManagedHint")}</em>}
          {!isLdap && (!isEdit || form.password) && (
            <PasswordRules password={form.password} invalid={rulesFlagged} />
          )}
        </label>

        <label className={styles.field}>
          <span>{t("AdminPage.fieldRole")}</span>
          <select
            value={form.role}
            onChange={(e) => setField("role", e.target.value)}
            disabled={isEdit && isSelf}
            title={isEdit && isSelf ? t("AdminPage.selfEditLockedHint") : undefined}
          >
            {ROLE_OPTIONS.map((role) => (
              <option key={role.value} value={role.value}>{role.label}</option>
            ))}
          </select>
        </label>
      </div>

      <div className={styles.toggleGrid} data-guide="admin-user-security">
        <label
          className={styles.checkRow}
          title={isEdit && isSelf ? t("AdminPage.selfEditLockedHint") : undefined}
        >
          <input
            type="checkbox"
            checked={form.is_active}
            onChange={(e) => setField("is_active", e.target.checked)}
            disabled={isEdit && (isSelf || user?.registration_status !== "approved")}
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
      {isEdit && isSelf && (
        <em className={styles.fieldHint}>{t("AdminPage.selfEditLockedHint")}</em>
      )}
      {isEdit && user?.registration_status !== "approved" && (
        <em className={styles.fieldHint}>{t("AdminPage.registrationActivationHint")}</em>
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
    </Modal>
  );
}

function UserRow({ user, currentUserId, selected = false, selecting = false, onToggleSelect, onRowClick, onEdit, onDelete }) {
  const { t } = useTranslation("system");
  const ROLE_META = {
    student: { label: t("AdminPage.roleStudent"), icon: ROLE_ICONS.student },
    teacher: { label: t("AdminPage.roleTeacher"), icon: ROLE_ICONS.teacher },
    admin: { label: t("AdminPage.roleAdmin"), icon: ROLE_ICONS.admin },
  };
  const role = ROLE_META[user.role] ?? ROLE_META.student;
  const isSelf = user.id === currentUserId;

  return (
    /* 勾選狀態下點整列也是勾選（編輯、刪除鈕照舊） */
    <div
      className={`${styles.row} ${selected ? styles.rowSelected : ""} ${selecting && !isSelf ? styles.rowSelectMode : ""}`}
      onClick={(event) => onRowClick?.(user.id, event)}
      data-guide="admin-user-row"
    >
      {/* 自己不能勾（不能停用自己），格子照留，頭像才對得齊 */}
      <div className={styles.rowCheck}>
        {!isSelf && (
          <SelectCheckbox
            checked={selected}
            onToggle={(shiftKey) => onToggleSelect(user.id, shiftKey)}
            ariaLabel={t("AdminPage.selectRowAria", { name: userDisplayName(user) })}
          />
        )}
      </div>
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
          {user.registration_status === "pending_email"
            ? t("AdminPage.statusPendingEmail")
            : user.registration_status === "pending_review"
              ? t("AdminPage.statusPendingReview")
              : user.registration_status === "rejected"
                ? t("AdminPage.statusRejected")
                : user.is_active ? t("AdminPage.statusActive") : t("AdminPage.statusInactive")}
        </span>
      </div>
      <span className={styles.createdAt}>{formatDate(user.created_at)}</span>
      <div className={styles.rowActions}>
        <button type="button" className={styles.actionBtn} title={t("AdminPage.editTitle")} onClick={() => onEdit(user)} data-guide="admin-user-edit">
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
  const [policySaving, setPolicySaving] = useState(false);
  const [studentEmailDomainDraft, setStudentEmailDomainDraft] = useState(null);
  const [reviewingId, setReviewingId] = useState(null);
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [modal, setModal] = useState(null);
  const modalPresence  = useDialogPresence(modal);

  /* 使用者清單、註冊政策與待審核註冊走 TanStack Query：背景 30 秒刷新、分頁隱藏時暫停。
     service 逐頁取回全部使用者（原本寫死 limit 100，第 101 位之後在這頁根本管不到）；
     清單改由前端分頁呈現，搜尋因此能掃到全部帳號。
     較早送出卻較晚回來的舊清單由 Query 作廢（新的重抓會取消舊的），
     增刪改經 useUserCacheUpdater 就地寫入，寫入前先取消還在路上的刷新 */
  const usersQuery = useAllUsersQuery();
  const pendingQuery = usePendingRegistrationsQuery();
  /* 開關是按下就存、沒有草稿，跟著清單一起背景刷新 */
  const policyQuery = useRegistrationPolicyQuery({ refetchInterval: LIST_REFRESH_INTERVAL });
  const userCache = useUserCacheUpdater();
  const users = usersQuery.data ?? EMPTY;
  const registrationPolicy = policyQuery.data ?? null;
  const pendingRegistrations = pendingQuery.data?.data ?? EMPTY;
  /* 載入失敗只在手上沒資料時提示（背景刷新失敗維持靜默、沿用舊資料）。首載失敗看 useQueryView／useLoadError：
     沒資料時 v5 每次重抓都把 status 改回 pending、清掉 error，直接看 isError 會在每次背景重試時閃回整頁載入中、
     重跳同一則 toast；useLoadError 的錯誤在重試期間不清掉，依訊息字串觸發，同一串失敗只提示一次 */
  const usersView = useQueryView(usersQuery);
  const loading = usersView.loading;
  const usersLoadErrorMessage = usersView.error
    ? usersView.error?.message ?? t("Error.generic", { ns: "common" })
    : null;
  useEffect(() => {
    if (usersLoadErrorMessage) toast.error(usersLoadErrorMessage);
  }, [usersLoadErrorMessage, toast]);
  const policyLoadError = useLoadError(policyQuery).error;
  const pendingLoadError = useLoadError(pendingQuery).error;
  const registrationLoadError = policyLoadError ?? pendingLoadError;
  const registrationLoadErrorMessage = registrationLoadError
    ? registrationLoadError?.message ?? t("AdminPage.registrationLoadFailed")
    : null;
  useEffect(() => {
    if (registrationLoadErrorMessage) toast.error(registrationLoadErrorMessage);
  }, [registrationLoadErrorMessage, toast]);

  /* 群組（例如一個班）：篩選選單、群組管理對話框、批量加入／移出共用同一份清單，跟著使用者清單一起背景刷新 */
  const groupsQuery = useUserGroupsQuery({ refetchInterval: LIST_REFRESH_INTERVAL });
  const groupCache = useUserGroupCacheUpdater();
  const groups = groupsQuery.data ?? EMPTY;
  const groupsLoadError = useLoadError(groupsQuery).error;
  const groupsLoadErrorMessage = groupsLoadError
    ? groupsLoadError?.message ?? t("UserGroups.loadFailed")
    : null;
  useEffect(() => {
    if (groupsLoadErrorMessage) toast.error(groupsLoadErrorMessage);
  }, [groupsLoadErrorMessage, toast]);
  const [groupFilter, setGroupFilter] = useState("");
  /* 篩選中的群組被刪掉了（自己或別的管理員刪的）：清單回來後就當作沒選，回到全部使用者 */
  const activeGroupId = groupsQuery.data && !groups.some((group) => group.id === groupFilter) ? "" : groupFilter;
  const membersQuery = useUserGroupMembersQuery(activeGroupId);
  useQueryErrorEffect(membersQuery, (err) => {
    toast.error(err?.message ?? t("UserGroups.loadFailed"));
    setGroupFilter("");
  });
  const [showGroupManager, setShowGroupManager] = useState(false);
  const groupManagerPresence = useDialogPresence(showGroupManager);
  const [groupPick, setGroupPick] = useState(null); // { mode: "add" | "remove", count }
  const groupPickPresence = useDialogPresence(groupPick);
  const [groupSaving, setGroupSaving] = useState(false);

  async function saveRegistrationPolicy(patch) {
    if (!registrationPolicy || policySaving) return false;
    setPolicySaving(true);
    try {
      const saved = await UsersService.saveRegistrationPolicy({ ...registrationPolicy, ...patch });
      await userCache.setRegistrationPolicy(saved);
      toast.success(t("AdminPage.registrationSettingsSaved"));
      return true;
    } catch (err) {
      toast.error(err?.message ?? t("AdminPage.toastSaveFailed"));
      return false;
    } finally {
      setPolicySaving(false);
    }
  }

  /* 學號信箱網域是文字欄位：編輯中保留草稿（背景刷新不蓋掉），存檔成功才回到顯示已存的值 */
  const savedStudentEmailDomain = registrationPolicy?.student_email_domain ?? "";
  const studentEmailDomain = studentEmailDomainDraft ?? savedStudentEmailDomain;
  const studentEmailDomainChanged = studentEmailDomain.trim() !== savedStudentEmailDomain;

  async function saveStudentEmailDomain(event) {
    event.preventDefault();
    if (!studentEmailDomainChanged) return;
    if (await saveRegistrationPolicy({ student_email_domain: studentEmailDomain.trim() || null })) {
      setStudentEmailDomainDraft(null);
    }
  }

  async function reviewRegistration(user, approve) {
    if (reviewingId) return;
    setReviewingId(user.id);
    try {
      const updated = await UsersService.reviewRegistration(user.id, approve);
      await Promise.all([userCache.removePendingRegistration(user.id), userCache.update(updated)]);
      toast.success(t(approve ? "AdminPage.registrationApproved" : "AdminPage.registrationRejected"));
    } catch (err) {
      toast.error(err?.message ?? t("AdminPage.toastSaveFailed"));
    } finally {
      setReviewingId(null);
    }
  }

  const memberData = membersQuery.data;
  const visibleUsers = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    /* 選了群組但成員還沒載回來時先顯示空清單，不要閃一下全部使用者 */
    const memberIds = activeGroupId ? new Set((memberData ?? EMPTY).map((member) => member.id)) : null;
    if (!keyword && !memberIds) return users;
    return users.filter((item) =>
      (!memberIds || memberIds.has(item.id))
      && (!keyword || [item.email, item.full_name, item.role]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword))),
    );
  }, [query, users, activeGroupId, memberData]);

  /* 前端分頁：搜尋過濾後再切頁；條件一改就回到第一頁 */
  const [page, setPage] = useState(0);
  useEffect(() => { setPage(0); }, [query, activeGroupId]);
  const totalPages = Math.max(1, Math.ceil(visibleUsers.length / PAGE_SIZE));
  /* 背景刷新後人數變少時 page 可能超出範圍，切片、頁碼與按鈕停用一律用夾過的值 */
  const safePage = Math.min(page, totalPages - 1);
  const pagedUsers = useMemo(
    () => visibleUsers.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE),
    [visibleUsers, safePage],
  );

  const stats = useMemo(() => ({
    active: users.filter((item) => item.is_active).length,
    admins: users.filter((item) => item.role === "admin").length,
    teachers: users.filter((item) => item.role === "teacher").length,
  }), [users]);

  /* 批量停用／啟用：只勾得到這一頁、不是自己的帳號；翻頁或搜尋換了，看不到的自動取消勾選 */
  const selection = useRowSelection(
    pagedUsers.filter((item) => item.id !== currentUser?.id).map((item) => item.id),
  );
  const selectedUsers = pagedUsers.filter((item) => selection.isSelected(item.id));
  const [batchProgress, setBatchProgress] = useState(null); // { label, done, total }

  /* 停用只送啟用中的、啟用只送停用中的；不適用的略過 */
  const batchTargets = (active) => selectedUsers.filter((item) => item.is_active !== active);

  function batchHint(active) {
    const n = batchTargets(active).length;
    if (n === 0) return t("AdminPage.batchNotApplicable");
    return n < selectedUsers.length
      ? t("AdminPage.batchApplicableHint", { count: n, skipped: selectedUsers.length - n })
      : undefined;
  }

  async function runBatchActive(active) {
    const targets = batchTargets(active);
    if (!targets.length || batchProgress) return;
    const label = active ? t("AdminPage.batchEnable") : t("AdminPage.batchDisable");
    /* 停用會讓對方登不進來：先確認並列出名字（最多 5 位） */
    if (!active) {
      const names = targets.map(userDisplayName);
      const rest = names.length - BATCH_NAME_PREVIEW;
      const ok = await confirm({
        title: t("AdminPage.batchDisableTitle", { count: targets.length }),
        message: `${t("AdminPage.batchDisableDesc")}\n${joinList(names.slice(0, BATCH_NAME_PREVIEW))}${rest > 0 ? ` ${t("AdminPage.batchNamesMore", { count: rest })}` : ""}`,
        confirmText: label,
        danger: true,
      });
      if (!ok) return;
    }
    setBatchProgress({ label, done: 0, total: targets.length });
    const res = await runBatch(targets, (item) => UsersService.update(item.id, { is_active: active }), {
      onProgress: ({ done, total }) => setBatchProgress((prev) => prev && { ...prev, done, total }),
    });
    await userCache.updateMany(res.results.filter((r) => r.ok && r.value).map((r) => r.value));
    if (res.failed === 0) {
      toast.success(t("AdminPage.batchDoneToast", { count: res.succeeded, label }));
    } else {
      toast.error(t("AdminPage.batchFailedKeptToast", { label, succeeded: res.succeeded, failed: res.failed }));
    }
    /* 成功的取消勾選，失敗的留著可以直接再試 */
    selection.replace(res.results.filter((r) => !r.ok).map((r) => r.item.id));
    setBatchProgress(null);
  }

  /* 批量刪除：規則同單筆刪除（帳號無法恢復、仍持有資源的後端會拒絕），逐筆呼叫同一支 API，
     先確認並列出名字（最多 5 位）；失敗的保留勾選，錯誤原因在 toast 裡講 */
  async function runBatchDelete() {
    const targets = selectedUsers;
    if (!targets.length || batchProgress) return;
    const label = t("AdminPage.delete");
    const names = targets.map(userDisplayName);
    const rest = names.length - BATCH_NAME_PREVIEW;
    const ok = await confirm({
      title: t("AdminPage.batchDeleteTitle", { count: targets.length }),
      message: `${t("AdminPage.batchDeleteDesc")}\n${joinList(names.slice(0, BATCH_NAME_PREVIEW))}${rest > 0 ? ` ${t("AdminPage.batchNamesMore", { count: rest })}` : ""}`,
      confirmText: label,
      danger: true,
    });
    if (!ok) return;
    setBatchProgress({ label, done: 0, total: targets.length });
    const res = await runBatch(targets, (item) => UsersService.delete(item.id), {
      onProgress: ({ done, total }) => setBatchProgress((prev) => prev && { ...prev, done, total }),
    });
    /* 成功的先就地拿掉，再整份重抓（刪帳號會連帶影響其他欄位與待審清單） */
    await userCache.removeMany(res.results.filter((r) => r.ok).map((r) => r.item.id));
    userCache.invalidate();
    groupCache.invalidate();
    if (res.failed === 0) {
      toast.success(t("AdminPage.batchDoneToast", { count: res.succeeded, label }));
    } else {
      const firstError = res.results.find((r) => !r.ok)?.error;
      toast.error(t("AdminPage.batchFailedKeptToast", { label, succeeded: res.succeeded, failed: res.failed })
        + (firstError?.message ? `\n${firstError.message}` : ""));
    }
    selection.replace(res.results.filter((r) => !r.ok).map((r) => r.item.id));
    setBatchProgress(null);
  }

  async function submitGroupPick(group) {
    const ids = selectedUsers.map((item) => item.id);
    if (!ids.length || !groupPick || groupSaving) return;
    const isAdd = groupPick.mode === "add";
    setGroupSaving(true);
    try {
      const res = isAdd
        ? await UserGroupsService.addMembers(group.id, { userIds: ids })
        : await UserGroupsService.removeMembers(group.id, ids);
      toast.success(isAdd
        ? t("UserGroups.addedToast", { name: group.name, count: res.added })
        : t("UserGroups.removedToast", { name: group.name, count: res.removed }));
      setGroupPick(null);
      selection.clear();
      await groupCache.invalidate();
    } catch (err) {
      toast.error(err?.message ?? t("UserGroups.saveFailed"));
    } finally {
      setGroupSaving(false);
    }
  }

  async function handleSubmit(payload) {
    setSaving(true);
    try {
      if (modal?.mode === "edit") {
        const updated = await UsersService.update(modal.user.id, payload);
        await userCache.update(updated);
        toast.success(t("AdminPage.toastUpdated"));
      } else {
        const created = await UsersService.create(payload);
        await userCache.prepend(created);
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
      await userCache.remove(user.id);
      groupCache.invalidate();
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
      await userCache.patchUser(user.id, { totp_enabled: false });
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
        <button type="button" className={styles.btnSecondary} data-guide="admin-groups" onClick={() => setShowGroupManager(true)}>
          <MIcon name="groups" size={16} />
          {t("UserGroups.manage")}
        </button>
        <button type="button" className={styles.btnPrimary} onClick={() => setModal({ mode: "create" })}>
          <MIcon name="person_add" size={16} />
          {t("AdminPage.addUser")}
        </button>
      </PageHeader>

      <section className={styles.registrationPanel} aria-label={t("AdminPage.registrationSettingsTitle")} data-guide="admin-registration">
        <h2>{t("AdminPage.registrationSettingsTitle")}</h2>
        <label className={styles.registrationOption}>
          <span>
            <strong>{t("AdminPage.signupEnabled")}</strong>
            <small>{t("AdminPage.signupEnabledHint")}</small>
          </span>
          <input type="checkbox" checked={Boolean(registrationPolicy?.signup_enabled)}
            disabled={!registrationPolicy || policySaving}
            onChange={(event) => saveRegistrationPolicy({ signup_enabled: event.target.checked })} />
        </label>
        <label className={styles.registrationOption}>
          <span>
            <strong>{t("AdminPage.approvalRequired")}</strong>
            <small>{t("AdminPage.approvalRequiredHint")}</small>
          </span>
          <input type="checkbox" checked={Boolean(registrationPolicy?.approval_required)}
            disabled={!registrationPolicy || policySaving}
            onChange={(event) => saveRegistrationPolicy({ approval_required: event.target.checked })} />
        </label>
        <label className={styles.registrationOption}>
          <span>
            <strong>{t("AdminPage.googleSignupEnabled")}</strong>
            <small>{t("AdminPage.googleSignupEnabledHint")}</small>
          </span>
          <input type="checkbox" checked={Boolean(registrationPolicy?.google_signup_enabled)}
            disabled={!registrationPolicy || policySaving}
            onChange={(event) => saveRegistrationPolicy({ google_signup_enabled: event.target.checked })} />
        </label>
        <form className={styles.registrationOption} onSubmit={saveStudentEmailDomain}>
          <label htmlFor="admin-student-email-domain">
            <strong>{t("AdminPage.studentEmailDomain")}</strong>
            <small>{t("AdminPage.studentEmailDomainHint")}</small>
          </label>
          <div className={styles.domainField}>
            <input id="admin-student-email-domain" type="text" inputMode="url" autoComplete="off" spellCheck={false}
              placeholder="school.edu.tw" value={studentEmailDomain}
              disabled={!registrationPolicy || policySaving}
              onChange={(event) => setStudentEmailDomainDraft(event.target.value)} />
            <button type="submit" className={styles.btnSecondary}
              disabled={!registrationPolicy || policySaving || !studentEmailDomainChanged}>
              {t("AdminPage.save")}
            </button>
          </div>
        </form>
      </section>

      <section className={styles.registrationPanel} aria-label={t("AdminPage.pendingRegistrationsTitle")} data-guide="admin-pending">
        <h2>{t("AdminPage.pendingRegistrationsTitle")} ({pendingRegistrations.length})</h2>
        {pendingRegistrations.length === 0 ? (
          <p className={styles.registrationEmpty}>{t("AdminPage.noPendingRegistrations")}</p>
        ) : pendingRegistrations.map((item) => (
          <div className={styles.pendingRow} key={item.id}>
            <span><strong>{userDisplayName(item)}</strong><small>{item.email}</small></span>
            <div>
              <button type="button" className={styles.btnSecondary}
                disabled={Boolean(reviewingId)} onClick={() => reviewRegistration(item, false)}>
                {t("AdminPage.rejectRegistration")}
              </button>
              <button type="button" className={styles.btnPrimary}
                disabled={Boolean(reviewingId)} onClick={() => reviewRegistration(item, true)}>
                {t("AdminPage.approveRegistration")}
              </button>
            </div>
          </div>
        ))}
      </section>

      <div className={styles.summaryGrid} data-guide="admin-summary">
        <div className={styles.summaryItem}>
          <span>{t("AdminPage.statTotal")}</span>
          <strong>{users.length}</strong>
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

      {/* 平常是搜尋；勾選後原地換成批量停用／啟用（同一個高度，清單不會跳） */}
      <DataToolbar
        selecting={selectedUsers.length > 0}
        batch={(
          <BatchBar
            countLabel={t("AdminPage.selectedCount", { count: selectedUsers.length })}
            onClear={selection.clear}
            selectAll={{
              checked: selection.allSelected,
              indeterminate: selection.someSelected,
              onToggle: selection.toggleAll,
            }}
            progress={batchProgress && {
              text: t("AdminPage.batchRunning", batchProgress),
              done: batchProgress.done,
              total: batchProgress.total,
            }}
          >
            <BatchAction
              icon="check_circle"
              label={t("AdminPage.batchEnable")}
              disabled={batchTargets(true).length === 0}
              title={batchHint(true)}
              onClick={() => runBatchActive(true)}
            />
            <BatchAction
              icon="block"
              label={t("AdminPage.batchDisable")}
              disabled={batchTargets(false).length === 0}
              title={batchHint(false)}
              onClick={() => runBatchActive(false)}
            />
            <BatchDivider />
            <BatchAction
              icon="group_add"
              label={t("UserGroups.batchAdd")}
              disabled={groups.length === 0}
              title={groups.length === 0 ? t("UserGroups.emptyCreateFirst") : undefined}
              onClick={() => setGroupPick({ mode: "add", count: selectedUsers.length })}
            />
            <BatchAction
              icon="group_remove"
              label={t("UserGroups.batchRemove")}
              disabled={groups.length === 0}
              title={groups.length === 0 ? t("UserGroups.emptyCreateFirst") : undefined}
              onClick={() => setGroupPick({ mode: "remove", count: selectedUsers.length })}
            />
            <BatchDivider />
            <BatchAction
              icon="delete"
              label={t("AdminPage.delete")}
              danger
              onClick={runBatchDelete}
            />
          </BatchBar>
        )}
      >
        {groups.length > 0 && (
          <select
            className={styles.groupFilter}
            value={activeGroupId}
            onChange={(event) => setGroupFilter(event.target.value)}
            aria-label={t("UserGroups.filterAria")}
            data-guide="admin-group-filter"
          >
            <option value="">{t("UserGroups.filterAll")}</option>
            {groups.map((group) => (
              <option key={group.id} value={group.id}>
                {t("UserGroups.filterOption", { name: group.name, count: group.member_count })}
              </option>
            ))}
          </select>
        )}
        <ToolbarSearch value={query} onChange={setQuery} placeholder={t("AdminPage.searchPlaceholder")} />
      </DataToolbar>

      <div className={styles.content}>
        {loading ? (
          <LoadingState fullPage text={t("AdminPage.loading")} />
        ) : visibleUsers.length === 0 ? (
          <EmptyState hasQuery={Boolean(query.trim() || activeGroupId)} onClearSearch={() => { setQuery(""); setGroupFilter(""); }} onCreate={() => setModal({ mode: "create" })} />
        ) : (
          <>
            <div className={styles.list}>
              {pagedUsers.map((item) => (
                <UserRow
                  key={item.id}
                  user={item}
                  currentUserId={currentUser?.id}
                  selected={selection.isSelected(item.id)}
                  selecting={selection.selecting}
                  onToggleSelect={selection.toggle}
                  onRowClick={selection.rowClick}
                  onEdit={(target) => setModal({ mode: "edit", user: target })}
                  onDelete={handleDelete}
                />
              ))}
            </div>
            {totalPages > 1 && (
              <Pagination
                page={safePage}
                totalPages={totalPages}
                info={t("AdminPage.paginationInfo", { count: visibleUsers.length, page: safePage + 1, totalPages })}
                prevLabel={t("AdminPage.prevPage")}
                nextLabel={t("AdminPage.nextPage")}
                onChange={setPage}
              />
            )}
          </>
        )}
      </div>

      {modalPresence.open && (
        <UserModal
          mode={modalPresence.item.mode}
          user={modalPresence.item.user}
          isSelf={Boolean(currentUser?.id) && modalPresence.item.user?.id === currentUser.id}
          loading={saving}
          closing={modalPresence.closing}
          onClose={() => setModal(null)}
          onSubmit={handleSubmit}
          onResetTotp={handleResetTotp}
        />
      )}

      {groupManagerPresence.open && (
        <GroupManagerDialog
          groups={groups}
          closing={groupManagerPresence.closing}
          onClose={() => setShowGroupManager(false)}
          onChanged={() => groupCache.invalidate()}
        />
      )}

      {groupPickPresence.open && (
        <GroupPickDialog
          mode={groupPickPresence.item.mode}
          groups={groups}
          defaultGroupId={activeGroupId}
          count={groupPickPresence.item.count}
          busy={groupSaving}
          closing={groupPickPresence.closing}
          onClose={() => setGroupPick(null)}
          onSubmit={submitGroupPick}
        />
      )}
    </div>
  );
}
