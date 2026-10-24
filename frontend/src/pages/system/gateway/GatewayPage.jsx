import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./GatewayPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import GatewayCertificateTab from "./GatewayCertificateTab";
import GatewayInstallTab from "./GatewayInstallTab";
import GatewayPlatformEntryTab from "./GatewayPlatformEntryTab";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import EmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import { useToast } from "../../../hooks/useToast";
import { useQueryView } from "../../../hooks/useQueryLoadState";
import { GatewayService } from "../../../services/gateway";
import {
  useGatewayCacheUpdater,
  useGatewayConfigQuery,
  useGatewayServiceConfigQuery,
  useGatewayServiceLogsQuery,
  useGatewayServiceStatusQuery,
  useWireGuardOverviewQuery,
} from "../../../queries/gateway";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";

/* 日誌變化快，比站上預設的 30 秒更新得勤；每次重抓只是一次短暫的 SSH journalctl */
const LOG_REFRESH_MS = 10_000;
const LOG_LINES = 100;

// Monaco 很大：只有打開設定檔分頁時才載入（WireGuard／安裝分頁用不到）
const ConfigCodeEditor = lazy(() => import("./ConfigCodeEditor"));

/* 服務日誌走 TanStack Query 自動更新：分頁隱藏時暫停，前一次還沒回來不會再送
   （Gateway 連不上時 SSH 不會越堆越多），抓失敗就保留畫面上的內容；
   從來沒抓成功時回 null（卡片顯示讀取失敗）。服務操作後用 refetch 立即重抓。 */
function useLiveLogs(service, enabled) {
  const logsQuery = useGatewayServiceLogsQuery(service, LOG_LINES, { enabled, refetchInterval: LOG_REFRESH_MS });
  /* 首載失敗後每 10 秒的重抓期間不算載入中，整個分頁才不會每輪閃一次轉圈（見 useQueryView） */
  const logsView = useQueryView(logsQuery);
  return {
    logs: logsQuery.data !== undefined ? logsQuery.data : null,
    pending: enabled && logsView.loading,
    refetch: logsQuery.refetch,
  };
}

/* nginx.conf 是主設定；SkyLab 自動重建的 /etc/nginx/skylab/http.conf、stream.conf 不開放編輯 */
const SERVICE_FILES = {
  nginx: { path: "/etc/nginx/nginx.conf", language: "nginx" },
};

