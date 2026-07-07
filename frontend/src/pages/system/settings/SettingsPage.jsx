import { useCallback, useEffect, useState } from "react";
import styles from "./SettingsPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import { ProxmoxConfigService } from "../../../services/proxmoxConfig";
import GovernanceTab from "./GovernanceTab";
import LdapTab from "./LdapTab";

const TABS = [
  { key: "overview",  label: "叢集概覽", icon: "layers"         },
  { key: "pve",       label: "PVE 連線",  icon: "device_hub"    },
  { key: "scheduler", label: "資源排程",  icon: "settings_input_component" },
  { key: "governance", label: "治理",     icon: "policy"        },
  { key: "ldap",      label: "LDAP",      icon: "badge"         },
  { key: "nodes",     label: "節點管理",  icon: "lock"          },
  { key: "storage",   label: "Storage",   icon: "storage"       },
];

/** PUT /proxmox-config 需要的完整欄位（password / ca_cert 另外處理） */
const UPDATE_KEYS = [
  "host", "user", "verify_ssl", "iso_storage", "data_storage",
  "api_timeout", "task_check_interval", "pool_name", "gateway_ip",
  "local_subnet", "default_node", "placement_strategy",
  "cpu_overcommit_ratio", "disk_overcommit_ratio",
  "migration_enabled", "migration_max_per_rebalance",
  "migration_min_interval_minutes", "migration_retry_limit",
  "rebalance_migration_cost", "rebalance_peak_cpu_margin",
  "rebalance_peak_memory_margin", "rebalance_loadavg_warn_per_core",
  "rebalance_loadavg_max_per_core", "rebalance_loadavg_penalty_weight",
  "rebalance_cpu_peak_warn_share", "rebalance_cpu_peak_high_share",
  "rebalance_memory_peak_warn_share", "rebalance_memory_peak_high_share",
  "rebalance_resource_weight_cpu", "rebalance_resource_weight_memory",
  "rebalance_resource_weight_disk", "migration_lxc_live_enabled",
  "scheduled_boot_batch_size", "scheduled_boot_batch_interval_seconds",
  "scheduled_boot_lead_time_minutes", "window_grace_period_minutes",
  "practice_session_hours", "practice_warning_minutes",
  "expiry_warning_hours",
];

function buildFormFromConfig(config) {
  const form = {};
  for (const key of UPDATE_KEYS) form[key] = config?.[key] ?? "";
  form.password = "";
  form.ca_cert = "";
  return form;
}

function buildPayload(form) {
  const payload = {};
  for (const key of UPDATE_KEYS) {
    const value = form[key];
    payload[key] = value === "" ? null : value;
  }
  if (form.password) payload.password = form.password;
  if (form.ca_cert?.trim()) payload.ca_cert = form.ca_cert.trim();
  return payload;
}

function pct(used, total) {
  if (!total) return 0;
  return Math.min(Math.round((used / total) * 100), 100);
}

function UsageBar({ label, used, total, unit }) {
  const percent = pct(used, total);
  return (
    <div className={styles.usageBarRow}>
      <span className={styles.usageBarLabel}>{label}</span>
      <div className={styles.usageBarTrack}>
        <div className={styles.usageBarFill} style={{ width: `${percent}%` }} />
      </div>
      <span className={styles.usageBarValue}>
        {Math.round(used)} / {Math.round(total)} {unit}（{percent}%）
      </span>
    </div>
  );
}

