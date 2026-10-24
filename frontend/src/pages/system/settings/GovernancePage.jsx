import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./settings.module.scss";
import ErrorState from "../../../components/ErrorState/ErrorState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import PageHeader from "../../../components/PageHeader/PageHeader";
import QuotasSection from "./QuotasSection";
import { GovernanceService } from "../../../services/governance";
import { useGovernanceCacheUpdater, useGovernanceConfigQuery } from "../../../queries/governance";
import { useToast } from "../../../hooks/useToast";
import { useLoadError, useQueryErrorEffect } from "../../../hooks/useQueryLoadState";
import { useUnsavedChangesGuard } from "../../../contexts/UnsavedChangesContext";

/**
 * 資源政策（系統管理 → 資源政策）：上半是治理設定——閾值警告 / TTL 回收 / 閒置偵測 /
 * 自動判斷 / 反挖礦 / 快照治理 / 克隆併發，單一儲存鍵送出全部欄位；
 * 下半是配額——全域預設上限與個別使用者覆寫（QuotasSection），各自儲存。
 * 2026-09 從「系統設定」的分頁拆成獨立頁面；2026-10 把原本獨立的「配額」頁併進來（/quotas 也導向這裡）。
 */

function useSections(t) {
  return useMemo(() => [
    {
      title: t("GovernanceTab.alertsTitle"),
      /* 頁面導覽的錨點（UserGuide pageGuides/systemSettings.js） */
      guideProps: { "data-guide": "governance-alerts" },
      toggles: [
        { key: "alerts_enabled", label: t("GovernanceTab.alertsEnabled") },
        { key: "alert_email_enabled", label: t("GovernanceTab.alertEmailEnabled") },
      ],
      fields: [
        { key: "alert_cpu_threshold", label: t("GovernanceTab.alertCpuThreshold"), min: 50, max: 100, step: 0.5 },
        { key: "alert_memory_threshold", label: t("GovernanceTab.alertMemoryThreshold"), min: 50, max: 100, step: 0.5 },
        { key: "alert_disk_threshold", label: t("GovernanceTab.alertDiskThreshold"), min: 50, max: 100, step: 0.5 },
        { key: "alert_cooldown_minutes", label: t("GovernanceTab.alertCooldownMinutes"), min: 1, max: 1440, hint: t("GovernanceTab.alertCooldownMinutesHint") },
        { key: "alert_check_interval_seconds", label: t("GovernanceTab.alertCheckIntervalSeconds"), min: 15, max: 3600 },
      ],
    },
    {
      title: t("GovernanceTab.ttlTitle"),
      desc: t("GovernanceTab.ttlDesc"),
      guideProps: { "data-guide": "governance-ttl" },
      toggles: [
        { key: "ttl_enabled", label: t("GovernanceTab.ttlEnabled") },
      ],
      fields: [
        { key: "expiry_warn_days", label: t("GovernanceTab.expiryWarnDays"), min: 1, max: 30, hint: t("GovernanceTab.expiryWarnDaysHint") },
        { key: "expiry_grace_delete_days", label: t("GovernanceTab.expiryGraceDeleteDays"), min: 0, max: 90, hint: t("GovernanceTab.expiryGraceDeleteDaysHint") },
      ],
    },
    {
      title: t("GovernanceTab.idleTitle"),
      desc: t("GovernanceTab.idleDesc"),
      guideProps: { "data-guide": "governance-idle" },
      toggles: [
        { key: "idle_detection_enabled", label: t("GovernanceTab.idleDetectionEnabled") },
      ],
      fields: [
        { key: "idle_cpu_threshold_percent", label: t("GovernanceTab.idleCpuThresholdPercent"), min: 0.1, max: 20, step: 0.1 },
        { key: "idle_window_hours", label: t("GovernanceTab.idleWindowHours"), min: 1, max: 720, hint: t("GovernanceTab.idleWindowHoursHint") },
        { key: "idle_notify_after_hours", label: t("GovernanceTab.idleNotifyAfterHours"), min: 1, max: 720, hint: t("GovernanceTab.idleNotifyAfterHoursHint") },
        { key: "idle_grace_hours", label: t("GovernanceTab.idleGraceHours"), min: 1, max: 720, hint: t("GovernanceTab.idleGraceHoursHint") },
        { key: "idle_scan_batch_size", label: t("GovernanceTab.idleScanBatchSize"), min: 1, max: 200 },
      ],
    },
    {
      title: t("GovernanceTab.workloadAdvisorTitle"),
      toggles: [
        { key: "workload_advisor_enabled", label: t("GovernanceTab.workloadAdvisorEnabled"), hint: t("GovernanceTab.workloadAdvisorEnabledHint") },
      ],
      fields: [],
    },
    {
      title: t("GovernanceTab.miningTitle"),
      desc: t("GovernanceTab.miningDesc"),
      guideProps: { "data-guide": "governance-mining" },
      toggles: [
        { key: "mining_detection_enabled", label: t("GovernanceTab.miningDetectionEnabled") },
        { key: "mining_auto_suspend", label: t("GovernanceTab.miningAutoSuspend"), hint: t("GovernanceTab.miningAutoSuspendHint") },
      ],
      fields: [
        { key: "mining_cpu_threshold_percent", label: t("GovernanceTab.miningCpuThresholdPercent"), min: 50, max: 100, step: 0.5 },
        { key: "mining_window_hours", label: t("GovernanceTab.miningWindowHours"), min: 1, max: 72 },
        { key: "mining_scan_batch_size", label: t("GovernanceTab.miningScanBatchSize"), min: 1, max: 200 },
      ],
    },
    {
      title: t("GovernanceTab.snapshotTitle"),
      desc: t("GovernanceTab.snapshotDesc"),
      toggles: [
        { key: "snapshot_cleanup_enabled", label: t("GovernanceTab.snapshotCleanupEnabled") },
      ],
      fields: [
        { key: "snapshot_retention_days", label: t("GovernanceTab.snapshotRetentionDays"), min: 1, max: 90 },
        { key: "student_snapshot_max_count", label: t("GovernanceTab.studentSnapshotMaxCount"), min: 1, max: 10 },
        { key: "student_backup_max_count", label: t("GovernanceTab.studentBackupMaxCount"), min: 1, max: 10 },
      ],
    },
    {
      title: t("GovernanceTab.cloneConcurrencyTitle"),
      desc: t("GovernanceTab.cloneConcurrencyDesc"),
      toggles: [],
      fields: [{ key: "provision_max_concurrency", label: t("GovernanceTab.provisionMaxConcurrency"), min: 1, max: 16 }],
    },
  ], [t]);
}

