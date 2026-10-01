/**
 * 初始化精靈步驟：Gateway（可略過）。
 *
 * 同一個畫面由上往下三段，前一段完成才出現下一段：
 *   1. SSH 連線設定 → 儲存時後端產生金鑰並回傳公鑰
 *   2. 管理員把公鑰貼到 Gateway 後「測試連線」
 *   3. 一鍵安裝（nginx／certbot／WireGuard），安裝在 Gateway 背景跑，這裡每 3 秒讀一次日誌
 * 表單驗證與送出內容和閘道頁的「安裝服務」分頁共用（installForm.js）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../../components/MIcon";
import { LoadingSpinner } from "../../components/LoadingState/LoadingState";
import useAutoRefresh from "../../hooks/useAutoRefresh";
import { useToast } from "../../hooks/useToast";
import { SetupService } from "../../services/setup";
import {
  firstIpv4,
  hasCoreServices,
  toInstallForm,
  toInstallPayload,
  validateInstallForm,
} from "../system/gateway/installForm";
import { Notice } from "./wizardParts";
import styles from "./SetupPage.module.scss";

/* 安裝進行中每 3 秒經 SSH 讀一次日誌；其他狀態不輪詢 */
const POLL_MS = 3_000;

const COMPONENTS = [
  ["nginx", "nginx"],
  ["wireguard", "WireGuard"],
  ["certbot", "certbot"],
  ["ufw", "UFW"],
];

const STATE_ICONS = {
  idle: "radio_button_unchecked",
  running: "autorenew",
  succeeded: "check_circle",
  failed: "error",
  interrupted: "warning",
};

function toConnectionForm(config) {
  return {
    host: config?.host ?? "",
    ssh_port: String(config?.ssh_port ?? 22),
    ssh_user: config?.ssh_user ?? "root",
  };
}

function connectionPayload(form) {
  return {
    host: form.host.trim(),
    ssh_port: Number(form.ssh_port) || 22,
    ssh_user: form.ssh_user.trim() || "root",
  };
}

function InterfaceSelect({ label, value, interfaces, onChange, disabled, noIpv4Text }) {
  const names = interfaces.map((iface) => iface.name);
  return (
    <label className={styles.field}>
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
        {/* 偵測不到網卡時保留目前的值，避免選單變成空的 */}
        {!names.includes(value) && <option value={value}>{value}</option>}
        {interfaces.map((iface) => (
          <option key={iface.name} value={iface.name}>
            {iface.name}（{iface.addresses.length ? iface.addresses.join(", ") : noIpv4Text}）
          </option>
        ))}
      </select>
    </label>
  );
}

