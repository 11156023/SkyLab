import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./GatewayPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import { GatewayService } from "../../../services/gateway";
import { ReverseProxyService } from "../../../services/reverseProxy";
import {
  firstIpv4,
  hasCoreServices,
  toInstallForm,
  toInstallPayload,
  validateInstallForm,
} from "./installForm";

/* 安裝進行中每 3 秒經 SSH 讀一次日誌；其他狀態不輪詢 */
const POLL_MS = 3_000;

const STATE_BADGES = {
  idle:        { tone: styles.badge_muted,   icon: "radio_button_unchecked" },
  running:     { tone: styles.badge_info,    icon: "autorenew", spin: true },
  succeeded:   { tone: styles.badge_success, icon: "check_circle" },
  failed:      { tone: styles.badge_danger,  icon: "error" },
  interrupted: { tone: styles.badge_warning, icon: "warning" },
};

const COMPONENTS = [
  ["nginx", "nginx"],
  ["wireguard", "WireGuard"],
  ["certbot", "certbot"],
  ["ufw", "UFW"],
];

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function InterfaceSelect({ label, value, interfaces, onChange, disabled }) {
  const { t } = useTranslation("system");
  const names = interfaces.map((iface) => iface.name);
  return (
    <label className={styles.field}>
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
        {/* 偵測不到網卡時保留目前的值，避免選單變成空的 */}
        {!names.includes(value) && <option value={value}>{value}</option>}
        {interfaces.map((iface) => (
          <option key={iface.name} value={iface.name}>
            {iface.name}（{iface.addresses.length ? iface.addresses.join(", ") : t("GatewayPage.installNoIpv4")}）
          </option>
        ))}
      </select>
    </label>
  );
}