function GovernanceForm() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const SECTIONS = useSections(t);
  /** 所有可編輯欄位（送出時用） */
  const ALL_KEYS = useMemo(() => SECTIONS.flatMap((s) => [
    ...s.toggles.map((tg) => tg.key),
    ...s.fields.map((f) => f.key),
  ]), [SECTIONS]);
  const [form, setForm] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [saving, setSaving] = useState(false);
  const dirty = Boolean(form && baseline && ALL_KEYS.some((key) => form[key] !== baseline[key]));
  useUnsavedChangesGuard(dirty);

  /* 設定走 TanStack Query 讀（不輪詢、不因切回分頁重抓）；表單只在第一次拿到資料時初始化，
     之後的背景重抓不會蓋掉正在編輯的欄位。載入失敗要看得見：改渲染重試區塊，不要停在轉圈畫面 */
  const configQuery = useGovernanceConfigQuery();
  const governanceCache = useGovernanceCacheUpdater();
  /* 進頁時快取若已過期會重抓：那次重抓回來前不初始化表單（維持載入中）。不然表單會先填快取裡的舊設定，
     之後的新資料又被「只初始化一次」略過，存檔時把別的管理員剛改的設定整份蓋回去。
     重抓失敗時仍用手上的資料；5 秒內回到頁面不會重抓，快取就夠新 */
  const configReady = configQuery.data !== undefined && !(configQuery.isFetching && !configQuery.isFetchedAfterMount);
  useEffect(() => {
    if (baseline !== null || !configReady) return;
    const next = {};
    for (const key of ALL_KEYS) next[key] = configQuery.data[key];
    setForm(next);
    setBaseline(next);
  }, [baseline, configReady, configQuery.data, ALL_KEYS]);

  /* 每次載入失敗提示一次：重抓時 error 會先清空再變回來，不能拿 error 物件當觸發條件 */
  useQueryErrorEffect(configQuery, (err) => {
    toast.error(err?.message ?? t("Error.generic", { ns: "common" }));
  });

  /* 重試區塊在重抓期間維持、不閃回轉圈，按重試才回到載入中（見 useLoadError） */
  const { error: loadError, retry: retryLoad } = useLoadError(configQuery);

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await GovernanceService.updateConfig(form);
      governanceCache.setConfig(updated);
      const next = {};
      for (const key of ALL_KEYS) next[key] = updated[key];
      setForm(next);
      setBaseline(next);
      toast.success(t("GovernanceTab.toastSaved"));
    } catch (err) {
      toast.error(t("GovernanceTab.toastSaveFailed", { message: err?.message ?? t("GovernanceTab.unknownError") }));
    } finally {
      setSaving(false);
    }
  }

  if (!form && loadError) {
    return <ErrorState onRetry={() => retryLoad()} />;
  }
  if (!form) return <LoadingState text={t("GovernanceTab.loading")} />;

  return (
    <form className={styles.panelStack} onSubmit={handleSave}>
      {SECTIONS.map((section) => (
        <div key={section.title} className={styles.card} {...section.guideProps}>
          <h3 className={styles.cardTitle}>{section.title}</h3>
          {section.desc && <p className={styles.cardDesc}>{section.desc}</p>}

          {section.toggles.map((toggle) => (
            <label key={toggle.key} className={styles.checkRow}>
              <input
                type="checkbox"
                checked={Boolean(form[toggle.key])}
                onChange={(e) => setField(toggle.key, e.target.checked)}
              />
              <span>{toggle.label}</span>
              {toggle.hint && <em className={styles.fieldHint}>{toggle.hint}</em>}
            </label>
          ))}

          {section.fields.length > 0 && (
            <div className={styles.formGrid}>
              {section.fields.map((field) => (
                <label key={field.key} className={styles.field}>
                  <span>{field.label}</span>
                  <input
                    type="number"
                    min={field.min}
                    max={field.max}
                    step={field.step ?? 1}
                    value={form[field.key]}
                    onChange={(e) => setField(field.key, e.target.valueAsNumber)}
                    required
                  />
                  {field.hint && <em className={styles.fieldHint}>{field.hint}</em>}
                </label>
              ))}
            </div>
          )}
        </div>
      ))}

      <div className={styles.saveBar} data-guide="governance-save">
        {dirty && (
          <button type="button" className={styles.btnSecondary} disabled={saving} onClick={() => setForm(baseline)}>
            {t("SettingsPage.restore")}
          </button>
        )}
        <button type="submit" className={styles.btnPrimary} disabled={!dirty || saving}>
          {saving ? t("GovernanceTab.saving") : t("GovernanceTab.saveConfig")}
        </button>
      </div>
    </form>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function GovernancePage() {
  const { t } = useTranslation("system");
  return (
    <div className={styles.page}>
      <PageHeader title={t("SettingsPage.governanceTitle")} />
      <div className={styles.content}>
        <section className={styles.pageSection} aria-labelledby="governance-section-title">
          <h2 id="governance-section-title" className={styles.sectionHeading}>{t("SettingsPage.governanceSectionTitle")}</h2>
          <GovernanceForm />
        </section>
        <section className={styles.pageSection} aria-labelledby="quotas-section-title">
          <h2 id="quotas-section-title" className={styles.sectionHeading}>{t("SettingsPage.quotasTitle")}</h2>
          <QuotasSection />
        </section>
      </div>
    </div>
  );
}
