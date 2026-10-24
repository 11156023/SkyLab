import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./QuotasSection.module.scss";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import DataTable, { DataTableCells } from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { QuotasService } from "../../../services/quotas";
import { useGlobalQuotaQuery, useQuotaCacheUpdater, useQuotasQuery } from "../../../queries/quotas";
import { useAllUsersQuery } from "../../../queries/users";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import { useLoadError, useLoadFailureMessage, useQueryView } from "../../../hooks/useQueryLoadState";
import useDialogPresence from "../../../hooks/useDialogPresence";
import useOutsideClick from "../../../hooks/useOutsideClick";
import { formatDateTime } from "../../../utils/formatDate";
import { isAdminUser } from "../../../utils/roles";

/**
 * 配額（資源政策頁的下半段）：全域預設上限 + 個別使用者覆寫。
 * 2026-09 曾短暫併入「系統設定」的分頁，同月隨系統設定拆分回到獨立的 /quotas；
 * 2026-10 與治理合併成「資源政策」一頁（/quotas 導向 /governance），這裡只輸出區段。
 */

const FIELD_KEYS = ["max_cpu_cores", "max_memory_mb", "max_disk_gb", "max_instances"];

const PICKER_MAX_ROWS = 50;

const EMPTY = Object.freeze([]);


/** 上限顯示：0 = 無限制。 */
function fmtLimit(value, t, unit = "") {
  if (Number(value) === 0) return t("QuotasTab.unlimited");
  return unit ? `${value} ${unit}` : String(value);
}

