import { useCallback, useEffect, useState } from "react";
import styles from "./GatewayPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import { GatewayService } from "../../../services/gateway";

const TABS = [
  { key: "connection", label: "連線設定" },
  { key: "haproxy",    label: "haproxy"  },
  { key: "traefik",    label: "Traefik"  },
  { key: "frps",       label: "frps"     },
  { key: "frpc",       label: "frpc"     },
];

const SERVICE_ACTIONS = [
  { action: "start",   label: "啟動",   icon: "play_arrow" },
  { action: "stop",    label: "停止",   icon: "stop" },
  { action: "restart", label: "重啟",   icon: "restart_alt" },
  { action: "reload",  label: "Reload", icon: "refresh" },
];

/* ── 連線設定 Tab ───────────────────────────────────── */
function ConnectionTab({ config, onConfigChange }) {
  const toast = useToast();
  const [form, setForm] = useState({
    host: config?.host ?? "",
    ssh_port: config?.ssh_port ?? 22,
    ssh_user: config?.ssh_user ?? "root",
  });
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    setForm({
      host: config?.host ?? "",
      ssh_port: config?.ssh_port ?? 22,
      ssh_user: config?.ssh_user ?? "root",
    });
  }, [config]);

  function set(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await GatewayService.updateConfig({
        host: form.host.trim(),
        ssh_port: Number(form.ssh_port) || 22,
        ssh_user: form.ssh_user.trim() || "root",
      });
      onConfigChange(updated);
      toast.success("連線設定已儲存");
    } catch (err) {
      toast.error(err?.message ?? "儲存失敗");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    try {
      const res = await GatewayService.testConnection();
      if (res.success) toast.success(res.message || "SSH 連線成功");
      else toast.error(res.message || "SSH 連線失敗");
    } catch (err) {
      toast.error(err?.message ?? "連線測試失敗");
    } finally {
      setTesting(false);
    }
  }

  async function handleGenerateKeypair() {
    setGenerating(true);
    try {
      const updated = await GatewayService.generateKeypair();
      onConfigChange(updated);
      toast.success("已產生新的 SSH Keypair，請將公鑰加到 Gateway VM");
    } catch (err) {
      toast.error(err?.message ?? "產生 Keypair 失敗");
    } finally {
      setGenerating(false);
    }
  }

  function copyPublicKey() {
    if (!config?.public_key) return;
    navigator.clipboard.writeText(config.public_key).then(
      () => toast.success("公鑰已複製"),
      () => toast.error("複製失敗"),
    );
  }

  return (
    <div className={styles.panelStack}>
      <form className={styles.card} onSubmit={handleSave}>
        <h2 className={styles.cardTitle}>SSH 連線設定</h2>
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span>Host / IP *</span>
            <input
              value={form.host}
              onChange={(e) => set("host", e.target.value)}
              placeholder="例：192.168.100.143"
              required
            />
          </label>
          <label className={styles.field}>
            <span>SSH Port</span>
            <input
              type="number"
              min={1}
              max={65535}
              value={form.ssh_port}
              onChange={(e) => set("ssh_port", e.target.value)}
            />
          </label>
          <label className={styles.field}>
            <span>SSH 使用者</span>
            <input
              value={form.ssh_user}
              onChange={(e) => set("ssh_user", e.target.value)}
              placeholder="root"
            />
          </label>
        </div>
        <div className={styles.cardActions}>
          <button type="button" className={styles.btnSecondary} onClick={handleTest} disabled={testing || !config?.is_configured}>
            <MIcon name="wifi_tethering" size={16} />
            {testing ? "測試中..." : "測試連線"}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={saving}>
            {saving ? "儲存中..." : "儲存設定"}
          </button>
        </div>
      </form>

      <div className={styles.card}>
        <div className={styles.cardHead}>
          <h2 className={styles.cardTitle}>SSH 公鑰</h2>
          <div className={styles.cardHeadActions}>
            <button type="button" className={styles.btnSecondary} onClick={copyPublicKey} disabled={!config?.public_key}>
              <MIcon name="content_copy" size={16} />
              複製
            </button>
            <button type="button" className={styles.btnSecondary} onClick={handleGenerateKeypair} disabled={generating}>
              <MIcon name="key" size={16} />
              {generating ? "產生中..." : "重新產生 Keypair"}
            </button>
          </div>
        </div>
        <p className={styles.cardHint}>
          將此公鑰加入 Gateway VM 的 ~/.ssh/authorized_keys，平台才能透過 SSH 管理服務。
        </p>
        <pre className={styles.keyBlock}>
          {config?.public_key || "尚未產生 Keypair"}
        </pre>
      </div>
    </div>
  );
}

