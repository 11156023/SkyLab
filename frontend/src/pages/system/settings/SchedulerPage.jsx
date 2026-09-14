import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./settings.module.scss";
import LoadingState from "../../../components/LoadingState/LoadingState";
import { useToast } from "../../../hooks/useToast";
import { ProxmoxConfigService } from "../../../services/proxmoxConfig";
import { useUnsavedChangesGuard } from "../../../contexts/UnsavedChangesContext";
import PageHeader from "../../../components/PageHeader/PageHeader";
import RelatedSettingsNav from "./RelatedSettingsNav";

/**
 * 資源排程（系統管理 → 資源排程）：跨叢集共用的放置／超配／排程開機參數，
 * 存在 proxmox_config singleton。2026-09 從「系統設定」的分頁拆成獨立頁面。
 */

/**
 * 這一頁管的欄位。PUT /proxmox-config 已改為**部分更新**（payload 沒帶
 * 的欄位後端維持現值），所以只送排程相關欄位即可——連線與叢集資源欄位
 * （host / user / storage / pool / gateway…）由「PVE 連線」的表單管理，
 * 不必再原樣回送。
 */
const UPDATE_KEYS = [
  "cpu_overcommit_ratio", "disk_overcommit_ratio",
  "placement_reassignment_cost", "placement_peak_cpu_margin",
  "placement_peak_memory_margin", "placement_loadavg_warn_per_core",
  "placement_loadavg_max_per_core", "placement_loadavg_penalty_weight",
  "placement_disk_contention_warn_share", "placement_disk_contention_high_share",
  "placement_disk_penalty_weight",
  "placement_cpu_peak_warn_share", "placement_cpu_peak_high_share",
  "placement_memory_peak_warn_share", "placement_memory_peak_high_share",
  "placement_resource_weight_cpu", "placement_resource_weight_memory",
  "placement_resource_weight_disk",
  "scheduled_boot_batch_size", "scheduled_boot_batch_interval_seconds",
  "scheduled_boot_lead_time_minutes", "window_grace_period_minutes",
  "practice_session_hours", "practice_warning_minutes",
  "expiry_warning_hours",
];

function buildFormFromConfig(config) {
  const form = {};
  for (const key of UPDATE_KEYS) form[key] = config?.[key] ?? "";
  return form;
}

function buildPayload(form) {
  const payload = {};
  for (const key of UPDATE_KEYS) payload[key] = form[key];
  return payload;
}

/* ── 資源排程 ──────────────────────────────────────── */
/* 依性質與生效時機分卡：放置權重改了立即影響下一次落點，排程開機下個
   週期生效，練習時段與到期提醒是使用者體驗設定（與放置演算法無關） */
function useSchedulerGroups(t) {
  return useMemo(() => [
    {
      title: t("SettingsPage.placementOvercommitTitle"),
      desc: t("SettingsPage.placementOvercommitDesc"),
      fields: [
        { key: "cpu_overcommit_ratio", label: t("SettingsPage.cpuOvercommitRatio"), step: 0.1 },
        { key: "disk_overcommit_ratio", label: t("SettingsPage.diskOvercommitRatio"), step: 0.1 },
        { key: "placement_reassignment_cost", label: t("SettingsPage.placementReassignmentCost"), step: 0.01 },
      ],
    },
    {
      title: t("SettingsPage.resourceThresholdsTitle"),
      desc: t("SettingsPage.resourceThresholdsDesc"),
      fields: [
        { key: "placement_peak_cpu_margin", label: t("SettingsPage.placementPeakCpuMargin"), step: 0.01 },
        { key: "placement_peak_memory_margin", label: t("SettingsPage.placementPeakMemoryMargin"), step: 0.01 },
        { key: "placement_loadavg_warn_per_core", label: t("SettingsPage.placementLoadavgWarnPerCore"), step: 0.1 },
        { key: "placement_loadavg_max_per_core", label: t("SettingsPage.placementLoadavgMaxPerCore"), step: 0.1 },
        { key: "placement_loadavg_penalty_weight", label: t("SettingsPage.placementLoadavgPenaltyWeight"), step: 0.01 },
        { key: "placement_disk_contention_warn_share", label: t("SettingsPage.placementDiskContentionWarnShare"), step: 0.01 },
        { key: "placement_disk_contention_high_share", label: t("SettingsPage.placementDiskContentionHighShare"), step: 0.01 },
        { key: "placement_disk_penalty_weight", label: t("SettingsPage.placementDiskPenaltyWeight"), step: 0.01 },
        { key: "placement_cpu_peak_warn_share", label: t("SettingsPage.placementCpuPeakWarnShare"), step: 0.01 },
        { key: "placement_cpu_peak_high_share", label: t("SettingsPage.placementCpuPeakHighShare"), step: 0.01 },
        { key: "placement_memory_peak_warn_share", label: t("SettingsPage.placementMemoryPeakWarnShare"), step: 0.01 },
        { key: "placement_memory_peak_high_share", label: t("SettingsPage.placementMemoryPeakHighShare"), step: 0.01 },
        { key: "placement_resource_weight_cpu", label: t("SettingsPage.placementResourceWeightCpu"), step: 0.01 },
        { key: "placement_resource_weight_memory", label: t("SettingsPage.placementResourceWeightMemory"), step: 0.01 },
        { key: "placement_resource_weight_disk", label: t("SettingsPage.placementResourceWeightDisk"), step: 0.01 },
      ],
    },
    {
      title: t("SettingsPage.scheduledBootTitle"),
      desc: t("SettingsPage.scheduledBootDesc"),
      fields: [
        { key: "scheduled_boot_batch_size", label: t("SettingsPage.scheduledBootBatchSize") },
        { key: "scheduled_boot_batch_interval_seconds", label: t("SettingsPage.scheduledBootBatchIntervalSeconds") },
        { key: "scheduled_boot_lead_time_minutes", label: t("SettingsPage.scheduledBootLeadTimeMinutes") },
        { key: "window_grace_period_minutes", label: t("SettingsPage.windowGracePeriodMinutes") },
      ],
    },
    {
      title: t("SettingsPage.practiceExpiryTitle"),
      desc: t("SettingsPage.practiceExpiryDesc"),
      fields: [
        { key: "practice_session_hours", label: t("SettingsPage.practiceSessionHours") },
        { key: "practice_warning_minutes", label: t("SettingsPage.practiceWarningMinutes") },
        { key: "expiry_warning_hours", label: t("SettingsPage.expiryWarningHours"), hint: t("SettingsPage.expiryWarningHoursHint") },
      ],
    },
  ], [t]);
}