/* ── 叢集概覽 ──────────────────────────────────────── */
function OverviewTab() {
  const toast = useToast();
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    ProxmoxConfigService.getClusterStats()
      .then(setStats)
      .catch((err) => toast.error(err?.message ?? "載入叢集統計失敗"))
      .finally(() => setLoading(false));
  }, [toast]);

  if (loading) return <div className={styles.loading}>載入叢集統計...</div>;
  if (!stats) {
    return (
      <div className={styles.empty}>
        <div className={styles.emptyIcon}><MIcon name="layers" size={40} /></div>
        <h2 className={styles.emptyTitle}>尚無叢集資料</h2>
        <p className={styles.emptyDesc}>請先完成 PVE 連線設定並同步</p>
      </div>
    );
  }

  return (
    <div className={styles.panelStack}>
      <div className={styles.summaryGrid}>
        <div className={styles.summaryItem}>
          <span>節點</span>
          <strong>
            {stats.online_count} 在線
            {stats.offline_count > 0 ? ` / ${stats.offline_count} 離線` : ""}
          </strong>
        </div>
        <div className={styles.summaryItem}>
          <span>VM / LXC 總數</span>
          <strong>{stats.total_vm_count}</strong>
        </div>
        <div className={styles.summaryItem}>
          <span>CPU 核心</span>
          <strong>{Math.round(stats.used_cpu_cores)} / {stats.total_cpu_cores}</strong>
        </div>
        <div className={styles.summaryItem}>
          <span>記憶體</span>
          <strong>{Math.round(stats.used_mem_gb)} / {Math.round(stats.total_mem_gb)} GB</strong>
        </div>
      </div>

      <div className={styles.nodeGrid}>
        {stats.nodes.map((node) => (
          <div key={node.name} className={styles.card}>
            <div className={styles.cardHead}>
              <h2 className={styles.cardTitle}>{node.name}</h2>
              <span className={`${styles.badge} ${node.status === "online" ? styles.badge_success : styles.badge_danger}`}>
                {node.status === "online" ? "在線" : node.status}
              </span>
            </div>
            <UsageBar label="CPU" used={node.cpu_usage_pct} total={100} unit="%" />
            <UsageBar label="RAM" used={node.mem_used_gb} total={node.mem_total_gb} unit="GB" />
            <UsageBar label="Disk" used={node.disk_used_gb} total={node.disk_total_gb} unit="GB" />
            <span className={styles.cardHint}>{node.vm_count} 台 VM / LXC · {node.cpu_cores} 核心</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── PVE 連線 ──────────────────────────────────────── */
function PveTab({ config, form, setField, onSave, saving }) {
  const toast = useToast();
  const [testing, setTesting] = useState(false);
  const [syncing, setSyncing] = useState(false);

  async function handleTest() {
    setTesting(true);
    try {
      const res = await ProxmoxConfigService.testConnection();
      if (res.success) toast.success(res.message || "連線成功");
      else toast.error(res.message || "連線失敗");
    } catch (err) {
      toast.error(err?.message ?? "連線測試失敗");
    } finally {
      setTesting(false);
    }
  }

  async function handleSync() {
    setSyncing(true);
    try {
      const res = await ProxmoxConfigService.syncNow();
      if (res.success) toast.success(`同步完成：${res.nodes?.length ?? 0} 節點、${res.storage_count ?? 0} storage`);
      else toast.error(res.error || "同步失敗");
    } catch (err) {
      toast.error(err?.message ?? "同步失敗");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <form className={styles.panelStack} onSubmit={onSave}>
      <div className={styles.card}>
        <h2 className={styles.cardTitle}>PVE API 連線</h2>
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span>Host *</span>
            <input value={form.host} onChange={(e) => setField("host", e.target.value)} placeholder="例：192.168.100.2" required />
          </label>
          <label className={styles.field}>
            <span>API 使用者 *</span>
            <input value={form.user} onChange={(e) => setField("user", e.target.value)} placeholder="root@pam" required />
          </label>
          <label className={styles.field}>
            <span>密碼{config?.is_configured ? "（留空表示不變更）" : " *"}</span>
            <input
              type="password"
              value={form.password}
              onChange={(e) => setField("password", e.target.value)}
              placeholder={config?.is_configured ? "已設定" : "PVE 密碼"}
              required={!config?.is_configured}
            />
          </label>
          <label className={styles.field}>
            <span>Pool 名稱</span>
            <input value={form.pool_name} onChange={(e) => setField("pool_name", e.target.value)} />
          </label>
          <label className={styles.field}>
            <span>ISO Storage</span>
            <input value={form.iso_storage} onChange={(e) => setField("iso_storage", e.target.value)} />
          </label>
          <label className={styles.field}>
            <span>Data Storage</span>
            <input value={form.data_storage} onChange={(e) => setField("data_storage", e.target.value)} />
          </label>
          <label className={styles.field}>
            <span>API Timeout（秒）</span>
            <input type="number" min={1} value={form.api_timeout} onChange={(e) => setField("api_timeout", Number(e.target.value))} />
          </label>
          <label className={styles.field}>
            <span>任務檢查間隔（秒）</span>
            <input type="number" min={1} value={form.task_check_interval} onChange={(e) => setField("task_check_interval", Number(e.target.value))} />
          </label>
          <label className={styles.field}>
            <span>Gateway IP</span>
            <input value={form.gateway_ip ?? ""} onChange={(e) => setField("gateway_ip", e.target.value)} placeholder="選填" />
          </label>
          <label className={styles.field}>
            <span>內網網段</span>
            <input value={form.local_subnet ?? ""} onChange={(e) => setField("local_subnet", e.target.value)} placeholder="例：192.168.100.0/24" />
          </label>
        </div>
        <label className={styles.checkRow}>
          <input type="checkbox" checked={Boolean(form.verify_ssl)} onChange={(e) => setField("verify_ssl", e.target.checked)} />
          <span>驗證 SSL 憑證</span>
        </label>
        {form.verify_ssl && (
          <label className={styles.field}>
            <span>CA 憑證 PEM（留空表示不變更{config?.has_ca_cert ? "，目前已設定" : ""}）</span>
            <textarea
              rows={5}
              value={form.ca_cert}
              onChange={(e) => setField("ca_cert", e.target.value)}
              placeholder="-----BEGIN CERTIFICATE-----"
              spellCheck={false}
            />
            {config?.ca_fingerprint && (
              <em className={styles.fieldHint}>目前憑證指紋：{config.ca_fingerprint}</em>
            )}
          </label>
        )}
      </div>

      <div className={styles.cardActions}>
        <button type="button" className={styles.btnSecondary} onClick={handleTest} disabled={testing || !config?.is_configured}>
          <MIcon name="wifi_tethering" size={16} />
          {testing ? "測試中..." : "測試連線"}
        </button>
        <button type="button" className={styles.btnSecondary} onClick={handleSync} disabled={syncing || !config?.is_configured}>
          <MIcon name="sync" size={16} />
          {syncing ? "同步中..." : "立即同步節點 / Storage"}
        </button>
        <button type="submit" className={styles.btnPrimary} disabled={saving}>
          {saving ? "儲存中..." : "儲存設定"}
        </button>
      </div>
    </form>
  );
}

/* ── 資源排程 ──────────────────────────────────────── */
const SCHEDULER_GROUPS = [
  {
    title: "放置與超配",
    fields: [
      { key: "cpu_overcommit_ratio", label: "CPU 超配比", step: 0.1 },
      { key: "disk_overcommit_ratio", label: "Disk 超配比", step: 0.1 },
    ],
  },
  {
    title: "自動遷移",
    fields: [
      { key: "migration_max_per_rebalance", label: "單次重平衡最大遷移數" },
      { key: "migration_min_interval_minutes", label: "遷移最小間隔（分）" },
      { key: "migration_retry_limit", label: "遷移重試上限" },
      { key: "rebalance_migration_cost", label: "遷移成本權重", step: 0.01 },
    ],
  },
  {
    title: "重平衡閾值",
    fields: [
      { key: "rebalance_peak_cpu_margin", label: "CPU 峰值餘裕", step: 0.01 },
      { key: "rebalance_peak_memory_margin", label: "RAM 峰值餘裕", step: 0.01 },
      { key: "rebalance_loadavg_warn_per_core", label: "LoadAvg 警戒 / 核", step: 0.1 },
      { key: "rebalance_loadavg_max_per_core", label: "LoadAvg 上限 / 核", step: 0.1 },
      { key: "rebalance_loadavg_penalty_weight", label: "LoadAvg 懲罰權重", step: 0.01 },
      { key: "rebalance_cpu_peak_warn_share", label: "CPU 峰值警戒占比", step: 0.01 },
      { key: "rebalance_cpu_peak_high_share", label: "CPU 峰值高位占比", step: 0.01 },
      { key: "rebalance_memory_peak_warn_share", label: "RAM 峰值警戒占比", step: 0.01 },
      { key: "rebalance_memory_peak_high_share", label: "RAM 峰值高位占比", step: 0.01 },
      { key: "rebalance_resource_weight_cpu", label: "資源權重 CPU", step: 0.01 },
      { key: "rebalance_resource_weight_memory", label: "資源權重 RAM", step: 0.01 },
      { key: "rebalance_resource_weight_disk", label: "資源權重 Disk", step: 0.01 },
    ],
  },
  {
    title: "排程開機與時段",
    fields: [
      { key: "scheduled_boot_batch_size", label: "開機批次大小" },
      { key: "scheduled_boot_batch_interval_seconds", label: "批次間隔（秒）" },
      { key: "scheduled_boot_lead_time_minutes", label: "提前開機（分）" },
      { key: "window_grace_period_minutes", label: "時段寬限（分）" },
      { key: "practice_session_hours", label: "練習時段（小時）" },
      { key: "practice_warning_minutes", label: "練習提醒（分）" },
      { key: "expiry_warning_hours", label: "到期提醒（小時）" },
    ],
  },
];

function SchedulerTab({ form, setField, onSave, saving }) {
  return (
    <form className={styles.panelStack} onSubmit={onSave}>
      <div className={styles.card}>
        <h2 className={styles.cardTitle}>放置策略</h2>
        <div className={styles.strategyGrid}>
          {[
            {
              value: "dominant_share_min",
              title: "Dominant Share Min",
              desc: "每次選擇主要資源份額最低的節點，讓負載平均分散於整個叢集。",
            },
            {
              value: "priority_dominant_share",
              title: "Priority Dominant Share",
              desc: "先按節點優先級篩選候選節點，相同優先級內再以 Dominant Share 排序。",
            },
          ].map((opt) => (
            <button
              key={opt.value}
              type="button"
              className={form.placement_strategy === opt.value ? styles.strategyCardActive : styles.strategyCard}
              onClick={() => setField("placement_strategy", opt.value)}
            >
              <span className={styles.strategyTitle}>{opt.title}</span>
              <span className={styles.strategyDesc}>{opt.desc}</span>
            </button>
          ))}
        </div>
        <div className={styles.toggleGrid}>
          <label className={styles.checkRow}>
            <input
              type="checkbox"
              checked={Boolean(form.migration_enabled)}
              onChange={(e) => setField("migration_enabled", e.target.checked)}
            />
            <span>啟用自動遷移（重平衡）</span>
          </label>
          <label className={styles.checkRow}>
            <input
              type="checkbox"
              checked={Boolean(form.migration_lxc_live_enabled)}
              onChange={(e) => setField("migration_lxc_live_enabled", e.target.checked)}
            />
            <span>允許 LXC 線上遷移</span>
          </label>
        </div>
      </div>

      {SCHEDULER_GROUPS.map((group) => (
        <div key={group.title} className={styles.card}>
          <h2 className={styles.cardTitle}>{group.title}</h2>
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
              </label>
            ))}
          </div>
        </div>
      ))}

      <div className={styles.cardActions}>
        <button type="submit" className={styles.btnPrimary} disabled={saving}>
          {saving ? "儲存中..." : "儲存排程設定"}
        </button>
      </div>
    </form>
  );
}

/* ── 節點管理 ──────────────────────────────────────── */
function NodesTab() {
  const toast = useToast();
  const [nodes, setNodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null); // node id
  const [editForm, setEditForm] = useState({ host: "", port: 8006, priority: 0 });
  const [saving, setSaving] = useState(false);

  const fetchNodes = useCallback(() => {
    setLoading(true);
    ProxmoxConfigService.getNodes()
      .then(setNodes)
      .catch((err) => toast.error(err?.message ?? "載入節點失敗"))
      .finally(() => setLoading(false));
  }, [toast]);

  useEffect(() => {
    fetchNodes();
  }, [fetchNodes]);

  function startEdit(node) {
    setEditing(node.id);
    setEditForm({ host: node.host, port: node.port, priority: node.priority });
  }

  async function saveEdit(node) {
    setSaving(true);
    try {
      const updated = await ProxmoxConfigService.updateNode(node.id, {
        host: editForm.host.trim(),
        port: Number(editForm.port) || 8006,
        priority: Number(editForm.priority) || 0,
      });
      setNodes((prev) => prev.map((n) => (n.id === node.id ? updated : n)));
      toast.success("節點已更新");
      setEditing(null);
    } catch (err) {
      toast.error(err?.message ?? "更新節點失敗");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className={styles.loading}>載入節點...</div>;
  if (nodes.length === 0) {
    return (
      <div className={styles.empty}>
        <div className={styles.emptyIcon}><MIcon name="lock" size={40} /></div>
        <h2 className={styles.emptyTitle}>尚無節點資料</h2>
        <p className={styles.emptyDesc}>請先完成 PVE 連線設定並執行同步</p>
      </div>
    );
  }

  return (
    <div className={styles.list}>
      {nodes.map((node) => (
        <div key={node.id ?? node.name} className={styles.nodeRow}>
          <div className={styles.rowMain}>
            <span className={styles.rowName}>
              {node.name}
              {node.is_primary && <span className={`${styles.badge} ${styles.badge_info}`}>主節點</span>}
            </span>
            <span className={styles.rowMeta}>
              {node.host}:{node.port} · Priority {node.priority}
            </span>
          </div>
          <span className={`${styles.badge} ${node.is_online ? styles.badge_success : styles.badge_danger}`}>
            {node.is_online ? "在線" : "離線"}
          </span>
          {editing === node.id ? (
            <div className={styles.nodeEdit}>
              <input
                value={editForm.host}
                onChange={(e) => setEditForm((p) => ({ ...p, host: e.target.value }))}
                placeholder="Host"
              />
              <input
                type="number"
                value={editForm.port}
                onChange={(e) => setEditForm((p) => ({ ...p, port: e.target.value }))}
                placeholder="Port"
              />
              <input
                type="number"
                value={editForm.priority}
                onChange={(e) => setEditForm((p) => ({ ...p, priority: e.target.value }))}
                placeholder="Priority"
              />
              <button type="button" className={styles.btnPrimary} disabled={saving} onClick={() => saveEdit(node)}>
                {saving ? "..." : "儲存"}
              </button>
              <button type="button" className={styles.btnSecondary} onClick={() => setEditing(null)}>
                取消
              </button>
            </div>
          ) : (
            <button type="button" className={styles.btnSecondary} onClick={() => startEdit(node)} disabled={node.id == null}>
              <MIcon name="edit" size={16} />
              編輯
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

/* ── Storage ───────────────────────────────────────── */
function StorageTab() {
  const toast = useToast();
  const [storages, setStorages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [savingId, setSavingId] = useState(null);

  useEffect(() => {
    ProxmoxConfigService.getStorages()
      .then(setStorages)
      .catch((err) => toast.error(err?.message ?? "載入 Storage 失敗"))
      .finally(() => setLoading(false));
  }, [toast]);

  async function save(storage, patch) {
    setSavingId(storage.id);
    try {
      const updated = await ProxmoxConfigService.updateStorage(storage.id, {
        enabled: patch.enabled ?? storage.enabled,
        speed_tier: patch.speed_tier ?? storage.speed_tier,
        user_priority: patch.user_priority ?? storage.user_priority,
      });
      setStorages((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      toast.success(`${storage.storage} 已更新`);
    } catch (err) {
      toast.error(err?.message ?? "更新 Storage 失敗");
    } finally {
      setSavingId(null);
    }
  }

  if (loading) return <div className={styles.loading}>載入 Storage...</div>;
  if (storages.length === 0) {
    return (
      <div className={styles.empty}>
        <div className={styles.emptyIcon}><MIcon name="storage" size={40} /></div>
        <h2 className={styles.emptyTitle}>尚無 Storage 設定</h2>
        <p className={styles.emptyDesc}>請先完成 PVE 連線設定並執行同步</p>
      </div>
    );
  }

  return (
    <div className={styles.list}>
      {storages.map((storage) => (
        <div key={storage.id} className={styles.storageRow}>
          <div className={styles.rowMain}>
            <span className={styles.rowName}>
              {storage.storage}
              <span className={`${styles.badge} ${styles.badge_muted}`}>{storage.node_name}</span>
              {storage.is_shared && <span className={`${styles.badge} ${styles.badge_info}`}>共享</span>}
            </span>
            <span className={styles.rowMeta}>
              {storage.storage_type ?? "?"} · {Math.round(storage.used_gb)} / {Math.round(storage.total_gb)} GB ·
              {" "}{[storage.can_vm && "VM", storage.can_lxc && "LXC", storage.can_iso && "ISO", storage.can_backup && "Backup"].filter(Boolean).join(" / ") || "無用途"}
            </span>
          </div>
          <select
            value={storage.speed_tier}
            disabled={savingId === storage.id}
            onChange={(e) => save(storage, { speed_tier: e.target.value })}
            className={styles.inlineSelect}
          >
            <option value="nvme">NVMe</option>
            <option value="ssd">SSD</option>
            <option value="hdd">HDD</option>
            <option value="unknown">未知</option>
          </select>
          <input
            type="number"
            className={styles.inlineInput}
            title="使用者優先度"
            value={storage.user_priority}
            disabled={savingId === storage.id}
            onChange={(e) => save(storage, { user_priority: Number(e.target.value) || 0 })}
          />
          <label className={styles.checkRow}>
            <input
              type="checkbox"
              checked={storage.enabled}
              disabled={savingId === storage.id}
              onChange={(e) => save(storage, { enabled: e.target.checked })}
            />
            <span>啟用</span>
          </label>
        </div>
      ))}
    </div>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function SettingsPage() {
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("overview");
  const [config, setConfig] = useState(null);
  const [form, setForm] = useState(buildFormFromConfig(null));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    ProxmoxConfigService.getConfig()
      .then((cfg) => {
        setConfig(cfg);
        setForm(buildFormFromConfig(cfg));
      })
      .catch((err) => toast.error(err?.message ?? "載入 PVE 設定失敗"))
      .finally(() => setLoading(false));
  }, [toast]);

  const setField = useCallback((name, value) => {
    setForm((prev) => ({ ...prev, [name]: value }));
  }, []);

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await ProxmoxConfigService.updateConfig(buildPayload(form));
      setConfig(updated);
      setForm(buildFormFromConfig(updated));
      toast.success("設定已儲存");
    } catch (err) {
      toast.error(err?.message ?? "儲存設定失敗");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className={styles.page}>
      {/* ── 頁首 ── */}
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <div className={styles.titleRow}>
            <h1 className={styles.pageTitle}>系統設定</h1>
            {config?.is_configured && (
              <div className={styles.ipBadge}>
                <MIcon name="check_circle" size={12} />
                {config.host}
              </div>
            )}
          </div>
          <p className={styles.pageSubtitle}>
            管理 Proxmox VE 連線、節點、Storage 與資源排程設定。
          </p>
        </div>

        {/* ── Tabs ── */}
        <div className={styles.tabs}>
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`${styles.tab} ${activeTab === tab.key ? styles.tabActive : ""}`}
              onClick={() => setActiveTab(tab.key)}
            >
              <MIcon name={tab.icon} size={16} />
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── 內容 ── */}
      <div className={styles.content}>
        {loading ? (
          <div className={styles.loading}>載入設定...</div>
        ) : (
          <>
            {activeTab === "overview" && <OverviewTab />}
            {activeTab === "pve" && (
              <PveTab config={config} form={form} setField={setField} onSave={handleSave} saving={saving} />
            )}
            {activeTab === "scheduler" && (
              <SchedulerTab form={form} setField={setField} onSave={handleSave} saving={saving} />
            )}
            {activeTab === "governance" && <GovernanceTab />}
            {activeTab === "ldap" && <LdapTab />}
            {activeTab === "nodes" && <NodesTab />}
            {activeTab === "storage" && <StorageTab />}
          </>
        )}
      </div>
    </div>
  );
}
