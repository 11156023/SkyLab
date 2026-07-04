import { useEffect, useState } from "react";
import styles from "./SettingsPage.module.scss";
import { GovernanceService } from "../../../services/governance";
import { useToast } from "../../../hooks/useToast";

/**
 * 治理設定分頁：閾值告警 / TTL 回收 / 閒置偵測 / 自動判斷 /
 * 反挖礦 / 快照治理 / 克隆併發。單一儲存鍵送出全部欄位。
 */

const SECTIONS = [
  {
    title: "資源告警",
    desc: "超過閾值時建立告警事件並通知管理員（站內 + Email）。",
    toggles: [
      { key: "alerts_enabled", label: "啟用告警", hint: "定期檢查叢集/節點/VM 資源使用率" },
      { key: "alert_email_enabled", label: "Email 通知", hint: "告警建立時寄送 Email 給管理員" },
    ],
    fields: [
      { key: "alert_cpu_threshold", label: "CPU 閾值（%）", min: 50, max: 100, step: 0.5 },
      { key: "alert_memory_threshold", label: "記憶體閾值（%）", min: 50, max: 100, step: 0.5 },
      { key: "alert_disk_threshold", label: "磁碟閾值（%）", min: 50, max: 100, step: 0.5 },
      { key: "alert_cooldown_minutes", label: "冷卻期（分鐘）", min: 1, max: 1440, hint: "同一目標同一指標在冷卻期內不重發告警" },
      { key: "alert_check_interval_seconds", label: "檢查間隔（秒）", min: 15, max: 3600 },
    ],
  },
  {
    title: "TTL 生命週期",
    desc: "資源到期後漸進回收：到期前通知 → 到期關機 → 寬限期滿進入刪除佇列。",
    toggles: [
      { key: "ttl_enabled", label: "啟用 TTL 回收", hint: "依資源到期日自動通知、關機與排入刪除" },
    ],
    fields: [
      { key: "expiry_warn_days", label: "到期前通知（天）", min: 1, max: 30 },
      { key: "expiry_grace_delete_days", label: "刪除寬限期（天）", min: 0, max: 90, hint: "到期關機後保留幾天才排入刪除佇列" },
    ],
  },
  {
    title: "閒置偵測",
    desc: "CPU 長期低於閾值的資源先通知擁有者，寬限期滿自動關機（不刪除）。",
    toggles: [
      { key: "idle_detection_enabled", label: "啟用閒置偵測", hint: "偵測長期低 CPU 的運行中資源" },
    ],
    fields: [
      { key: "idle_cpu_threshold_percent", label: "閒置 CPU 閾值（%）", min: 0.1, max: 20, step: 0.1 },
      { key: "idle_window_hours", label: "觀察視窗（小時）", min: 1, max: 720 },
      { key: "idle_grace_hours", label: "關機寬限期（小時）", min: 1, max: 720, hint: "通知後仍閒置達此時數才自動關機" },
      { key: "idle_scan_batch_size", label: "每輪掃描台數", min: 1, max: 200 },
    ],
  },
  {
    title: "VM / LXC 自動判斷",
    desc: "申請資源時提供「自動判斷」模式，由規則引擎依工作負載建議 VM 或 LXC。",
    toggles: [
      { key: "workload_advisor_enabled", label: "啟用自動判斷", hint: "停用後申請表單僅能手動選擇資源類型" },
    ],
    fields: [],
  },
  {
    title: "反挖礦偵測",
    desc: "CPU 長期滿載的資源自動存證快照、暫停並通知；帳號停權由管理員在「資源監控 → 挖礦事件」人工確認。",
    toggles: [
      { key: "mining_detection_enabled", label: "啟用挖礦偵測", hint: "定期掃描運行中資源的 CPU 特徵" },
      { key: "mining_auto_suspend", label: "自動存證並暫停", hint: "關閉後僅建立事件與通知，暫停由管理員手動執行" },
    ],
    fields: [
      { key: "mining_cpu_threshold_percent", label: "CPU 閾值（%）", min: 50, max: 100, step: 0.5 },
      { key: "mining_window_hours", label: "觀察視窗（小時）", min: 1, max: 72, hint: "平均 CPU 持續高於閾值達此時數才判定" },
      { key: "mining_scan_batch_size", label: "每輪掃描台數", min: 1, max: 200 },
    ],
  },
  {
    title: "快照治理",
    desc: "定期清理過期的學生快照（skylab-init 初始快照永不清理），並限制每台 VM 的學生快照數量。",
    toggles: [
      { key: "snapshot_cleanup_enabled", label: "啟用快照自動清理", hint: "超過保留天數的非保護快照將被排程刪除" },
    ],
    fields: [
      { key: "snapshot_retention_days", label: "保留天數", min: 1, max: 90 },
      { key: "student_snapshot_max_count", label: "學生快照上限", min: 1, max: 10, hint: "不含 skylab-init；達上限需先刪舊快照" },
    ],
  },
  {
    title: "克隆併發",
    desc: "同時執行的 VM/LXC 克隆數上限（克隆為 PVE 磁碟 I/O 重活，過高會拖垮儲存）。變更於下一個排程週期生效。",
    toggles: [],
    fields: [{ key: "provision_max_concurrency", label: "併發上限", min: 1, max: 16 }],
  },
];

/** 所有可編輯欄位（送出時用） */
const ALL_KEYS = SECTIONS.flatMap((s) => [
  ...s.toggles.map((t) => t.key),
  ...s.fields.map((f) => f.key),
]);

export default function GovernanceTab() {
  const toast = useToast();
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    GovernanceService.getConfig()
      .then((config) => {
        if (cancelled) return;
        const next = {};
        for (const key of ALL_KEYS) next[key] = config[key];
        setForm(next);
      })
      .catch((err) => toast.error(err?.message ?? "載入治理設定失敗"));
    return () => {
      cancelled = true;
    };
  }, [toast]);

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await GovernanceService.updateConfig(form);
      const next = {};
      for (const key of ALL_KEYS) next[key] = updated[key];
      setForm(next);
      toast.success("治理設定已儲存");
    } catch (err) {
      toast.error(`儲存失敗：${err?.message ?? "未知錯誤"}`);
    } finally {
      setSaving(false);
    }
  }

  if (!form) return <div className={styles.loading}>載入治理設定...</div>;

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

      <div className={styles.cardActions}>
        <button type="submit" className={styles.btnPrimary} disabled={saving}>
          {saving ? "儲存中..." : "儲存治理設定"}
        </button>
      </div>
    </form>
  );
}
