import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./QuotasPage.module.scss";
import pageStyles from "./settings.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { QuotasService } from "../../../services/quotas";
import { UsersService } from "../../../services/users";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { formatDateTime } from "../../../utils/formatDate";
import RelatedSettingsNav from "./RelatedSettingsNav";

/**
 * 配額（系統管理 → 配額）：全域預設上限 + 個別使用者覆寫。
 * 2026-09 曾短暫併入「系統設定」的分頁，同月隨系統設定拆分回到獨立的 /quotas。
 */

const FIELD_KEYS = ["max_cpu_cores", "max_memory_mb", "max_disk_gb", "max_instances"];

const PICKER_MAX_ROWS = 50;

/** 上限顯示：0 = 無限制。 */
function fmtLimit(value, t, unit = "") {
  if (Number(value) === 0) return t("QuotasTab.unlimited");
  return unit ? `${value} ${unit}` : String(value);
}

function useNumberFields() {
  const { t } = useTranslation("system");
  // fallback：取消「無限制」勾選時回填的預設值；0 = 無限制
  return [
    { key: "max_cpu_cores", label: "CPU cores", min: 1, max: 256, fallback: 8 },
    { key: "max_memory_mb", label: t("QuotasTab.fieldMemoryMb"), min: 256, max: 1048576, fallback: 16384 },
    { key: "max_disk_gb", label: t("QuotasTab.fieldDiskGb"), min: 1, max: 65536, fallback: 100 },
    { key: "max_instances", label: t("QuotasTab.fieldInstances"), min: 1, max: 100, fallback: 5 },
  ];
}

/** 只取出與基準值不同的欄位，配合後端 partial 更新。 */
function changedFields(form, baseline) {
  return FIELD_KEYS.reduce((acc, key) => {
    if (Number(form[key]) !== Number(baseline[key])) acc[key] = Number(form[key]);
    return acc;
  }, {});
}

function pickNumbers(source) {
  return FIELD_KEYS.reduce((acc, key) => {
    acc[key] = Number(source?.[key] ?? 0);
    return acc;
  }, {});
}

/** 表單值正規化：清空的輸入框回退基準值，其餘轉數字。 */
function normNumbers(form, baseline) {
  return FIELD_KEYS.reduce((acc, key) => {
    const v = form[key];
    acc[key] = v === "" || Number.isNaN(Number(v)) ? Number(baseline[key]) : Number(v);
    return acc;
  }, {});
}

/** 上限輸入欄：數字輸入 + 無限制勾選（對應 0）。 */
function LimitField({ idPrefix, field, value, onChange }) {
  const { t } = useTranslation("system");
  const { key, label, min, max, fallback } = field;
  const unlimited = value === 0;
  return (
    <div className={styles.field}>
      <label htmlFor={`${idPrefix}-${key}`}>{label}</label>
      <input
        id={`${idPrefix}-${key}`}
        type="number"
        min={min}
        max={max}
        value={unlimited ? "" : value}
        placeholder={unlimited ? t("QuotasTab.unlimited") : undefined}
        disabled={unlimited}
        onChange={(e) =>
          onChange(e.target.value === "" ? "" : Number(e.target.value))
        }
      />
      <label className={styles.unlimitedToggle}>
        <input
          type="checkbox"
          checked={unlimited}
          onChange={(e) => onChange(e.target.checked ? 0 : fallback)}
        />
        {t("QuotasTab.unlimited")}
      </label>
    </div>
  );
}

function formatUser(user) {
  return user?.full_name ? `${user.full_name}（${user.email}）` : (user?.email ?? "");
}

/* ── 可搜尋的使用者選擇欄位 ───────────────────────────────────────────── */
function UserPicker({ users, loading, value, onChange }) {
  const { t } = useTranslation("system");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const list = useDialogPresence(open, 130);
  const wrapRef = useRef(null);

  const selected = users.find((u) => u.id === value) ?? null;

  useEffect(() => {
    const onClickOutside = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const hit = q
      ? users.filter(
          (u) =>
            (u.email ?? "").toLowerCase().includes(q) ||
            (u.full_name ?? "").toLowerCase().includes(q),
        )
      : users;
    return hit.slice(0, PICKER_MAX_ROWS);
  }, [users, query]);

  const handlePick = (user) => {
    onChange(user.id);
    setQuery("");
    setOpen(false);
  };

  return (
    <div className={styles.field} ref={wrapRef}>
      <label htmlFor="quota-user">{t("QuotasTab.userLabel")}</label>
      <div className={styles.picker}>
        <input
          id="quota-user"
          autoComplete="off"
          value={query || (selected ? formatUser(selected) : "")}
          placeholder={loading ? t("QuotasTab.loadingUsers") : t("QuotasTab.userSearchPlaceholder")}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            if (value) onChange("");
          }}
        />
        {list.open && (
          <ul className={`${styles.pickerList} ${list.closing ? styles.pickerListOut : ""}`}>
            {matches.length === 0 ? (
              <li className={styles.pickerEmpty}>
                {loading ? t("QuotasTab.loading") : t("QuotasTab.noMatchingUsers")}
              </li>
            ) : (
              matches.map((user) => (
                <li key={user.id}>
                  <button
                    type="button"
                    className={styles.pickerItem}
                    onClick={() => handlePick(user)}
                  >
                    <span className={styles.pickerPrimary}>{user.email}</span>
                    {user.full_name && (
                      <span className={styles.pickerSecondary}>{user.full_name}</span>
                    )}
                  </button>
                </li>
              ))
            )}
          </ul>
        )}
      </div>
    </div>
  );
}

