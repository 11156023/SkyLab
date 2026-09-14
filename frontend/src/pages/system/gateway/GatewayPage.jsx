import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./GatewayPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import ConfigCodeEditor from "./ConfigCodeEditor";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { useToast } from "../../../hooks/useToast";
import { GatewayService } from "../../../services/gateway";
import PageHeader from "../../../components/PageHeader/PageHeader";

const SERVICE_FILES = {
  haproxy: { path: "/etc/haproxy/haproxy.cfg", language: "haproxy" },
  traefik: { path: "/etc/traefik/traefik.yml", language: "yaml" },
};

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let amount = bytes / 1024;
  let unit = units[0];
  for (let index = 1; amount >= 1024 && index < units.length; index += 1) {
    amount /= 1024;
    unit = units[index];
  }
  return `${amount >= 10 ? amount.toFixed(0) : amount.toFixed(1)} ${unit}`;
}

/* ── 連線設定 Tab ───────────────────────────────────── */
function ConnectionTab({ config, onConfigChange }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const [form, setForm] = useState({
    host: config?.host ?? "",
    ssh_port: config?.ssh_port ?? 22,
    ssh_user: config?.ssh_user ?? "root",
  });
  const [formDirty, setFormDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [resetting, setResetting] = useState(false);

  // 表單編輯中不跟著 config 重置，避免產生 Keypair 等操作吃掉未儲存的輸入
  useEffect(() => {
    if (formDirty) return;
    setForm({
      host: config?.host ?? "",
      ssh_port: config?.ssh_port ?? 22,
      ssh_user: config?.ssh_user ?? "root",
    });
  }, [config, formDirty]);

  function set(name, value) {
    setFormDirty(true);
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
      setFormDirty(false);
      onConfigChange(updated);
      toast.success(t("GatewayPage.toastConnectionSaved"));
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastSaveFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    try {
      const res = await GatewayService.testConnection();
      if (res.success) toast.success(res.message || t("GatewayPage.toastSshConnectSuccess"));
      else toast.error(res.message || t("GatewayPage.toastSshConnectFailed"));
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastConnectTestFailed"));
    } finally {
      setTesting(false);
    }
  }

  async function handleGenerateKeypair() {
    if (config?.public_key) {
      const ok = await confirm({
        title: t("GatewayPage.regenerateKeypairTitle"),
        message: t("GatewayPage.regenerateKeypairMessage"),
        confirmText: t("GatewayPage.regenerateKeypairConfirm"),
        danger: true,
      });
      if (!ok) return;
    }
    setGenerating(true);
    try {
      const updated = await GatewayService.generateKeypair();
      onConfigChange(updated);
      toast.success(t("GatewayPage.toastKeypairGenerated"));
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastGenerateKeypairFailed"));
    } finally {
      setGenerating(false);
    }
  }

  async function handleResetHostKey() {
    const ok = await confirm({
      title: t("GatewayPage.resetHostKeyTitle"),
      message: t("GatewayPage.resetHostKeyMessage"),
      confirmText: t("GatewayPage.resetHostKeyConfirm"),
      danger: true,
    });
    if (!ok) return;
    setResetting(true);
    try {
      const res = await GatewayService.resetHostKey();
      toast.success(res.message || t("GatewayPage.toastHostKeyReset"));
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastResetHostKeyFailed"));
    } finally {
      setResetting(false);
    }
  }

  function copyPublicKey() {
    if (!config?.public_key) return;
    navigator.clipboard.writeText(config.public_key).then(
      () => toast.success(t("GatewayPage.toastPublicKeyCopied")),
      () => toast.error(t("GatewayPage.toastCopyFailed")),
    );
  }

  return (
    <div className={styles.panelStack}>
      <form className={styles.card} onSubmit={handleSave}>
        <h2 className={styles.cardTitle}>{t("GatewayPage.sshConnectionTitle")}</h2>
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span>Host / IP *</span>
            <input
              value={form.host}
              onChange={(e) => set("host", e.target.value)}
              placeholder={t("GatewayPage.hostPlaceholder")}
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
            <span>{t("GatewayPage.sshUser")}</span>
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
            {testing ? t("GatewayPage.testing") : t("GatewayPage.testConnection")}
          </button>
          <button
            type="button"
            className={styles.btnSecondary}
            onClick={handleResetHostKey}
            disabled={resetting || !config?.host}
            title={t("GatewayPage.resetHostKeyHint")}
          >
            <MIcon name="key_off" size={16} />
            {resetting ? t("GatewayPage.resetting") : t("GatewayPage.resetHostKey")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={saving}>
            {saving ? t("GatewayPage.saving") : t("GatewayPage.saveConnectionSettings")}
          </button>
        </div>
      </form>

      <div className={styles.card}>
        <div className={styles.cardHead}>
          <h2 className={styles.cardTitle}>{t("GatewayPage.sshPublicKeyTitle")}</h2>
          <div className={styles.cardHeadActions}>
            <button type="button" className={styles.btnSecondary} onClick={copyPublicKey} disabled={!config?.public_key}>
              <MIcon name="content_copy" size={16} />
              {t("GatewayPage.copy")}
            </button>
            <button type="button" className={styles.btnSecondary} onClick={handleGenerateKeypair} disabled={generating}>
              <MIcon name="key" size={16} />
              {generating ? t("GatewayPage.generating") : t("GatewayPage.regenerateKeypair")}
            </button>
          </div>
        </div>
        <p className={styles.cardHint}>
          {t("GatewayPage.publicKeyHint")}
        </p>
        <pre className={styles.keyBlock}>
          {config?.public_key || t("GatewayPage.keypairNotGenerated")}
        </pre>
      </div>
    </div>
  );
}

/* ── 服務管理 Tab ───────────────────────────────────── */
function ServiceTab({ service, gatewayReady, host, onDirtyChange }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const SERVICE_ACTIONS = [
    { action: "start",   label: t("GatewayPage.actionStart"),   icon: "play_arrow" },
    { action: "stop",    label: t("GatewayPage.actionStop"),    icon: "stop" },
    { action: "restart", label: t("GatewayPage.actionRestart"), icon: "restart_alt" },
    { action: "reload",  label: "Reload", icon: "refresh" },
  ];
  const [status, setStatus] = useState(null);
  const [configText, setConfigText] = useState("");
  const [savedText, setSavedText] = useState("");
  const [configLoadFailed, setConfigLoadFailed] = useState(false);
  const [logs, setLogs] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [acting, setActing] = useState(null);
  const [loadingLogs, setLoadingLogs] = useState(false);

  const file = SERVICE_FILES[service];
  const dirty = configText !== savedText;

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [statusRes, configRes, logsRes] = await Promise.all([
        GatewayService.getServiceStatus(service).catch(() => null),
        GatewayService.readServiceConfig(service).catch(() => null),
        GatewayService.getServiceLogs(service, 100).catch(() => null),
      ]);
      setStatus(statusRes);
      setLogs(logsRes);
      // 讀取失敗（configRes 為 null）不可與「檔案是空的」混為一談，
      // 否則空白編輯器會顯示「已同步」，寫入時直接覆蓋遠端設定檔
      const failed = configRes === null;
      setConfigLoadFailed(failed);
      setConfigText(configRes?.content ?? "");
      setSavedText(configRes?.content ?? "");
      if (failed) toast.error(t("GatewayPage.toastConfigReadFailed", { service }));
    } finally {
      setLoading(false);
    }
  }, [service, toast, t]);

  useEffect(() => {
    if (gatewayReady) fetchAll();
    else setLoading(false);
  }, [gatewayReady, fetchAll]);

  // 把 dirty 回報給 GatewayPage，讓分頁切換能攔截未寫入變更
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  // dirty 時擋瀏覽器重新整理 / 關閉
  useEffect(() => {
    if (!dirty) return undefined;
    const handler = (e) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  async function handleAction(action) {
    setActing(action);
    try {
      const res = await GatewayService.controlService(service, action);
      if (res.success) toast.success(t("GatewayPage.toastServiceActionSuccess", { service, action }));
      else toast.error(res.output || t("GatewayPage.toastServiceActionFailed", { service, action }));
      const statusRes = await GatewayService.getServiceStatus(service).catch(() => null);
      setStatus(statusRes);
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastActionFailed", { action }));
    } finally {
      setActing(null);
    }
  }

  async function handleSaveConfig() {
    if (configLoadFailed) return;
    setSaving(true);
    try {
      await GatewayService.writeServiceConfig(service, configText);
      setSavedText(configText);
      toast.success(t("GatewayPage.toastConfigWritten"));
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastWriteConfigFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleReload() {
    if (dirty) {
      const ok = await confirm({
        title: t("GatewayPage.reloadConfigTitle"),
        message: t("GatewayPage.reloadConfigMessage"),
        confirmText: t("GatewayPage.reloadConfigConfirm"),
        danger: true,
      });
      if (!ok) return;
    }
    fetchAll();
  }

  async function handleRefreshLogs() {
    setLoadingLogs(true);
    try {
      setLogs(await GatewayService.getServiceLogs(service, 100));
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastLoadLogsFailed"));
    } finally {
      setLoadingLogs(false);
    }
  }

  if (!gatewayReady) {
    return (
      <EmptyState
        icon="dns"
        title={t("GatewayPage.emptyNotConfigured")}
      />
    );
  }

  if (loading) {
    return <LoadingState text={t("GatewayPage.loadingServiceStatus", { service })} />;
  }

  return (
    <div className={styles.serviceLayout}>
      <div className={`${styles.card} ${styles.areaStatus}`}>
        <div className={styles.cardHead}>
          <div className={styles.statusRow}>
            <h2 className={styles.cardTitle}>{service}</h2>
            {status ? (
              <span className={`${styles.badge} ${status.active ? styles.badge_success : styles.badge_muted}`}>
                <MIcon name={status.active ? "check_circle" : "cancel"} size={13} />
                {status.active ? t("GatewayPage.statusRunning") : t("GatewayPage.statusStopped")}
              </span>
            ) : (
              <span className={`${styles.badge} ${styles.badge_danger}`}>{t("GatewayPage.statusUnavailable")}</span>
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

      <div className={styles.areaEditor}>
        <ConfigCodeEditor
          fileName={file.path.split("/").pop()}
          filePath={file.path}
          language={file.language}
          value={configText}
          onChange={setConfigText}
          dirty={dirty}
          saving={saving}
          busy={acting !== null}
          loadFailed={configLoadFailed}
          host={host}
          onSave={handleSaveConfig}
          onReload={handleReload}
        />
      </div>

      <div className={`${styles.card} ${styles.areaLogs}`}>
        <div className={styles.cardHead}>
          <h2 className={styles.cardTitle}>{t("GatewayPage.serviceLogsTitle")}</h2>
          <button type="button" className={styles.btnSecondary} onClick={handleRefreshLogs} disabled={loadingLogs}>
            <MIcon name="refresh" size={16} />
            {loadingLogs ? t("GatewayPage.loadingLogs") : t("GatewayPage.refresh")}
          </button>
        </div>
        <pre className={styles.logBlock}>
          {loadingLogs
            ? t("GatewayPage.loadingLogs")
            : logs === null
              ? t("GatewayPage.logsLoadFailed")
              : logs || t("GatewayPage.noLogOutput")}
        </pre>
      </div>
    </div>
  );
}

function WireGuardTab({ gatewayReady }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const [overview, setOverview] = useState(null);
  const [status, setStatus] = useState(null);
  const [logs, setLogs] = useState(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(null);
  const [loadingLogs, setLoadingLogs] = useState(false);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    const [overviewRes, statusRes, logsRes] = await Promise.all([
      GatewayService.getWireGuardOverview().catch(() => null),
      GatewayService.getServiceStatus("wireguard").catch(() => null),
      GatewayService.getServiceLogs("wireguard", 100).catch(() => null),
    ]);
    setOverview(overviewRes);
    setStatus(statusRes);
    setLogs(logsRes);
    if (!overviewRes) toast.error(t("GatewayPage.wireGuardLoadFailed"));
    setLoading(false);
  }, [t, toast]);

  useEffect(() => {
    if (gatewayReady) fetchAll();
    else setLoading(false);
  }, [gatewayReady, fetchAll]);

  async function handleAction(action) {
    setActing(action);
    try {
      const result = await GatewayService.controlService("wireguard", action);
      if (result.success) {
        toast.success(t("GatewayPage.toastServiceActionSuccess", { service: "WireGuard", action }));
      } else {
        toast.error(result.output || t("GatewayPage.toastServiceActionFailed", { service: "WireGuard", action }));
      }
      const [nextOverview, nextStatus] = await Promise.all([
        GatewayService.getWireGuardOverview().catch(() => overview),
        GatewayService.getServiceStatus("wireguard").catch(() => null),
      ]);
      setOverview(nextOverview);
      setStatus(nextStatus);
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastActionFailed", { action }));
    } finally {
      setActing(null);
    }
  }

  async function handleRefreshLogs() {
    setLoadingLogs(true);
    try {
      setLogs(await GatewayService.getServiceLogs("wireguard", 100));
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastLoadLogsFailed"));
    } finally {
      setLoadingLogs(false);
    }
  }

  if (!gatewayReady) {
    return <EmptyState icon="vpn_key" title={t("GatewayPage.emptyNotConfigured")} />;
  }

  if (loading) {
    return <LoadingState text={t("GatewayPage.wireGuardLoading")} />;
  }

  const sessionTtlHours = overview
    ? Math.round((overview.session_ttl_seconds / 3600) * 10) / 10
    : null;

  const details = overview ? [
    [t("GatewayPage.wireGuardMode"), overview.mode],
    [t("GatewayPage.wireGuardInterface"), overview.interface],
    [t("GatewayPage.wireGuardSystemdUnit"), overview.systemd_unit],
    [t("GatewayPage.wireGuardEndpoint"), overview.endpoint],
    [t("GatewayPage.wireGuardClientSubnet"), overview.client_subnet],
    [t("GatewayPage.wireGuardVmSubnet"), overview.vm_subnet],
    [t("GatewayPage.wireGuardSessionTtl"), t("GatewayPage.wireGuardHours", { hours: sessionTtlHours })],
    [t("GatewayPage.wireGuardReconciler"), overview.reconcile_enabled
      ? t("GatewayPage.wireGuardEnabled")
      : t("GatewayPage.wireGuardDisabled")],
    [t("GatewayPage.wireGuardListenPort"), overview.listen_port ?? t("GatewayPage.wireGuardInspectionUnavailable")],
  ] : [];

  return (
    <div className={styles.wireguardLayout}>
      <div className={styles.card}>
        <div className={styles.cardHead}>
          <div>
            <div className={styles.statusRow}>
              <h2 className={styles.cardTitle}>WireGuard VPN</h2>
              {status ? (
                <span className={`${styles.badge} ${status.active ? styles.badge_success : styles.badge_muted}`}>
                  <MIcon name={status.active ? "check_circle" : "cancel"} size={13} />
                  {status.active ? t("GatewayPage.statusRunning") : t("GatewayPage.statusStopped")}
                </span>
              ) : (
                <span className={`${styles.badge} ${styles.badge_danger}`}>
                  {t("GatewayPage.statusUnavailable")}
                </span>
              )}
            </div>
            <p className={styles.cardHint}>{t("GatewayPage.wireGuardDescription")}</p>
          </div>
          <div className={styles.cardHeadActions}>
            {[
              { action: "start", label: t("GatewayPage.actionStart"), icon: "play_arrow" },
              { action: "stop", label: t("GatewayPage.actionStop"), icon: "stop" },
              { action: "restart", label: t("GatewayPage.actionRestart"), icon: "restart_alt" },
            ].map(({ action, label, icon }) => (
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
        {status?.status_text && <pre className={styles.statusBlock}>{status.status_text}</pre>}
      </div>

      {overview ? (
        <>
          <div className={styles.wireguardMetrics}>
            <div className={styles.metricCard}>
              <MIcon name="verified_user" size={22} />
              <div><strong>{overview.authorized_sessions}</strong><span>{t("GatewayPage.wireGuardAuthorizedSessions")}</span></div>
            </div>
            <div className={styles.metricCard}>
              <MIcon name="hub" size={22} />
              <div><strong>{overview.live_peers}</strong><span>{t("GatewayPage.wireGuardLivePeers")}</span></div>
            </div>
            <div className={styles.metricCard}>
              <MIcon name="sync_alt" size={22} />
              <div><strong>{overview.recent_handshakes}</strong><span>{t("GatewayPage.wireGuardRecentHandshakes")}</span></div>
            </div>
            <div className={styles.metricCard}>
              <MIcon name="data_usage" size={22} />
              <div>
                <strong>{formatBytes(overview.transfer_rx_bytes)} / {formatBytes(overview.transfer_tx_bytes)}</strong>
                <span>{t("GatewayPage.wireGuardTraffic")}</span>
              </div>
            </div>
          </div>

          {overview.expired_sessions > 0 && (
            <div className={styles.warningNote}>
              <MIcon name="warning" size={18} />
              {t("GatewayPage.wireGuardExpiredSessions", { count: overview.expired_sessions })}
            </div>
          )}

          <div className={styles.card}>
            <div className={styles.cardHead}>
              <h2 className={styles.cardTitle}>{t("GatewayPage.wireGuardOverviewTitle")}</h2>
              {!overview.inspection_available && (
                <span className={`${styles.badge} ${styles.badge_muted}`}>
                  {t("GatewayPage.wireGuardInspectionUnavailable")}
                </span>
              )}
            </div>
            <dl className={styles.detailGrid}>
              {details.map(([label, value]) => (
                <div className={styles.detailItem} key={label}>
                  <dt>{label}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
            <div className={styles.securityNote}>
              <MIcon name="shield" size={20} />
              <div>
                <strong>{t("GatewayPage.wireGuardSecurityTitle")}</strong>
                <span>{t("GatewayPage.wireGuardSecurityHint")}</span>
              </div>
            </div>
          </div>
        </>
      ) : (
        <EmptyState icon="vpn_key_off" title={t("GatewayPage.wireGuardLoadFailed")} />
      )}

      <div className={styles.card}>
        <div className={styles.cardHead}>
          <h2 className={styles.cardTitle}>{t("GatewayPage.serviceLogsTitle")}</h2>
          <button type="button" className={styles.btnSecondary} onClick={handleRefreshLogs} disabled={loadingLogs}>
            <MIcon name="refresh" size={16} />
            {loadingLogs ? t("GatewayPage.loadingLogs") : t("GatewayPage.refresh")}
          </button>
        </div>
        <pre className={styles.logBlock}>
          {loadingLogs
            ? t("GatewayPage.loadingLogs")
            : logs === null
              ? t("GatewayPage.logsLoadFailed")
              : logs || t("GatewayPage.noLogOutput")}
        </pre>
      </div>
    </div>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function GatewayPage() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const [activeTab, setActiveTab] = useState("connection");
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const dirtyRef = useRef(false);

  const TABS = [
    { key: "connection", label: t("GatewayPage.tabConnection") },
    { key: "haproxy",    label: "haproxy"  },
    { key: "traefik",    label: "Traefik"  },
    { key: "wireguard",  label: t("GatewayPage.tabWireGuard") },
  ];

  const handleDirtyChange = useCallback((dirty) => {
    dirtyRef.current = dirty;
  }, []);

  async function handleTabSelect(key) {
    if (key === activeTab) return;
    if (dirtyRef.current) {
      const ok = await confirm({
        title: t("GatewayPage.switchTabTitle"),
        message: t("GatewayPage.switchTabMessage"),
        confirmText: t("GatewayPage.switchTabConfirm"),
        danger: true,
      });
      if (!ok) return;
    }
    setActiveTab(key);
  }

  useEffect(() => {
    GatewayService.getConfig()
      .then(setConfig)
      .catch((err) => toast.error(err?.message ?? t("GatewayPage.toastLoadConfigFailed")))
      .finally(() => setLoading(false));
  }, [toast, t]);

  return (
    <div className={styles.page}>
      <PageHeader title={t("GatewayPage.pageTitle")} subtitle={t("GatewayPage.pageSubtitle")}>

        <div className={styles.tabs}>
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`${styles.tab} ${activeTab === tab.key ? styles.tabActive : ""}`}
              onClick={() => handleTabSelect(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </PageHeader>

      <div className={styles.content}>
        {loading ? (
          <LoadingState fullPage text={t("GatewayPage.loadingConfig")} />
        ) : activeTab === "connection" ? (
          <ConnectionTab config={config} onConfigChange={setConfig} />
        ) : activeTab === "wireguard" ? (
          <WireGuardTab gatewayReady={Boolean(config?.is_configured)} />
        ) : (
          <ServiceTab
            key={activeTab}
            service={activeTab}
            gatewayReady={Boolean(config?.is_configured)}
            host={config?.host}
            onDirtyChange={handleDirtyChange}
          />
        )}
      </div>
    </div>
  );
}
