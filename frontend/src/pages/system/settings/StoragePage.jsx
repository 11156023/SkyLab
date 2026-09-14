import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./settings.module.scss";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { useToast } from "../../../hooks/useToast";
import { ProxmoxConfigService } from "../../../services/proxmoxConfig";
import PageHeader from "../../../components/PageHeader/PageHeader";

/**
 * Storage（系統管理 → Storage）：各 PVE Storage 的速度等級、使用者優先度與啟用狀態，
 * 放置演算法據此挑選磁碟。2026-09 從「系統設定」的分頁拆成獨立頁面。
 */

/* ── Storage ───────────────────────────────────────── */
function StorageList() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const [storages, setStorages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [savingId, setSavingId] = useState(null);

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
    } catch (err) {
      toast.error(err?.message ?? t("SettingsPage.toastUpdateStorageFailed"));
    } finally {
      setSavingId(null);
    }
  }

  if (loading) return <LoadingState text={t("SettingsPage.loadingStorage")} />;
  if (storages.length === 0) {
    return (
      <EmptyState icon="storage" title={t("SettingsPage.emptyNoStorageConfig")} />
    );
  }

  return (
    <div className={styles.list}>
      {storages.map((storage) => (
        <div key={storage.id} className={styles.storageRow}>
          <div className={styles.rowMain}>
            <span className={styles.rowName}>
              {storage.storage}
              {multiConnection && storage.connection_name && (
                <span className={`${styles.badge} ${styles.badge_muted}`}>{storage.connection_name}</span>
              )}
              {storage.is_shared ? (
                <span
                  className={`${styles.badge} ${styles.badge_info}`}
                  title={(storage.node_names ?? []).join("、")}
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
            <input
              type="number"
              className={styles.inlineInput}
              value={storage.user_priority}
              disabled={savingId === storage.id}
              onChange={(e) => save(storage, { user_priority: Number(e.target.value) || 0 })}
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
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function StoragePage() {
  const { t } = useTranslation("system");
  return (
    <div className={styles.page}>
      <PageHeader title={t("SettingsPage.storageTitle")} subtitle={t("SettingsPage.storageSubtitle")} />
      <div className={styles.content}>
        <p className={styles.listHint}>{t("SettingsPage.storageHint")}</p>
        <StorageList />
      </div>
    </div>
  );
}
