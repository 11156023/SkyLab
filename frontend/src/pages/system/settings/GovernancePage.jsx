import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./settings.module.scss";
import LoadingState from "../../../components/LoadingState/LoadingState";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { GovernanceService } from "../../../services/governance";
import { useToast } from "../../../hooks/useToast";
import { useUnsavedChangesGuard } from "../../../contexts/UnsavedChangesContext";
import RelatedSettingsNav from "./RelatedSettingsNav";

/**
 * 治理設定（系統管理 → 治理）：閾值警告 / TTL 回收 / 閒置偵測 / 自動判斷 /
 * 反挖礦 / 快照治理 / 克隆併發 / 課程學習環境。單一儲存鍵送出全部欄位。
 * 2026-09 從「系統設定」的分頁拆成獨立頁面。
 */

function useSections(t) {
  return useMemo(() => [
    {
      title: t("GovernanceTab.alertsTitle"),
      desc: t("GovernanceTab.alertsDesc"),
      toggles: [
        { key: "alerts_enabled", label: t("GovernanceTab.alertsEnabled"), hint: t("GovernanceTab.alertsEnabledHint") },
        { key: "alert_email_enabled", label: t("GovernanceTab.alertEmailEnabled"), hint: t("GovernanceTab.alertEmailEnabledHint") },
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
      toggles: [
        { key: "ttl_enabled", label: t("GovernanceTab.ttlEnabled"), hint: t("GovernanceTab.ttlEnabledHint") },
      ],
      fields: [
        { key: "expiry_warn_days", label: t("GovernanceTab.expiryWarnDays"), min: 1, max: 30, hint: t("GovernanceTab.expiryWarnDaysHint") },
        { key: "expiry_grace_delete_days", label: t("GovernanceTab.expiryGraceDeleteDays"), min: 0, max: 90, hint: t("GovernanceTab.expiryGraceDeleteDaysHint") },
      ],
    },
    {
      title: t("GovernanceTab.idleTitle"),
      desc: t("GovernanceTab.idleDesc"),
      toggles: [
        { key: "idle_detection_enabled", label: t("GovernanceTab.idleDetectionEnabled"), hint: t("GovernanceTab.idleDetectionEnabledHint") },
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
      desc: t("GovernanceTab.workloadAdvisorDesc"),
      toggles: [
        { key: "workload_advisor_enabled", label: t("GovernanceTab.workloadAdvisorEnabled"), hint: t("GovernanceTab.workloadAdvisorEnabledHint") },
      ],
      fields: [],
    },
    {
      title: t("GovernanceTab.miningTitle"),
      desc: t("GovernanceTab.miningDesc"),
      toggles: [
        { key: "mining_detection_enabled", label: t("GovernanceTab.miningDetectionEnabled"), hint: t("GovernanceTab.miningDetectionEnabledHint") },
        { key: "mining_auto_suspend", label: t("GovernanceTab.miningAutoSuspend"), hint: t("GovernanceTab.miningAutoSuspendHint") },
      ],
      fields: [
        { key: "mining_cpu_threshold_percent", label: t("GovernanceTab.miningCpuThresholdPercent"), min: 50, max: 100, step: 0.5 },
        { key: "mining_window_hours", label: t("GovernanceTab.miningWindowHours"), min: 1, max: 72, hint: t("GovernanceTab.miningWindowHoursHint") },
        { key: "mining_scan_batch_size", label: t("GovernanceTab.miningScanBatchSize"), min: 1, max: 200 },
      ],
    },
    {
      title: t("GovernanceTab.snapshotTitle"),
      desc: t("GovernanceTab.snapshotDesc"),
      toggles: [
        { key: "snapshot_cleanup_enabled", label: t("GovernanceTab.snapshotCleanupEnabled"), hint: t("GovernanceTab.snapshotCleanupEnabledHint") },
      ],
      fields: [
        { key: "snapshot_retention_days", label: t("GovernanceTab.snapshotRetentionDays"), min: 1, max: 90 },
        { key: "student_snapshot_max_count", label: t("GovernanceTab.studentSnapshotMaxCount"), min: 1, max: 10, hint: t("GovernanceTab.studentSnapshotMaxCountHint") },
      ],
    },
    {
      title: t("GovernanceTab.cloneConcurrencyTitle"),
      desc: t("GovernanceTab.cloneConcurrencyDesc"),
      toggles: [],
      fields: [{ key: "provision_max_concurrency", label: t("GovernanceTab.provisionMaxConcurrency"), min: 1, max: 16 }],
    },
    {
      title: t("GovernanceTab.courseLabTitle"),
      desc: t("GovernanceTab.courseLabDesc"),
      toggles: [],
      fields: [
        { key: "course_ttl_hours", label: t("GovernanceTab.courseTtlHours"), min: 1, max: 24, hint: t("GovernanceTab.courseTtlHoursHint") },
        { key: "course_max_active_per_user", label: t("GovernanceTab.courseMaxActivePerUser"), min: 1, max: 5, hint: t("GovernanceTab.courseMaxActivePerUserHint") },
      ],
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

  useEffect(() => {
    let cancelled = false;
    GovernanceService.getConfig()
      .then((config) => {
        if (cancelled) return;
        const next = {};
        for (const key of ALL_KEYS) next[key] = config[key];
        setForm(next);
        setBaseline(next);
      })
      .catch((err) => toast.error(err?.message ?? t("GovernanceTab.toastLoadFailed")));
    return () => {
      cancelled = true;
    };
  }, [toast, t, ALL_KEYS]);

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await GovernanceService.updateConfig(form);
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

  if (!form) return <LoadingState text={t("GovernanceTab.loading")} />;

  return (
    <form className={styles.panelStack} onSubmit={handleSave}>
      {SECTIONS.map((section) => (
        <div key={section.title} className={styles.card}>
          <h2 className={styles.cardTitle}>{section.title}</h2>
          <p className={styles.cardDesc}>{section.desc}</p>

          {section.toggles.map((toggle) => (
            <label key={toggle.key} className={styles.checkRow}>
              <input
                type="checkbox"
                checked={Boolean(form[toggle.key])}
                onChange={(e) => setField(toggle.key, e.target.checked)}
              />
              <span>{toggle.label}</span>
              <em className={styles.fieldHint}>{toggle.hint}</em>
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

      <div className={styles.saveBar}>
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
      <PageHeader title={t("SettingsPage.governanceTitle")} subtitle={t("SettingsPage.governanceSubtitle")} />
      <RelatedSettingsNav current="governance" />
      <div className={styles.content}>
        <GovernanceForm />
      </div>
    </div>
  );
}