export default function GatewayStep({ onConfigured, onSkip, onBack, onNext }) {
  const { t } = useTranslation("login");
  /* 安裝參數的欄位名稱與錯誤訊息沿用閘道頁那一份，兩邊用語才會一致 */
  const { t: ts } = useTranslation("system");
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [config, setConfig] = useState(null);
  const [form, setForm] = useState(toConnectionForm(null));
  const [savingConnection, setSavingConnection] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [install, setInstall] = useState(null);
  const [installForm, setInstallForm] = useState(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const prevStateRef = useRef(null);
  const logRef = useRef(null);

  /* 套用安裝狀態；只有親眼看到「安裝中 → 結束」才提示 */
  const applyInstall = useCallback((next) => {
    const prev = prevStateRef.current;
    prevStateRef.current = next.state;
    setInstall(next);
    setInstallForm((current) => current ?? toInstallForm(next.defaults));
    if (prev !== "running") return;
    if (next.state === "succeeded") toast.success(ts("GatewayPage.toastInstallSucceeded"));
    else if (next.state === "failed" || next.state === "interrupted") {
      toast.error(ts("GatewayPage.toastInstallFailed"));
    }
  }, [toast, ts]);

  const refreshInstall = useCallback(async ({ silent = false } = {}) => {
    try {
      applyInstall(await SetupService.getGatewayInstallStatus());
      return true;
    } catch (err) {
      // 輪詢時偶發的 SSH 失敗不蓋掉畫面
      if (!silent) setError(err?.message ?? t("SetupPage.gatewayInstallLoadFailed"));
      return false;
    }
  }, [applyInstall, t]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const current = await SetupService.getGateway();
        if (cancelled) return;
        setConfig(current);
        setForm(toConnectionForm(current));
        // 重新整理頁面回來：連線已設定好就直接試著讀安裝狀態，讀得到代表 SSH 是通的
        if (current.is_configured && (await refreshInstall({ silent: true })) && !cancelled) {
          setTestResult({ success: true, message: "" });
        }
      } catch (err) {
        if (!cancelled) setError(err?.message ?? t("SetupPage.gatewayLoadFailed"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [refreshInstall, t]);

  useAutoRefresh(() => {
    if (install?.state === "running") return refreshInstall({ silent: true });
    return undefined;
  }, POLL_MS);

  // 安裝中日誌持續長出來，捲到最底
  useEffect(() => {
    if (install?.state !== "running" || !logRef.current) return;
    logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [install]);

  const connectionDirty = JSON.stringify(connectionPayload(form))
    !== JSON.stringify(connectionPayload(toConnectionForm(config)));
  const connected = Boolean(testResult?.success) && !connectionDirty;
  const running = install?.state === "running";
  // 安裝到一半 nginx 可能已經在了，跑完才算裝好
  const installed = !running && (install?.state === "succeeded" || hasCoreServices(install));
  const installFormError = installForm ? validateInstallForm(installForm) : null;
  const busy = savingConnection || testing || starting;

  function setField(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  function setInstallField(name, value) {
    setInstallForm((prev) => ({ ...prev, [name]: value }));
  }

  function handleVmInterfaceChange(name) {
    // SNAT 位址必須在 VM 內網介面上，換介面就帶入那張卡的第一個 IPv4
    const address = firstIpv4(install?.interfaces?.find((iface) => iface.name === name));
    setInstallForm((prev) => ({ ...prev, vm_interface: name, snat_address: address ?? prev.snat_address }));
  }

  async function handleSaveConnection(e) {
    e.preventDefault();
    setError("");
    setSavingConnection(true);
    try {
      const saved = await SetupService.saveGateway(connectionPayload(form));
      setConfig(saved);
      setForm(toConnectionForm(saved));
      // 連線對象換了，之前的測試結果與安裝狀態都不算數
      setTestResult(null);
      setInstall(null);
      setInstallForm(null);
      prevStateRef.current = null;
      onConfigured(saved);
      toast.success(t("SetupPage.gatewayConnectionSaved"));
    } catch (err) {
      setError(err?.message ?? t("SetupPage.gatewaySaveFailed"));
    } finally {
      setSavingConnection(false);
    }
  }

  async function handleTest() {
    setError("");
    setTesting(true);
    try {
      const result = await SetupService.testGateway();
      setTestResult(result);
      if (result.success) await refreshInstall();
    } catch (err) {
      setTestResult({ success: false, message: err?.message ?? t("SetupPage.testFailed") });
    } finally {
      setTesting(false);
    }
  }

  async function handleInstall() {
    setError("");
    setStarting(true);
    try {
      const next = await SetupService.startGatewayInstall(toInstallPayload(installForm));
      prevStateRef.current = "running";
      applyInstall(next);
      toast.success(ts("GatewayPage.toastInstallStarted"));
    } catch (err) {
      setError(err?.message ?? ts("GatewayPage.toastInstallStartFailed"));
    } finally {
      setStarting(false);
    }
  }

  function copyPublicKey() {
    const write = navigator.clipboard?.writeText?.bind(navigator.clipboard);
    // http 連線（還沒有 https）沒有剪貼簿 API，下方的公鑰文字可以直接選取複製
    if (!write) {
      toast.error(ts("GatewayPage.toastCopyFailed"));
      return;
    }
    write(config.public_key).then(
      () => toast.success(ts("GatewayPage.toastPublicKeyCopied")),
      () => toast.error(ts("GatewayPage.toastCopyFailed")),
    );
  }

  if (loading) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>{t("SetupPage.gatewayTitle")}</h2>
        <div className={styles.center}><LoadingSpinner size={32} /></div>
      </section>
    );
  }

  return (
    <form className={styles.section} onSubmit={handleSaveConnection}>
      <h2 className={styles.sectionTitle}>{t("SetupPage.gatewayTitle")}</h2>
      <p className={styles.sectionDesc}>{t("SetupPage.gatewayDesc")}</p>

      <div className={styles.formGrid}>
        <label className={`${styles.field} ${styles.fieldWide}`}>
          <span>{t("SetupPage.gatewayHostLabel")} *</span>
          <input
            value={form.host}
            onChange={(e) => setField("host", e.target.value)}
            placeholder={t("SetupPage.gatewayHostPlaceholder")}
            disabled={busy || running}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{t("SetupPage.gatewaySshPortLabel")}</span>
          <input
            type="number"
            min={1}
            max={65535}
            value={form.ssh_port}
            onChange={(e) => setField("ssh_port", e.target.value)}
            disabled={busy || running}
          />
        </label>
        <label className={styles.field}>
          <span>{t("SetupPage.gatewaySshUserLabel")}</span>
          <input
            value={form.ssh_user}
            onChange={(e) => setField("ssh_user", e.target.value)}
            placeholder="root"
            disabled={busy || running}
          />
        </label>
      </div>

      <div className={styles.testRow}>
        <button
          type="submit"
          className={styles.btnSecondary}
          disabled={busy || running || !form.host.trim() || (config?.is_configured && !connectionDirty)}
        >
          <MIcon name="key" size={18} />
          {savingConnection ? t("SetupPage.saving") : t("SetupPage.gatewaySaveConnection")}
        </button>
        <button
          type="button"
          className={styles.btnSecondary}
          onClick={handleTest}
          disabled={busy || running || !config?.is_configured || connectionDirty}
        >
          <MIcon name={testing ? "sync" : "network_check"} size={18} spin={testing} />
          {testing ? t("SetupPage.testing") : t("SetupPage.testConnection")}
        </button>
        {config?.is_configured && connectionDirty && (
          <span className={styles.hintWarn}>{t("SetupPage.gatewaySaveFirst")}</span>
        )}
      </div>

      {config?.public_key && (
        <>
          <div className={styles.subHead}>
            <h3 className={styles.subTitle}>{t("SetupPage.gatewayPublicKeyTitle")}</h3>
            <button type="button" className={styles.btnSecondary} onClick={copyPublicKey}>
              <MIcon name="content_copy" size={18} />
              {ts("GatewayPage.copy")}
            </button>
          </div>
          <pre className={styles.preBlock}>{config.public_key}</pre>
          {!connected && <Notice>{t("SetupPage.gatewayPublicKeyHint")}</Notice>}
        </>
      )}

      {testResult && !connectionDirty && (testResult.message || !testResult.success) && (
        <div className={`${styles.testResult} ${testResult.success ? styles.testResult_ok : styles.testResult_fail}`}>
          <MIcon name={testResult.success ? "check_circle" : "error"} size={20} />
          <div><strong>{testResult.message || t("SetupPage.testFailed")}</strong></div>
        </div>
      )}

      {connected && install && installForm && (
        <>
          <div className={styles.subHead}>
            <h3 className={styles.subTitle}>{t("SetupPage.gatewayInstallTitle")}</h3>
            <span className={styles.nodeChip}>
              <MIcon name={STATE_ICONS[install.state] ?? STATE_ICONS.idle} size={14} spin={running} />
              {ts(`GatewayPage.installState_${install.state}`)}
              {install.state === "failed" && install.exit_code !== null && <small>exit {install.exit_code}</small>}
            </span>
          </div>
          <p className={styles.sectionDesc}>{t("SetupPage.gatewayInstallDesc")}</p>

          <div className={styles.nodeChips}>
            {COMPONENTS.map(([key, label]) => (
              <span key={key} className={styles.nodeChip}>
                <MIcon name={install.components?.[key] ? "check_circle" : "remove_circle_outline"} size={14} />
                {label}
              </span>
            ))}
          </div>

          {!install.root_access && <p className={styles.error}>{ts("GatewayPage.installRootRequired")}</p>}

          <div className={styles.formGrid}>
            <InterfaceSelect
              label={ts("GatewayPage.installIngressInterface")}
              value={installForm.ingress_interface}
              interfaces={install.interfaces ?? []}
              onChange={(value) => setInstallField("ingress_interface", value)}
              disabled={running || starting}
              noIpv4Text={ts("GatewayPage.installNoIpv4")}
            />
            <InterfaceSelect
              label={ts("GatewayPage.installVmInterface")}
              value={installForm.vm_interface}
              interfaces={install.interfaces ?? []}
              onChange={handleVmInterfaceChange}
              disabled={running || starting}
              noIpv4Text={ts("GatewayPage.installNoIpv4")}
            />
            <label className={styles.field}>
              <span>{ts("GatewayPage.installSnatAddress")}</span>
              <input
                value={installForm.snat_address}
                onChange={(e) => setInstallField("snat_address", e.target.value)}
                placeholder="10.10.0.2"
                disabled={running || starting}
              />
            </label>
            <label className={styles.field}>
              <span>{ts("GatewayPage.installListenPort")}</span>
              <input
                type="number"
                min={1}
                max={65535}
                value={installForm.listen_port}
                onChange={(e) => setInstallField("listen_port", e.target.value)}
                disabled={running || starting}
              />
            </label>
            {/* 起—迄是同一個欄位：一個標籤、一組成對控制項 */}
            <div className={styles.field}>
              <span>{t("SetupPage.gatewayForwardPorts")}</span>
              <div className={styles.portPair}>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  aria-label={ts("GatewayPage.installForwardStart")}
                  value={installForm.forward_port_start}
                  onChange={(e) => setInstallField("forward_port_start", e.target.value)}
                  disabled={running || starting}
                />
                <span aria-hidden="true">–</span>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  aria-label={ts("GatewayPage.installForwardEnd")}
                  value={installForm.forward_port_end}
                  onChange={(e) => setInstallField("forward_port_end", e.target.value)}
                  disabled={running || starting}
                />
              </div>
            </div>
            <label className={styles.field}>
              <span>{ts("GatewayPage.installMonitoringSources")}</span>
              <input
                value={installForm.monitoring_allow_from}
                onChange={(e) => setInstallField("monitoring_allow_from", e.target.value)}
                placeholder="192.168.100.20, 10.0.0.0/24"
                disabled={running || starting}
              />
            </label>
          </div>

          {installFormError && <p className={styles.error}>{ts(`GatewayPage.${installFormError}`)}</p>}

          <div className={styles.testRow}>
            <button
              type="button"
              className={styles.btnSecondary}
              onClick={handleInstall}
              disabled={busy || running || !install.root_access || Boolean(installFormError)}
            >
              <MIcon name={running ? "autorenew" : "install_desktop"} size={18} spin={running || starting} />
              {running || starting
                ? ts("GatewayPage.installRunning")
                : installed ? ts("GatewayPage.installReinstall") : ts("GatewayPage.installStart")}
            </button>
          </div>

          {install.log && (
            <pre ref={logRef} className={`${styles.preBlock} ${styles.preLog}`}>{install.log}</pre>
          )}
        </>
      )}

      {error && <p className={styles.error}>{error}</p>}

      <div className={styles.actions}>
        <button type="button" className={styles.btnSecondary} onClick={onBack} disabled={busy}>
          <MIcon name="arrow_back" size={18} />
          {t("SetupPage.back")}
        </button>
        <div className={styles.actionGroup}>
          {!installed && (
            <button type="button" className={styles.btnSecondary} onClick={onSkip} disabled={busy}>
              {t("SetupPage.skip")}
            </button>
          )}
          <button type="button" className={styles.btnPrimary} onClick={onNext} disabled={busy || !installed}>
            {t("SetupPage.next")}
            <MIcon name="arrow_forward" size={18} />
          </button>
        </div>
      </div>
    </form>
  );
}