/* ── 服務管理 Tab ───────────────────────────────────── */
function ServiceTab({ service, gatewayReady }) {
  const toast = useToast();
  const [status, setStatus] = useState(null);
  const [configText, setConfigText] = useState("");
  const [logs, setLogs] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [acting, setActing] = useState(null);
  const [showLogs, setShowLogs] = useState(false);
  const [loadingLogs, setLoadingLogs] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [statusRes, configRes] = await Promise.all([
        GatewayService.getServiceStatus(service).catch(() => null),
        GatewayService.readServiceConfig(service).catch(() => null),
      ]);
      setStatus(statusRes);
      setConfigText(configRes?.content ?? "");
    } finally {
      setLoading(false);
    }
  }, [service]);

  useEffect(() => {
    if (gatewayReady) fetchAll();
    else setLoading(false);
  }, [gatewayReady, fetchAll]);

  async function handleAction(action) {
    setActing(action);
    try {
      const res = await GatewayService.controlService(service, action);
      if (res.success) toast.success(`${service} ${action} 成功`);
      else toast.error(res.output || `${service} ${action} 失敗`);
      const statusRes = await GatewayService.getServiceStatus(service).catch(() => null);
      setStatus(statusRes);
    } catch (err) {
      toast.error(err?.message ?? `${action} 失敗`);
    } finally {
      setActing(null);
    }
  }

  async function handleSaveConfig() {
    setSaving(true);
    try {
      await GatewayService.writeServiceConfig(service, configText);
      toast.success("設定檔已寫入，記得 reload / restart 服務以套用");
    } catch (err) {
      toast.error(err?.message ?? "寫入設定檔失敗");
    } finally {
      setSaving(false);
    }
  }

  async function handleLoadLogs() {
    setShowLogs(true);
    setLoadingLogs(true);
    try {
      setLogs(await GatewayService.getServiceLogs(service, 100));
    } catch (err) {
      toast.error(err?.message ?? "載入日誌失敗");
    } finally {
      setLoadingLogs(false);
    }
  }

  if (!gatewayReady) {
    return (
      <div className={styles.empty}>
        <div className={styles.emptyIcon}>
          <MIcon name="dns" size={40} />
        </div>
        <h2 className={styles.emptyTitle}>尚未設定 Gateway 連線</h2>
        <p className={styles.emptyDesc}>請先到「連線設定」完成 SSH 連線設定並測試成功</p>
      </div>
    );
  }

  if (loading) {
    return <div className={styles.loading}>載入 {service} 狀態...</div>;
  }

  return (
    <div className={styles.panelStack}>
      <div className={styles.card}>
        <div className={styles.cardHead}>
          <div className={styles.statusRow}>
            <h2 className={styles.cardTitle}>{service}</h2>
            {status ? (
              <span className={`${styles.badge} ${status.active ? styles.badge_success : styles.badge_muted}`}>
                <MIcon name={status.active ? "check_circle" : "cancel"} size={13} />
                {status.active ? "運行中" : "已停止"}
              </span>
            ) : (
              <span className={`${styles.badge} ${styles.badge_danger}`}>無法取得狀態</span>
            )}
          </div>
          <div className={styles.cardHeadActions}>
            {SERVICE_ACTIONS.map(({ action, label, icon }) => (
              <button
                key={action}
                type="button"
                className={styles.btnSecondary}
                disabled={acting !== null}
                onClick={() => handleAction(action)}
              >
                <MIcon name={icon} size={16} />
                {acting === action ? "..." : label}
              </button>
            ))}
          </div>
        </div>
        {status?.status_text && (
          <pre className={styles.statusBlock}>{status.status_text}</pre>
        )}
      </div>

      <div className={styles.card}>
        <div className={styles.cardHead}>
          <h2 className={styles.cardTitle}>設定檔</h2>
          <div className={styles.cardHeadActions}>
            <button type="button" className={styles.btnSecondary} onClick={fetchAll} disabled={saving}>
              <MIcon name="refresh" size={16} />
              重新載入
            </button>
            <button type="button" className={styles.btnPrimary} onClick={handleSaveConfig} disabled={saving}>
              {saving ? "寫入中..." : "寫入設定檔"}
            </button>
          </div>
        </div>
        <textarea
          className={styles.configEditor}
          value={configText}
          onChange={(e) => setConfigText(e.target.value)}
          spellCheck={false}
          rows={16}
        />
      </div>

      <div className={styles.card}>
        <div className={styles.cardHead}>
          <h2 className={styles.cardTitle}>服務日誌</h2>
          <button type="button" className={styles.btnSecondary} onClick={handleLoadLogs} disabled={loadingLogs}>
            <MIcon name="terminal" size={16} />
            {loadingLogs ? "載入中..." : "載入最近 100 行"}
          </button>
        </div>
        {showLogs && (
          <pre className={styles.logBlock}>
            {loadingLogs ? "載入中..." : logs || "（無日誌輸出）"}
          </pre>
        )}
      </div>
    </div>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function GatewayPage() {
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("connection");
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    GatewayService.getConfig()
      .then(setConfig)
      .catch((err) => toast.error(err?.message ?? "載入 Gateway 設定失敗"))
      .finally(() => setLoading(false));
  }, [toast]);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <div className={styles.titleRow}>
            <h1 className={styles.pageTitle}>Gateway VM 管理</h1>
            {config?.is_configured && (
              <div className={styles.ipBadge}>
                <MIcon name="check_circle" size={12} />
                {config.host}
              </div>
            )}
          </div>
          <p className={styles.pageSubtitle}>管理 haproxy、Traefik、frp 服務設定與狀態</p>
        </div>

        <div className={styles.tabs}>
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`${styles.tab} ${activeTab === tab.key ? styles.tabActive : ""}`}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className={styles.content}>
        {loading ? (
          <div className={styles.loading}>載入 Gateway 設定...</div>
        ) : activeTab === "connection" ? (
          <ConnectionTab config={config} onConfigChange={setConfig} />
        ) : (
          <ServiceTab
            key={activeTab}
            service={activeTab}
            gatewayReady={Boolean(config?.is_configured)}
          />
        )}
      </div>
    </div>
  );
}