function useNumberFields() {
  const { t } = useTranslation("system");
  // fallback：取消「無限制」勾選時回填的預設值；0 = 無限制
  return [
    { key: "max_cpu_cores", label: t("QuotasTab.fieldCpuCores"), min: 1, max: 256, fallback: 8 },
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

  const close = useCallback(() => setOpen(false), []);
  useOutsideClick(wrapRef, null, close);

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

  /* 外框（遮罩、標題列、Esc、焦點、捲動鎖）交給共用 Modal；儲存中 Esc／點遮罩／× 都不關 */
  return (
    <Modal
      closing={closing}
      onClose={onClose}
      busy={saving}
      closeButton
      size="md"
      title={isEdit ? t("QuotasTab.editQuota") : t("QuotasTab.addQuota")}
      actions={
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>
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
        </>
      }
    >
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
    </Modal>
  );
}

/* ── 全域預設配額卡片 ─────────────────────────────────────────────────── */
function GlobalQuotaCard({ config, onSaved }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const NUMBER_FIELDS = useNumberFields();
  /* 刪改個人覆寫後頁面會重抓全域設定（每次都是新物件）：只有數值真的變了才重設表單，
     否則全域卡片上還沒儲存的修改會被默默洗掉 */
  const baselineKey = FIELD_KEYS.map((key) => Number(config?.[key] ?? 0)).join(",");
  const baseline = useMemo(() => pickNumbers(config), [baselineKey]); // eslint-disable-line react-hooks/exhaustive-deps
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
    <section className={styles.card} data-guide="governance-quota-global">
      <header className={styles.cardHeader}>
        <div className={styles.cardHeading}>
          <span className={styles.cardTitle}>
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
              className={styles.btnSecondary}
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

/* ── 個人覆寫清單 ─────────────────────────────────────────────────────── */

/** 表頭文字（header 是元件，可以直接用 hook） */
function ColTitle({ name }) {
  const { t } = useTranslation("system");
  return t(`QuotasTab.${name}`);
}

function LimitCell({ value }) {
  const { t } = useTranslation("system");
  return fmtLimit(value, t);
}

/** 編輯／刪除；處理函式在點擊當下才從 table meta 取，拿到的一定是區段最新的那一份 */
function QuotaRowActions({ quota, deleting, table }) {
  const { t } = useTranslation("system");
  return (
    <div className={styles.rowActions}>
      <button
        type="button"
        className={styles.btnIcon}
        onClick={() => table.options.meta.onEdit(quota)}
        title={t("QuotasTab.editQuotaTitle")}
        aria-label={t("QuotasTab.editQuotaTitle")}
      >
        <MIcon name="edit" size={16} />
      </button>
      <button
        type="button"
        className={styles.btnIconDanger}
        disabled={deleting}
        onClick={() => table.options.meta.onDelete(quota)}
        title={t("QuotasTab.deleteQuotaTitle")}
        aria-label={t("QuotasTab.deleteQuotaTitle")}
      >
        <MIcon name="delete" size={16} />
      </button>
    </div>
  );
}

const columnHelper = createColumnHelper();

/* 欄位定義放模組層級：FlexRender 把函式型的 header／cell 當元件掛載，render 裡新建會整格重新掛載 */
const quotaColumns = columnHelper.columns([
  columnHelper.accessor("user_email", {
    header: () => <ColTitle name="colTarget" />,
    cell: ({ row }) => row.original.user_email ?? "—",
    meta: { thClassName: styles.colTarget },
  }),
  columnHelper.accessor("max_cpu_cores", {
    header: "CPU",
    cell: ({ row }) => <LimitCell value={row.original.max_cpu_cores} />,
    meta: { thClassName: styles.num, tdClassName: styles.num },
  }),
  columnHelper.accessor("max_memory_mb", {
    header: () => <ColTitle name="fieldMemoryMb" />,
    cell: ({ row }) => <LimitCell value={row.original.max_memory_mb} />,
    meta: { thClassName: styles.num, tdClassName: styles.num },
  }),
  columnHelper.accessor("max_disk_gb", {
    header: () => <ColTitle name="fieldDiskGb" />,
    cell: ({ row }) => <LimitCell value={row.original.max_disk_gb} />,
    meta: { thClassName: styles.num, tdClassName: styles.num },
  }),
  columnHelper.accessor("max_instances", {
    header: () => <ColTitle name="colInstanceCount" />,
    cell: ({ row }) => <LimitCell value={row.original.max_instances} />,
    meta: { thClassName: styles.num, tdClassName: styles.num },
  }),
  columnHelper.display({
    id: "actions",
    header: () => <ColTitle name="colActions" />,
    /* 刪除中的那一列停用刪除鈕：deleting 是區段的狀態，經 rowState 傳進來（只改 table meta 不會讓儲存格重繪） */
    cell: ({ row, table, rowState }) => (
      <QuotaRowActions quota={row.original} deleting={rowState.deleting} table={table} />
    ),
  }),
]);

export default function QuotasSection() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const [dialog, setDialog] = useState(null);
  const dialogPresence = useDialogPresence(dialog);
  const [deleting, setDeleting] = useState(null);

  /* 個人覆寫、全域預設與使用者清單走 TanStack Query；原本只在進頁與增刪改後重抓，不背景輪詢。
     增刪改後 invalidate 讓清單與全域設定一起重抓（全域卡片只在數值真的變了才重設表單） */
  const quotasQuery = useQuotasQuery();
  const globalQuery = useGlobalQuotaQuery();
  const usersQuery = useAllUsersQuery({ refetchInterval: false });
  const quotaCache = useQuotaCacheUpdater();
  /* 載入失敗：提示一次，覆寫清單改顯示空狀態（與原本相同），全域卡片沒資料就不顯示。
     從沒載到就失敗時看 useLoadError：重抓期間維持空狀態、不閃回載入中 */
  const quotasLoad = useLoadError(quotasQuery);
  const quotas = quotasQuery.data ?? (quotasLoad.error ? EMPTY : null);
  const globalQuota = globalQuery.data ?? null;
  /* 提示依訊息字串觸發（useLoadFailureMessage）：同一串失敗（含切回視窗重抓又失敗）只提示一次，成功過再失敗才再提示 */
  const quotasFailedMessage = useLoadFailureMessage(quotasQuery, t("Error.generic", { ns: "common" }));
  const globalFailedMessage = useLoadFailureMessage(globalQuery, t("Error.generic", { ns: "common" }));
  const loadFailedMessage = quotasFailedMessage ?? globalFailedMessage;
  useEffect(() => {
    if (loadFailedMessage) toast.error(loadFailedMessage);
  }, [loadFailedMessage, toast]);
  /* 使用者清單失敗照舊靜默（挑選器變空清單） */
  const users = usersQuery.data ?? EMPTY;
  /* 首載失敗後切回視窗重抓時挑選器不閃回載入中（沒資料時 v5 每次重抓（切回視窗）都把 status 改回 pending、清掉 error） */
  const loadingUsers = useQueryView(usersQuery).loading;

  const candidates = useMemo(() => {
    const taken = new Set((quotas ?? []).map((q) => q.user_id));
    // 管理員不受配額限制，後端也不讓替管理員設個人配額
    return users.filter((u) => !taken.has(u.id) && !isAdminUser(u));
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
      quotaCache.invalidate();
    } catch (e) {
      toast.error(e?.message ?? t("QuotasTab.toastDeleteFailed"));
    } finally {
      setDeleting(null);
    }
  };

  const table = useTable({
    features: baseFeatures,
    columns: quotaColumns,
    data: quotas ?? EMPTY,
    getRowId: (q) => String(q.id),
    meta: { onEdit: (quota) => setDialog({ mode: "edit", quota }), onDelete: handleDelete },
  });

  return (
    <div className={styles.stack}>
      {globalQuota && <GlobalQuotaCard config={globalQuota} onSaved={(updated) => quotaCache.setGlobal(updated)} />}

      <section className={styles.card} data-guide="governance-quota-overrides">
        <header className={styles.cardHeader}>
          <div className={styles.cardHeading}>
            <span className={styles.cardTitle}>
              {t("QuotasTab.overridesTitle")}
            </span>
          </div>
          {/* 有覆寫才在卡片標題列放「新增」；沒有時空狀態那顆就是唯一入口，載入中也先不放 */}
          {quotas?.length > 0 && (
            <button
              type="button"
              className={styles.btnPrimary}
              onClick={() => setDialog({ mode: "create" })}
            >
              <MIcon name="add" size={16} />
              {t("QuotasTab.addQuota")}
            </button>
          )}
        </header>

        {quotas === null ? (
          <LoadingState />
        ) : quotas.length === 0 ? (
          <EmptyState icon="data_usage" title={t("QuotasTab.emptyNoOverrides")}
            action={<button type="button" className={styles.btnPrimary} onClick={() => setDialog({ mode: "create" })}><MIcon name="add" size={16} />{t("QuotasTab.addQuota")}</button>} />
        ) : (
          <div className={styles.tableScroll}>
            <DataTable
              table={table}
              className={styles.table}
              renderRow={(row) => (
                <tr key={row.id}>
                  <DataTableCells row={row} rowState={{ deleting: deleting === row.original.id }} />
                </tr>
              )}
            />
          </div>
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
            quotaCache.invalidate();
          }}
        />
      )}
    </div>
  );
}