/* 頁面導覽（UserGuide）切換分頁用的錨點 */
const GUIDE_TAB_PROPS = {
  connection:  { "data-guide-tab": "gateway-connection" },
  install:     { "data-guide-tab": "gateway-install" },
  certificate: { "data-guide-tab": "gateway-certificate" },
  platform:    { "data-guide-tab": "gateway-platform" },
  nginx:       { "data-guide-tab": "gateway-nginx" },
  wireguard:   { "data-guide-tab": "gateway-wireguard" },
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
      <form className={styles.card} onSubmit={handleSave} data-guide="gateway-ssh-form">
        {/* 動作鈕放標題列右側，跟下方「SSH 公鑰」卡片同一套排法 */}
        <div className={styles.cardHead}>
          <h2 className={styles.cardTitle}>{t("GatewayPage.sshConnectionTitle")}</h2>
          <div className={styles.cardHeadActions}>
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
        </div>
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
      </form>

      <div className={styles.card} data-guide="gateway-ssh-key">
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

/* 服務動作清單：nginx 多一個 reload，WireGuard 只有啟動／停止／重新啟動 */
function serviceActions(t, { reload = false } = {}) {
  const actions = [
    { action: "start",   label: t("GatewayPage.actionStart"),   icon: "play_arrow" },
    { action: "stop",    label: t("GatewayPage.actionStop"),    icon: "stop" },
    { action: "restart", label: t("GatewayPage.actionRestart"), icon: "restart_alt" },
  ];
  if (reload) actions.push({ action: "reload", label: "Reload", icon: "refresh" });
  return actions;
}

/* 服務狀態徽章：status 為 null 代表查不到狀態 */
function ServiceStatusBadge({ status }) {
  const { t } = useTranslation("system");
  if (!status) {
    return <span className={`${styles.badge} ${styles.badge_danger}`}>{t("GatewayPage.statusUnavailable")}</span>;
  }
  return (
    <span className={`${styles.badge} ${status.active ? styles.badge_success : styles.badge_muted}`}>
      <MIcon name={status.active ? "check_circle" : "cancel"} size={13} />
      {status.active ? t("GatewayPage.statusRunning") : t("GatewayPage.statusStopped")}
    </span>
  );
}

/* 服務卡片右上的動作按鈕列：執行中的動作顯示「...」，同時只允許一個動作 */
function ServiceActionButtons({ actions, acting, onAction }) {
  return (
    <div className={styles.cardHeadActions}>
      {actions.map(({ action, label, icon }) => (
        <button
          key={action}
          type="button"
          className={styles.btnSecondary}
          disabled={acting !== null}
          onClick={() => onAction(action)}
        >
          <MIcon name={icon} size={16} />
          {acting === action ? "..." : label}
        </button>
      ))}
    </div>
  );
}

/* 服務日誌卡片：logs 為 null 代表抓取失敗、空字串代表沒有輸出 */
function ServiceLogsCard({ logs, className }) {
  const { t } = useTranslation("system");
  return (
    <div className={className ? `${styles.card} ${className}` : styles.card}>
      <div className={styles.cardHead}>
        <h2 className={styles.cardTitle}>{t("GatewayPage.serviceLogsTitle")}</h2>
      </div>
      <pre className={styles.logBlock}>
        {logs === null ? t("Error.generic", { ns: "common" }) : logs || t("GatewayPage.noLogOutput")}
      </pre>
    </div>
  );
}

/* ── 服務管理 Tab ───────────────────────────────────── */
function ServiceTab({ service, gatewayReady, host, onDirtyChange, onGoToConnection }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const gatewayCache = useGatewayCacheUpdater();
  const [saving, setSaving] = useState(false);
  const [acting, setActing] = useState(null);

  /* 狀態、設定檔、日誌走 TanStack Query；狀態與設定檔只在進分頁、操作後與按重新載入時抓，
     日誌每 10 秒自動更新。狀態抓不到就顯示未知（null） */
  const statusQuery = useGatewayServiceStatusQuery(service, { enabled: gatewayReady, refetchInterval: false });
  const status = statusQuery.isError ? null : statusQuery.data ?? null;
  const configQuery = useGatewayServiceConfigQuery(service, { enabled: gatewayReady });
  const { logs, pending: logsPending, refetch: refreshLogs } = useLiveLogs(service, gatewayReady);
  /* 設定檔重抓中（進分頁時的重抓、重新載入）也算載入中：不能讓人在快取的舊內容上開始編輯，
     寫回去時蓋掉 Gateway 上較新的設定 */
  const loading = gatewayReady
    && (statusQuery.isPending || configQuery.isPending || configQuery.isFetching || logsPending);

  /* 讀取失敗不可與「檔案是空的」混為一談，否則空白編輯器會顯示「已同步」，寫入時直接覆蓋遠端設定檔：
     失敗時編輯器清空並鎖住寫入，只留重新載入 */
  const configLoadFailed = configQuery.isError;
  const serverContent = configLoadFailed ? "" : configQuery.data?.content ?? "";
  const serverVersion = configLoadFailed ? configQuery.errorUpdatedAt : configQuery.dataUpdatedAt;
  /* 編輯中的文字與它所根據的那份內容；每抓回一份新內容就換進編輯器，但有未寫入的修改時不蓋掉
     （讀取失敗一律清空，跟原本一樣） */
  const [editor, setEditor] = useState({ text: serverContent, saved: serverContent });
  const configText = editor.text;
  const savedText = editor.saved;
  useEffect(() => {
    if (!serverVersion) return;
    setEditor((prev) => (
      !configLoadFailed && prev.text !== prev.saved ? prev : { text: serverContent, saved: serverContent }
    ));
  }, [serverVersion, serverContent, configLoadFailed]);
  /* 每次讀取失敗提示一次；重抓中先不提示（換分頁回來時快取裡上一次的失敗不再跳一次） */
  const configErrorAt = configLoadFailed && !configQuery.isFetching ? configQuery.errorUpdatedAt : 0;
  useEffect(() => {
    if (configErrorAt) toast.error(t("GatewayPage.toastConfigReadFailed", { service }));
  }, [configErrorAt, service, toast, t]);

  const file = SERVICE_FILES[service];
  const dirty = configText !== savedText;
  const setConfigText = (text) => setEditor((prev) => ({ ...prev, text }));

  /* 編輯器的「重新載入」：三份一起重抓 */
  function fetchAll() {
    statusQuery.refetch();
    configQuery.refetch();
    refreshLogs();
  }

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
      await statusQuery.refetch();
      refreshLogs();
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
      setEditor((prev) => ({ ...prev, saved: configText }));
      gatewayCache.setServiceConfig(service, configText);
      toast.success(t("GatewayPage.toastConfigWritten"));
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastWriteConfigFailed"));
    } finally {
      setSaving(false);
    }
  }

  if (!gatewayReady) {
    return (
      <EmptyState
        icon="dns"
        title={t("GatewayPage.emptyNotConfigured")}
        action={<button type="button" className={styles.btnPrimary} onClick={onGoToConnection}><MIcon name="settings_ethernet" size={16} />{t("GatewayPage.goToConnection")}</button>}
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
            <ServiceStatusBadge status={status} />
          </div>
          <ServiceActionButtons actions={serviceActions(t, { reload: true })} acting={acting} onAction={handleAction} />
        </div>
        {status?.status_text && (
          <pre className={styles.statusBlock}>{status.status_text}</pre>
        )}
      </div>

      <div className={styles.areaEditor}>
        <Suspense fallback={<LoadingState />}>
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
            onReload={fetchAll}
          />
        </Suspense>
      </div>

      <ServiceLogsCard logs={logs} className={styles.areaLogs} />
    </div>
  );
}