/* ── 安裝服務 Tab ───────────────────────────────────── */
export default function GatewayInstallTab({ gatewayReady, onGoToConnection }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const [status, setStatus] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState(null);
  const [starting, setStarting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const prevStateRef = useRef(null);
  const inFlightRef = useRef(false);
  const logRef = useRef(null);

  const syncRules = useCallback(async () => {
    setSyncing(true);
    try {
      const res = await ReverseProxyService.syncRules();
      toast.success(res?.message ?? t("GatewayPage.toastInstallRulesSynced"));
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastInstallRulesSyncFailed"));
    } finally {
      setSyncing(false);
    }
  }, [t, toast]);

  /* 套用新狀態；只有親眼看到「安裝中 → 結束」才提示，重新整理頁面看到舊結果不會再跳 */
  const applyStatus = useCallback((next) => {
    const prev = prevStateRef.current;
    prevStateRef.current = next.state;
    setStatus(next);
    setLoadError(null);
    setForm((current) => current ?? toInstallForm(next.defaults));
    if (prev !== "running") return;
    if (next.state === "succeeded") {
      toast.success(t("GatewayPage.toastInstallSucceeded"));
      // 重跑 install.sh 會清空自動產生的 http.conf／stream.conf，要把 DB 規則寫回去
      syncRules();
    } else if (next.state === "failed" || next.state === "interrupted") {
      toast.error(t("GatewayPage.toastInstallFailed"));
    }
  }, [syncRules, t, toast]);

  const refresh = useCallback(async ({ silent = false } = {}) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (!silent) setLoading(true);
    try {
      applyStatus(await GatewayService.getInstallStatus());
    } catch (err) {
      // 輪詢時偶發的 SSH 失敗不蓋掉畫面，只有手動載入才顯示錯誤
      if (!silent) setLoadError(err?.message ?? t("Error.generic", { ns: "common" }));
    } finally {
      inFlightRef.current = false;
      if (!silent) setLoading(false);
    }
  }, [applyStatus, t]);

  useEffect(() => {
    if (gatewayReady) refresh();
    else setLoading(false);
  }, [gatewayReady, refresh]);

  useAutoRefresh(() => {
    if (status?.state === "running") refresh({ silent: true });
  }, POLL_MS);

  // 安裝中日誌持續長出來，捲到最底
  useEffect(() => {
    if (status?.state !== "running" || !logRef.current) return;
    logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [status]);

  function setField(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  function handleVmInterfaceChange(name) {
    // SNAT 位址必須在 VM 內網介面上，換介面就帶入那張卡的第一個 IPv4
    const address = firstIpv4(status?.interfaces?.find((iface) => iface.name === name));
    setForm((prev) => ({ ...prev, vm_interface: name, snat_address: address ?? prev.snat_address }));
  }

  async function handleStart() {
    const reinstall = status?.state === "succeeded" || hasCoreServices(status);
    const ok = await confirm({
      title: reinstall ? t("GatewayPage.installConfirmReinstallTitle") : t("GatewayPage.installConfirmTitle"),
      message: t("GatewayPage.installConfirmMessage"),
      confirmText: t("GatewayPage.installConfirmButton"),
    });
    if (!ok) return;
    setStarting(true);
    try {
      const next = await GatewayService.startInstall(toInstallPayload(form));
      prevStateRef.current = "running";
      applyStatus(next);
      toast.success(t("GatewayPage.toastInstallStarted"));
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastInstallStartFailed"));
    } finally {
      setStarting(false);
    }
  }

  if (!gatewayReady) {
    return <EmptyState icon="install_desktop" title={t("GatewayPage.installNeedsSshKey")} action={<button type="button" className={styles.btnPrimary} onClick={onGoToConnection}><MIcon name="settings_ethernet" size={16} />{t("GatewayPage.goToConnection")}</button>} />;
  }

  if (loading && !status) {
    return <LoadingState text={t("GatewayPage.installLoading")} />;
  }

  if (!status) {
    return (
      <div className={styles.panelStack}>
        <div className={styles.card}>
          <div className={styles.cardHead}>
            <h2 className={styles.cardTitle}>{t("GatewayPage.installTitle")}</h2>
            <button type="button" className={styles.btnSecondary} onClick={() => refresh()}>
              <MIcon name="refresh" size={16} />
              {t("GatewayPage.installRetry")}
            </button>
          </div>
          <div className={styles.warningNote}>
            <MIcon name="warning" size={18} />
            <div className={styles.noteText}>
              <strong>{t("GatewayPage.installConnectErrorTitle")}</strong>
              <span>{loadError}</span>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const running = status.state === "running";
  const badge = STATE_BADGES[status.state] ?? STATE_BADGES.idle;
  const formError = form ? validateInstallForm(form) : null;
  const installed = hasCoreServices(status);
  const canStart = status.root_access && !running && !starting && !formError && form;
  const fixedDetails = [
    [t("GatewayPage.wireGuardInterface"), status.wireguard_interface],
    [t("GatewayPage.wireGuardClientSubnet"), status.wireguard_client_subnet],
    [t("GatewayPage.wireGuardVmSubnet"), status.wireguard_vm_subnet],
    [t("GatewayPage.installOs"), status.os_name ?? "—"],
    [t("GatewayPage.installStartedAt"), formatTime(status.started_at)],
    [t("GatewayPage.installFinishedAt"), running ? "—" : formatTime(status.finished_at)],
  ];

  return (
    <div className={styles.panelStack}>
      <div className={styles.card}>
        <div className={styles.cardHead}>
          <div className={styles.statusRow}>
            <h2 className={styles.cardTitle}>{t("GatewayPage.installTitle")}</h2>
            <span className={`${styles.badge} ${badge.tone}`}>
              <MIcon name={badge.icon} size={13} spin={badge.spin} />
              {t(`GatewayPage.installState_${status.state}`)}
              {status.state === "failed" && status.exit_code !== null && ` (exit ${status.exit_code})`}
            </span>
          </div>
          <div className={styles.cardHeadActions}>
            <button type="button" className={styles.btnSecondary} onClick={() => refresh()} disabled={loading}>
              <MIcon name="refresh" size={16} spin={loading} />
              {t("GatewayPage.installRefresh")}
            </button>
            {status.state === "succeeded" && (
              <button type="button" className={styles.btnSecondary} onClick={syncRules} disabled={syncing}>
                <MIcon name="sync" size={16} spin={syncing} />
                {syncing ? t("GatewayPage.installSyncingRules") : t("GatewayPage.installSyncRules")}
              </button>
            )}
            <button type="button" className={styles.btnPrimary} onClick={handleStart} disabled={!canStart}>
              <MIcon name={running ? "autorenew" : "install_desktop"} size={16} spin={running || starting} />
              {running || starting
                ? t("GatewayPage.installRunning")
                : installed ? t("GatewayPage.installReinstall") : t("GatewayPage.installStart")}
            </button>
          </div>
        </div>

        <div className={styles.componentRow}>
          {COMPONENTS.map(([key, label]) => {
            const present = Boolean(status.components?.[key]);
            return (
              <span
                key={key}
                className={`${styles.badge} ${present ? styles.badge_success : styles.badge_muted}`}
                title={present ? t("GatewayPage.installComponentPresent") : t("GatewayPage.installComponentMissing")}
              >
                <MIcon name={present ? "check_circle" : "remove_circle_outline"} size={13} />
                {label}
              </span>
            );
          })}
        </div>

        {!status.root_access && (
          <div className={styles.warningNote}>
            <MIcon name="admin_panel_settings" size={18} />
            {t("GatewayPage.installRootRequired")}
          </div>
        )}

        <dl className={styles.detailGrid}>
          {fixedDetails.map(([label, value]) => (
            <div className={styles.detailItem} key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </div>

      {form && (
        <div className={styles.card}>
          <div className={styles.cardHead}>
            <h2 className={styles.cardTitle}>{t("GatewayPage.installOptionsTitle")}</h2>
            <button
              type="button"
              className={styles.btnSecondary}
              onClick={() => setForm(toInstallForm(status.defaults))}
              disabled={running}
            >
              <MIcon name="auto_fix_high" size={16} />
              {t("GatewayPage.installUseDetected")}
            </button>
          </div>
          <div className={styles.installGrid}>
            <InterfaceSelect
              label={t("GatewayPage.installIngressInterface")}
              value={form.ingress_interface}
              interfaces={status.interfaces ?? []}
              onChange={(value) => setField("ingress_interface", value)}
              disabled={running}
            />
            <InterfaceSelect
              label={t("GatewayPage.installVmInterface")}
              value={form.vm_interface}
              interfaces={status.interfaces ?? []}
              onChange={handleVmInterfaceChange}
              disabled={running}
            />
            <label className={styles.field}>
              <span>{t("GatewayPage.installSnatAddress")}</span>
              <input
                value={form.snat_address}
                onChange={(e) => setField("snat_address", e.target.value)}
                placeholder="10.10.0.2"
                disabled={running}
              />
            </label>
            <label className={styles.field}>
              <span>{t("GatewayPage.installListenPort")}</span>
              <input
                type="number"
                min={1}
                max={65535}
                value={form.listen_port}
                onChange={(e) => setField("listen_port", e.target.value)}
                disabled={running}
              />
            </label>
            <label className={styles.field}>
              <span>{t("GatewayPage.installForwardStart")}</span>
              <input
                type="number"
                min={1}
                max={65535}
                value={form.forward_port_start}
                onChange={(e) => setField("forward_port_start", e.target.value)}
                disabled={running}
              />
            </label>
            <label className={styles.field}>
              <span>{t("GatewayPage.installForwardEnd")}</span>
              <input
                type="number"
                min={1}
                max={65535}
                value={form.forward_port_end}
                onChange={(e) => setField("forward_port_end", e.target.value)}
                disabled={running}
              />
            </label>
            <label className={`${styles.field} ${styles.fieldWide}`}>
              <span>{t("GatewayPage.installMonitoringSources")}</span>
              <input
                value={form.monitoring_allow_from}
                onChange={(e) => setField("monitoring_allow_from", e.target.value)}
                placeholder="192.168.100.20, 10.0.0.0/24"
                disabled={running}
              />
            </label>
          </div>

          {formError && (
            <div className={styles.warningNote}>
              <MIcon name="error_outline" size={18} />
              {t(`GatewayPage.${formError}`)}
            </div>
          )}

          <div className={styles.securityNote}>
            <MIcon name="info" size={20} />
            <div>
              <strong>{t("GatewayPage.installImpactTitle")}</strong>
              <span>{t("GatewayPage.installImpactHint")}</span>
            </div>
          </div>
        </div>
      )}

      <div className={styles.card}>
        <div className={styles.cardHead}>
          <h2 className={styles.cardTitle}>{t("GatewayPage.installLogTitle")}</h2>
        </div>
        <pre ref={logRef} className={`${styles.logBlock} ${styles.installLog}`}>
          {status.log || t("GatewayPage.installNoLog")}
        </pre>
      </div>
    </div>
  );
}
