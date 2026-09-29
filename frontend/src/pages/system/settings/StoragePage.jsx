import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./settings.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { useToast } from "../../../hooks/useToast";
import { ProxmoxConfigService } from "../../../services/proxmoxConfig";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { joinList } from "../../../utils/joinList";

/**
 * Storage（系統管理 → Storage）：各 PVE Storage 的速度等級、使用者優先度與啟用狀態，
 * 放置演算法據此挑選磁碟。2026-09 從「系統設定」的分頁拆成獨立頁面。
 */

/* 使用者優先度範圍，與後端 StorageUpdate.user_priority（ge=1, le=10）一致 */
export const PRIORITY_MIN = 1;
export const PRIORITY_MAX = 10;

/**
 * 優先度輸入框的草稿 → 要送出的值；不合法（空白、非整數、超出範圍）回 null。
 * 輸入框只在失焦／Enter 時才送出，避免每按一鍵就存檔並鎖住輸入框（打 "15" 只存到 1）。
 */
export function parsePriorityDraft(raw) {
  const text = String(raw ?? "").trim();
  if (!/^\d+$/.test(text)) return null;
  const value = Number(text);
  if (!Number.isInteger(value) || value < PRIORITY_MIN || value > PRIORITY_MAX) return null;
  return value;
}