function SchedulerForm({ form, setField, onSave, saving, dirty, onRestore }) {
  const { t } = useTranslation("system");
  const SCHEDULER_GROUPS = useSchedulerGroups(t);
  return (
    <form className={styles.panelStack} onSubmit={onSave}>
      {SCHEDULER_GROUPS.map((group) => (
        <div key={group.title} className={styles.card}>
          <h2 className={styles.cardTitle}>{group.title}</h2>
          <p className={styles.cardDesc}>{group.desc}</p>
          <div className={styles.formGrid}>
            {group.fields.map((f) => (
              <label key={f.key} className={styles.field}>
                <span>{f.label}</span>
                <input
                  type="number"
                  step={f.step ?? 1}
                  value={form[f.key]}
                  onChange={(e) => setField(f.key, Number(e.target.value))}
                />
                {f.hint && <em className={styles.fieldHint}>{f.hint}</em>}
              </label>
            ))}
          </div>
        </div>
      ))}

      <div className={styles.saveBar}>
        {dirty && (
          <button type="button" className={styles.btnSecondary} disabled={saving} onClick={onRestore}>
            {t("SettingsPage.restore")}
          </button>
        )}
        <button type="submit" className={styles.btnPrimary} disabled={!dirty || saving}>
          {saving ? t("SettingsPage.saving") : t("SettingsPage.saveSchedulerSettings")}
        </button>
      </div>
    </form>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function SchedulerPage() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const [form, setForm] = useState(buildFormFromConfig(null));
  const [baseline, setBaseline] = useState(form);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const dirty = Object.keys(form).some((key) => form[key] !== baseline[key]);
  useUnsavedChangesGuard(dirty);

  useEffect(() => {
    ProxmoxConfigService.getConfig()
      .then((cfg) => {
        const next = buildFormFromConfig(cfg);
        setForm(next);
        setBaseline(next);
      })
      .catch((err) => toast.error(err?.message ?? t("SettingsPage.toastLoadSchedulerFailed")))
      .finally(() => setLoading(false));
  }, [toast, t]);

  const setField = useCallback((name, value) => {
    setForm((prev) => ({ ...prev, [name]: value }));
  }, []);

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await ProxmoxConfigService.updateConfig(buildPayload(form));
      const next = buildFormFromConfig(updated);
      setForm(next);
      setBaseline(next);
      toast.success(t("SettingsPage.toastSettingsSaved"));
    } catch (err) {
      toast.error(err?.message ?? t("SettingsPage.toastSaveSettingsFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("SettingsPage.schedulerTitle")} subtitle={t("SettingsPage.schedulerSubtitle")} />
      <RelatedSettingsNav current="scheduler" />
      <div className={styles.content}>
        {loading ? (
          <LoadingState fullPage text={t("SettingsPage.loadingSettings")} />
        ) : (
          <SchedulerForm
            form={form}
            setField={setField}
            onSave={handleSave}
            saving={saving}
            dirty={dirty}
            onRestore={() => setForm(baseline)}
          />
        )}
      </div>
    </div>
  );
}