function WireGuardTab({ gatewayReady, onGoToConnection }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const [acting, setActing] = useState(null);

  /* 摘要、狀態、日誌走 TanStack Query；摘要與狀態只在進分頁、操作後與按重試時抓，日誌每 10 秒自動更新。
     操作後重抓摘要失敗時沿用上一份；狀態抓不到就顯示未知（null） */
  const overviewQuery = useWireGuardOverviewQuery({ enabled: gatewayReady, refetchInterval: false });
  const overview = overviewQuery.data ?? null;
  const statusQuery = useGatewayServiceStatusQuery("wireguard", { enabled: gatewayReady, refetchInterval: false });
  const status = statusQuery.isError ? null : statusQuery.data ?? null;
  const { logs, pending: logsPending, refetch: refreshLogs } = useLiveLogs("wireguard", gatewayReady);
  /* 首載失敗後的重抓（切回視窗、操作後）期間維持錯誤畫面，不閃回轉圈（見 useQueryView） */
  const overviewView = useQueryView(overviewQuery);
  const statusView = useQueryView(statusQuery);
  /* 摘要還沒有資料時按重試也顯示載入中（跟原本一樣整塊換成轉圈） */
  const loading = gatewayReady
    && (overviewView.loading || statusView.loading || logsPending);
  /* 摘要從來沒抓到（首次載入或重試失敗）才提示，跟原本一樣：每失敗一次提示一次 */
  const overviewErrorAt = overviewView.error ? overviewQuery.errorUpdatedAt : 0;
  useEffect(() => {
    if (overviewErrorAt) toast.error(t("Error.generic", { ns: "common" }));
  }, [overviewErrorAt, toast, t]);

  /* 重試：三份一起重抓（與原本相同），沒資料的回到載入中 */
  function fetchAll() {
    overviewView.retry();
    statusView.retry();
    refreshLogs();
  }

  async function handleAction(action) {
    setActing(action);
    try {
      const result = await GatewayService.controlService("wireguard", action);
      if (result.success) {
        toast.success(t("GatewayPage.toastServiceActionSuccess", { service: "WireGuard", action }));
      } else {
        toast.error(result.output || t("GatewayPage.toastServiceActionFailed", { service: "WireGuard", action }));
      }
      await Promise.all([overviewQuery.refetch(), statusQuery.refetch()]);
      refreshLogs();
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastActionFailed", { action }));
    } finally {
      setActing(null);
    }
  }

  if (!gatewayReady) {
    return <EmptyState icon="vpn_key" title={t("GatewayPage.emptyNotConfigured")} action={<button type="button" className={styles.btnPrimary} onClick={onGoToConnection}><MIcon name="settings_ethernet" size={16} />{t("GatewayPage.goToConnection")}</button>} />;
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
              <ServiceStatusBadge status={status} />
            </div>
            <p className={styles.cardHint}>{t("GatewayPage.wireGuardDescription")}</p>
          </div>
          <ServiceActionButtons actions={serviceActions(t)} acting={acting} onAction={handleAction} />
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
        <ErrorState onRetry={fetchAll} />
      )}

      <ServiceLogsCard logs={logs} />
    </div>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function GatewayPage() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const [activeTab, setActiveTab] = useState("connection");
  const dirtyRef = useRef(false);
  const gatewayCache = useGatewayCacheUpdater();

  /* Gateway 連線設定走 TanStack Query（不輪詢、不因切回分頁重抓）；
     連線分頁的表單編輯中不跟著換（ConnectionTab 自己判斷） */
  const configQuery = useGatewayConfigQuery();
  const config = configQuery.data ?? null;
  const loading = configQuery.data === undefined && (configQuery.isPending || configQuery.isFetching);
  const loadError = configQuery.error;
  useEffect(() => {
    if (loadError) toast.error(loadError?.message ?? t("GatewayPage.toastLoadConfigFailed"));
  }, [loadError, toast, t]);
  const setConfig = (next) => gatewayCache.setConfig(next);

  const TABS = [
    { key: "connection", label: t("GatewayPage.tabConnection") },
    { key: "install",    label: t("GatewayPage.tabInstall") },
    { key: "certificate", label: t("GatewayPage.tabCertificate") },
    { key: "platform",   label: t("GatewayPage.tabPlatformEntry") },
    { key: "nginx",      label: "nginx" },
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

  return (
    <div className={styles.page}>
      <PageHeader title={t("GatewayPage.pageTitle")}>

        {/* 切換前若設定檔有未存修改，handleTabSelect 會先跳確認 */}
        <SegmentedControl
          className={styles.tabs}
          ariaLabel={t("GatewayPage.tabsAriaLabel")}
          value={activeTab}
          onChange={handleTabSelect}
          options={TABS.map((tab) => ({
            value: tab.key,
            label: tab.label,
            buttonProps: GUIDE_TAB_PROPS[tab.key],
          }))}
        />
      </PageHeader>

      <div className={styles.content} data-guide="gateway-content">
        {loading ? (
          <LoadingState fullPage text={t("GatewayPage.loadingConfig")} />
        ) : activeTab === "connection" ? (
          <ConnectionTab config={config} onConfigChange={setConfig} />
        ) : activeTab === "install" ? (
          <GatewayInstallTab gatewayReady={Boolean(config?.is_configured)} onGoToConnection={() => handleTabSelect("connection")} />
        ) : activeTab === "certificate" ? (
          <GatewayCertificateTab
            gatewayReady={Boolean(config?.is_configured)}
            onDirtyChange={handleDirtyChange}
            onGoToConnection={() => handleTabSelect("connection")}
          />
        ) : activeTab === "platform" ? (
          <GatewayPlatformEntryTab
            gatewayReady={Boolean(config?.is_configured)}
            onDirtyChange={handleDirtyChange}
            onGoToConnection={() => handleTabSelect("connection")}
            onGoToCertificate={() => handleTabSelect("certificate")}
          />
        ) : activeTab === "wireguard" ? (
          <WireGuardTab gatewayReady={Boolean(config?.is_configured)} onGoToConnection={() => handleTabSelect("connection")} />
        ) : (
          <ServiceTab
            key={activeTab}
            service={activeTab}
            gatewayReady={Boolean(config?.is_configured)}
            host={config?.host}
            onDirtyChange={handleDirtyChange}
            onGoToConnection={() => handleTabSelect("connection")}
          />
        )}
      </div>
    </div>
  );
}