/* ── Storage ───────────────────────────────────────── */
function StorageList() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const [storages, setStorages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [savingId, setSavingId] = useState(null);
  const [status, setStatus] = useState("all"); // all | enabled | disabled
  const [query, setQuery] = useState("");

  useEffect(() => {
    ProxmoxConfigService.getStorages()
      .then(setStorages)
      .catch((err) => toast.error(err?.message ?? t("SettingsPage.toastLoadStorageFailed")))
      .finally(() => setLoading(false));
  }, [toast, t]);

  // 只有一組 PVE 連線時不必再標註連線名稱
  const multiConnection = useMemo(
    () => new Set(storages.map((s) => s.connection_id ?? null)).size > 1,
    [storages],
  );

  const counts = useMemo(() => {
    const enabled = storages.filter((s) => s.enabled).length;
    return { all: storages.length, enabled, disabled: storages.length - enabled };
  }, [storages]);

  // 搜尋比對名稱、節點（共享 Storage 比對全部節點）、連線名稱與類型
  const visibleStorages = useMemo(() => {
    const q = query.trim().toLowerCase();
    return storages.filter((s) => {
      if (status === "enabled" && !s.enabled) return false;
      if (status === "disabled" && s.enabled) return false;
      if (!q) return true;
      return [s.storage, s.node_name, ...(s.node_names ?? []), s.connection_name, s.storage_type]
        .some((v) => (v ?? "").toLowerCase().includes(q));
    });
  }, [storages, status, query]);

  const hasFilter = status !== "all" || query.trim() !== "";

  async function save(storage, patch) {
    setSavingId(storage.id);
    try {
      const updated = await ProxmoxConfigService.updateStorage(storage.id, {
        enabled: patch.enabled ?? storage.enabled,
        speed_tier: patch.speed_tier ?? storage.speed_tier,
        user_priority: patch.user_priority ?? storage.user_priority,
      });
      setStorages((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      const nodeCount = updated.node_names?.length ?? 1;
      toast.success(
        updated.is_shared && nodeCount > 1
          ? t("SettingsPage.toastStorageUpdatedMultiNode", { storage: storage.storage, count: nodeCount })
          : t("SettingsPage.toastStorageUpdated", { storage: storage.storage }),
      );
      return true;
    } catch (err) {
      toast.error(err?.message ?? t("SettingsPage.toastUpdateStorageFailed"));
      return false;
    } finally {
      setSavingId(null);
    }
  }

  /* 優先度草稿在失焦／Enter 才提交；不合法或沒變就還原成已存的值，存檔失敗也還原 */
  async function commitPriority(storage, input) {
    if (savingId === storage.id) return;
    const next = parsePriorityDraft(input.value);
    if (next === null || next === storage.user_priority) {
      input.value = String(storage.user_priority);
      return;
    }
    const ok = await save(storage, { user_priority: next });
    if (!ok && input.isConnected) input.value = String(storage.user_priority);
  }

  return (
    <>
      {/* 篩選同需求審核頁：分段切換（附數量）＋搜尋框同一列 */}
      <div className={styles.toolbar}>
        <SegmentedControl
          className={styles.filterControl}
          options={["all", "enabled", "disabled"].map((key) => ({
            value: key,
            label: key === "all" ? t("SettingsPage.storageFilterAll") : t(`SettingsPage.${key}`),
            badge: loading ? undefined : counts[key],
          }))}
          value={status}
          onChange={setStatus}
          ariaLabel={t("SettingsPage.storageFilterLabel")}
        />
        <div className={styles.search}>
          <MIcon name="search" size={16} />
          <input
            type="text"
            className={styles.searchInput}
            placeholder={t("SettingsPage.storageSearchPlaceholder")}
            aria-label={t("SettingsPage.storageSearchPlaceholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      <div className={styles.content}>
        {loading ? (
          <LoadingState text={t("SettingsPage.loadingStorage")} />
        ) : visibleStorages.length === 0 ? (
          hasFilter ? (
            <EmptyState icon="search_off" title={t("SettingsPage.storageNoResult")} />
          ) : (
            <EmptyState icon="storage" title={t("SettingsPage.emptyNoStorageConfig")} />
          )
        ) : (
          <div className={styles.list}>
            {visibleStorages.map((storage) => (
              <div key={storage.id} className={styles.storageRow}>
                <span className={styles.listAvatar} aria-hidden="true">
                  <MIcon name="storage" size={20} />
                </span>
                <div className={styles.rowMain}>
                  <span className={styles.rowName}>
                    {storage.storage}
                    {multiConnection && storage.connection_name && (
                      <span className={`${styles.badge} ${styles.badge_muted}`}>{storage.connection_name}</span>
                    )}
                    {storage.is_shared ? (
                      <span
                        className={`${styles.badge} ${styles.badge_info}`}
                        title={joinList(storage.node_names ?? [])}
                      >
                        {t("SettingsPage.sharedNodeCount", { count: storage.node_names?.length ?? 1 })}
                      </span>
                    ) : (
                      <span className={`${styles.badge} ${styles.badge_muted}`}>{storage.node_name}</span>
                    )}
                  </span>
                  <span className={styles.rowMeta}>
                    {storage.storage_type ?? "?"} · {Math.round(storage.used_gb)} / {Math.round(storage.total_gb)} GB ·
                    {" "}{[storage.can_vm && "VM", storage.can_lxc && "LXC", storage.can_iso && "ISO", storage.can_backup && "Backup"].filter(Boolean).join(" / ") || t("SettingsPage.noPurpose")}
                  </span>
                </div>
                <label className={styles.inlineField}>
                  <span>{t("SettingsPage.speedTierTitle")}</span>
                  <select
                    value={storage.speed_tier}
                    disabled={savingId === storage.id}
                    onChange={(e) => save(storage, { speed_tier: e.target.value })}
                    className={styles.inlineSelect}
                  >
                    <option value="nvme">NVMe</option>
                    <option value="ssd">SSD</option>
                    <option value="hdd">HDD</option>
                    <option value="unknown">{t("SettingsPage.unknown")}</option>
                  </select>
                </label>
                <label className={styles.inlineField}>
                  <span>{t("SettingsPage.userPriorityTitle")}</span>
                  {/* 非受控＋key 帶已存值：存檔成功後以新值重建，輸入中不受 re-render 影響；
                      存檔中不 disabled（會失焦吃掉後續按鍵），只設 readOnly */}
                  <input
                    key={`${storage.id}:${storage.user_priority}`}
                    type="number"
                    min={PRIORITY_MIN}
                    max={PRIORITY_MAX}
                    step={1}
                    className={styles.inlineInput}
                    defaultValue={storage.user_priority}
                    readOnly={savingId === storage.id}
                    onBlur={(e) => commitPriority(storage, e.currentTarget)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        e.currentTarget.blur();
                      }
                    }}
                  />
                </label>
                <label className={styles.checkRow}>
                  <input
                    type="checkbox"
                    checked={storage.enabled}
                    disabled={savingId === storage.id}
                    onChange={(e) => save(storage, { enabled: e.target.checked })}
                  />
                  <span>{t("SettingsPage.enable")}</span>
                </label>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function StoragePage() {
  const { t } = useTranslation("system");
  return (
    <div className={styles.page}>
      <PageHeader title={t("SettingsPage.storageTitle")} />
      <StorageList />
    </div>
  );
}