/* ── 新增／編輯個人覆寫 ───────────────────────────────────────────────── */
function QuotaDialog({ mode, quota, candidates, loadingUsers, defaults, closing = false, onClose, onSaved }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const NUMBER_FIELDS = useNumberFields();
  const isEdit = mode === "edit";
  const baseline = useMemo(
    () => pickNumbers(isEdit ? quota : defaults),
    [isEdit, quota, defaults],
  );
  const [userId, setUserId] = useState("");
  const [form, setForm] = useState(baseline);
  const [saving, setSaving] = useState(false);

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  const handleSave = async () => {
    setSaving(true);
    try {
      if (isEdit) {
        const patch = changedFields(normNumbers(form, baseline), baseline);
        if (Object.keys(patch).length === 0) {
          onClose();
          return;
        }
        await QuotasService.update(quota.id, patch);
        toast.success(t("QuotasTab.toastUpdated"));
      } else {
        await QuotasService.create({
          user_id: userId,
          ...normNumbers(form, baseline),
        });
        toast.success(t("QuotasTab.toastCreated"));
      }
      onSaved();
    } catch (e) {
      toast.error(e?.message ?? (isEdit ? t("QuotasTab.toastUpdateFailed") : t("QuotasTab.toastCreateFailed")));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onClick={onClose}
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <span className={styles.modalTitle}>
          <MIcon name="data_usage" size={18} />
          {isEdit ? t("QuotasTab.editQuota") : t("QuotasTab.addQuota")}
        </span>

        {isEdit ? (
          <div className={styles.field}>
            <label htmlFor="quota-target">{t("QuotasTab.userLabel")}</label>
            <input id="quota-target" value={quota.user_email ?? quota.user_id} disabled />
          </div>
        ) : (
          <UserPicker
            users={candidates}
            loading={loadingUsers}
            value={userId}
            onChange={setUserId}
          />
        )}

        <div className={styles.formGrid}>
          {NUMBER_FIELDS.map((field) => (
            <LimitField
              key={field.key}
              idPrefix="quota"
              field={field}
              value={form[field.key]}
              onChange={(value) => setField(field.key, value)}
            />
          ))}
        </div>

        {!isEdit && (
          <p className={styles.hint}>
            {t("QuotasTab.createHint")}
          </p>
        )}

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnGhost} onClick={onClose}>
            {t("QuotasTab.cancel")}
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={saving || (!isEdit && !userId)}
            onClick={handleSave}
          >
            {saving ? t("QuotasTab.saving") : isEdit ? t("QuotasTab.save") : t("QuotasTab.create")}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── 全域預設配額卡片 ─────────────────────────────────────────────────── */
function GlobalQuotaCard({ config, onSaved }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const NUMBER_FIELDS = useNumberFields();
  const baseline = useMemo(() => pickNumbers(config), [config]);
  const [form, setForm] = useState(baseline);
  const [saving, setSaving] = useState(false);

  useEffect(() => setForm(baseline), [baseline]);

  const patch = changedFields(normNumbers(form, baseline), baseline);
  const dirty = Object.keys(patch).length > 0;

  const handleSave = async () => {
    setSaving(true);
    try {
      onSaved(await QuotasService.updateGlobal(patch));
      toast.success(t("QuotasTab.toastGlobalUpdated"));
    } catch (e) {
      toast.error(e?.message ?? t("QuotasTab.toastUpdateFailed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className={styles.card}>
      <header className={styles.cardHeader}>
        <div className={styles.cardHeading}>
          <span className={styles.cardTitle}>
            <MIcon name="tune" size={18} />
            {t("QuotasTab.globalQuotaTitle")}
          </span>
          <p className={styles.cardSubtitle}>
            {t("QuotasTab.globalQuotaDesc")}
          </p>
        </div>
        {config?.updated_at && (
          <span className={styles.updatedAt}>
            {t("QuotasTab.lastUpdated")} {formatDateTime(config.updated_at)}
          </span>
        )}
      </header>

      <div className={styles.cardBody}>
        <div className={styles.formGrid}>
          {NUMBER_FIELDS.map((field) => (
            <LimitField
              key={field.key}
              idPrefix="global"
              field={field}
              value={form[field.key]}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, [field.key]: value }))
              }
            />
          ))}
        </div>

        <div className={styles.cardActions}>
          {dirty && (
            <button
              type="button"
              className={styles.btnGhost}
              disabled={saving}
              onClick={() => setForm(baseline)}
            >
              {t("QuotasTab.restore")}
            </button>
          )}
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={!dirty || saving}
            onClick={handleSave}
          >
            {saving ? t("QuotasTab.saving") : t("QuotasTab.save")}
          </button>
        </div>
      </div>
    </section>
  );
}

function QuotasSection() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const [quotas, setQuotas] = useState(null);
  const [globalQuota, setGlobalQuota] = useState(null);
  const [users, setUsers] = useState([]);
  const [loadingUsers, setLoadingUsers] = useState(true);
  const [dialog, setDialog] = useState(null);
  const dialogPresence = useDialogPresence(dialog);
  const [deleting, setDeleting] = useState(null);

  const load = useCallback(async () => {
    try {
      const [list, config] = await Promise.all([
        QuotasService.list(),
        QuotasService.getGlobal(),
      ]);
      setQuotas(list);
      setGlobalQuota(config);
    } catch (e) {
      toast.error(e?.message ?? t("QuotasTab.toastLoadFailed"));
      setQuotas((prev) => prev ?? []);
    }
  }, [toast, t]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    UsersService.listAll()
      .then((list) => {
        if (!cancelled) setUsers(list);
      })
      .catch(() => {
        if (!cancelled) setUsers([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingUsers(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const candidates = useMemo(() => {
    const taken = new Set((quotas ?? []).map((q) => q.user_id));
    return users.filter((u) => !taken.has(u.id));
  }, [users, quotas]);

  const handleDelete = async (quota) => {
    const target = quota.user_email ?? quota.id;
    const ok = await confirm({
      title: t("QuotasTab.deleteQuotaTitle"),
      message: t("QuotasTab.deleteQuotaMessage", { target }),
      confirmText: t("QuotasTab.delete"),
      danger: true,
    });
    if (!ok) return;
    setDeleting(quota.id);
    try {
      await QuotasService.remove(quota.id);
      toast.success(t("QuotasTab.toastDeleted"));
      load();
    } catch (e) {
      toast.error(e?.message ?? t("QuotasTab.toastDeleteFailed"));
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className={styles.stack}>
      {globalQuota && <GlobalQuotaCard config={globalQuota} onSaved={setGlobalQuota} />}

      <section className={styles.card}>
        <header className={styles.cardHeader}>
          <div className={styles.cardHeading}>
            <span className={styles.cardTitle}>
              <MIcon name="manage_accounts" size={18} />
              {t("QuotasTab.overridesTitle")}
            </span>
            <p className={styles.cardSubtitle}>
              {t("QuotasTab.overridesDesc")}
            </p>
          </div>
          <button
            type="button"
            className={styles.btnPrimary}
            onClick={() => setDialog({ mode: "create" })}
          >
            <MIcon name="add" size={16} />
            {t("QuotasTab.addQuota")}
          </button>
        </header>

        {quotas === null ? (
          <LoadingState />
        ) : quotas.length === 0 ? (
          <EmptyState icon="data_usage" title={t("QuotasTab.emptyNoOverrides")} />
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>{t("QuotasTab.colScope")}</th>
                <th>{t("QuotasTab.colTarget")}</th>
                <th>CPU</th>
                <th>{t("QuotasTab.fieldMemoryMb")}</th>
                <th>{t("QuotasTab.fieldDiskGb")}</th>
                <th>{t("QuotasTab.colInstanceCount")}</th>
                <th className={styles.thRight}>{t("QuotasTab.colActions")}</th>
              </tr>
            </thead>
            <tbody>
              {quotas.map((q) => (
                <tr key={q.id}>
                  <td>
                    <span className={`${styles.badge} ${styles.badge_user}`}>{t("QuotasTab.personalOverride")}</span>
                  </td>
                  <td>{q.user_email ?? "—"}</td>
                  <td>{fmtLimit(q.max_cpu_cores, t)}</td>
                  <td>{fmtLimit(q.max_memory_mb, t)}</td>
                  <td>{fmtLimit(q.max_disk_gb, t)}</td>
                  <td>{fmtLimit(q.max_instances, t)}</td>
                  <td className={styles.tdRight}>
                    <div className={styles.rowActions}>
                      <button
                        type="button"
                        className={styles.btnIcon}
                        onClick={() => setDialog({ mode: "edit", quota: q })}
                        title={t("QuotasTab.editQuotaTitle")}
                      >
                        <MIcon name="edit" size={16} />
                      </button>
                      <button
                        type="button"
                        className={styles.btnDanger}
                        disabled={deleting === q.id}
                        onClick={() => handleDelete(q)}
                        title={t("QuotasTab.deleteQuotaTitle")}
                      >
                        <MIcon name="delete" size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {dialogPresence.open && (
        <QuotaDialog
          mode={dialogPresence.item.mode}
          quota={dialogPresence.item.quota}
          candidates={candidates}
          loadingUsers={loadingUsers}
          defaults={globalQuota}
          closing={dialogPresence.closing}
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null);
            load();
          }}
        />
      )}
    </div>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function QuotasPage() {
  const { t } = useTranslation("system");
  return (
    <div className={pageStyles.page}>
      <PageHeader title={t("SettingsPage.quotasTitle")} subtitle={t("SettingsPage.quotasSubtitle")} />
      <RelatedSettingsNav current="quotas" />
      <div className={pageStyles.content}>
        <QuotasSection />
      </div>
    </div>
  );
}
