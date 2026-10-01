import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./GatewayPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import { GatewayService } from "../../../services/gateway";
import {
  isPlatformFormDirty,
  toPlatformForm,
  toPlatformPayload,
  toUpstreamTarget,
  validatePlatformForm,
} from "./platformEntryForm";

function formatDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString();
}

/* 徽章：啟用且 Gateway 上的內容對得上才是綠的；狀態還沒讀回來時先照設定顯示 */
function entryBadge(config, status) {
  if (status && !status.applied) return { tone: styles.badge_warning, icon: "sync_problem", key: "platformBadgeDrift" };
  if (config.enabled) return { tone: styles.badge_success, icon: "check_circle", key: "platformBadgeEnabled" };
  return { tone: styles.badge_muted, icon: "radio_button_unchecked", key: "platformBadgeDisabled" };
}

/* ── Gateway 套用狀態卡 ─────────────────────────────── */
function PlatformStatusCard({ config, status, error, loading, onRefresh }) {
  const { t } = useTranslation("system");
  /* 只有管理員自己就是從平台入口的網域進來時，「後端看到的來源」才判斷得出對不對 */
  const viaPlatform = Boolean(config.domain) && window.location.hostname === config.domain;

  const warnings = [];
  if (status) {
    if (!status.applied) warnings.push(t("GatewayPage.platformWarnDrift"));
    if (status.certificate_ready === false) warnings.push(t("GatewayPage.platformWarnCert"));
    if (status.upstream_reachable === false) {
      warnings.push(t("GatewayPage.platformWarnUpstream", { detail: status.upstream_detail ?? "" }));
    }
    if (viaPlatform && config.gateway_host && status.observed_client_ip === config.gateway_host) {
      warnings.push(t("GatewayPage.platformWarnTrustedProxy", { ip: config.gateway_host }));
    } else if (viaPlatform && window.location.protocol === "https:" && status.observed_scheme !== "https") {
      warnings.push(t("GatewayPage.platformWarnScheme"));
    }
  }

  let certificateText = "—";
  if (status?.applied_https === false) certificateText = t("GatewayPage.platformStatusCertNone");
  else if (status?.certificate_ready === false) certificateText = t("GatewayPage.platformStatusCertFallback");
  else if (status?.certificate) {
    const date = formatDate(status.certificate_expires_at);
    certificateText = date
      ? t("GatewayPage.platformStatusCertReady", { name: status.certificate, date })
      : status.certificate;
  }

  let upstreamText = "—";
  if (status?.upstream_reachable === true) upstreamText = t("GatewayPage.platformStatusReachable");
  else if (status?.upstream_reachable === false) upstreamText = t("GatewayPage.platformStatusUnreachable");

  const details = status ? [
    [t("GatewayPage.platformStatusDomain"), status.applied_domain || t("GatewayPage.platformStatusNotApplied")],
    [t("GatewayPage.platformStatusUpstream"), status.applied_upstream || "—"],
    [t("GatewayPage.platformStatusCertificate"), certificateText],
    [t("GatewayPage.platformStatusUpstreamCheck"), upstreamText],
    [t("GatewayPage.platformStatusClientIp"), status.observed_client_ip || "—"],
    [t("GatewayPage.platformStatusScheme"), status.observed_scheme || "—"],
  ] : [];

  return (
    <div className={styles.card}>
      <div className={styles.cardHead}>
        <h2 className={styles.cardTitle}>{t("GatewayPage.platformStatusTitle")}</h2>
        <button type="button" className={styles.btnSecondary} onClick={onRefresh} disabled={loading}>
          <MIcon name="refresh" size={16} spin={loading} />
          {t("GatewayPage.installRefresh")}
        </button>
      </div>

      {!status && loading && <LoadingState />}

      {!status && !loading && error && (
        <div className={styles.warningNote}>
          <MIcon name="warning" size={18} />
          <div className={styles.noteText}>
            <strong>{t("GatewayPage.platformStatusError")}</strong>
            <span>{error}</span>
          </div>
        </div>
      )}

      {status && (
        <>
          <dl className={styles.detailGrid}>
            {details.map(([label, value]) => (
              <div className={styles.detailItem} key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          {warnings.map((text) => (
            <div className={styles.warningNote} key={text}>
              <MIcon name="warning" size={18} />
              {text}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

/* ── 啟用後還要手動完成的事 ─────────────────────────── */
function PlatformTodoCard({ gatewayHost }) {
  const { t } = useTranslation("system");
  const items = [
    ["dns", t("GatewayPage.platformTodoDnsTitle"), t("GatewayPage.platformTodoDns")],
    ["verified_user", t("GatewayPage.platformTodoProxyTitle"), t("GatewayPage.platformTodoProxy", { host: gatewayHost || "<Gateway IP>" })],
    ["link", t("GatewayPage.platformTodoUrlsTitle"), t("GatewayPage.platformTodoUrls")],
    ["lan", t("GatewayPage.platformTodoFallbackTitle"), t("GatewayPage.platformTodoFallback")],
  ];
  return (
    <div className={styles.card}>
      <div className={styles.cardHead}>
        <h2 className={styles.cardTitle}>{t("GatewayPage.platformTodoTitle")}</h2>
      </div>
      <ul className={styles.todoList}>
        {items.map(([icon, title, body]) => (
          <li key={icon}>
            <MIcon name={icon} size={20} />
            <div className={styles.noteText}>
              <strong>{title}</strong>
              <span>{body}</span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ── 平台入口 Tab ───────────────────────────────────── */
export default function GatewayPlatformEntryTab({ gatewayReady, onGoToConnection, onDirtyChange }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const [config, setConfig] = useState(null);
  const [form, setForm] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const statusInFlightRef = useRef(false);

  const refreshStatus = useCallback(async () => {
    if (statusInFlightRef.current) return;
    statusInFlightRef.current = true;
    setStatusLoading(true);
    try {
      setStatus(await GatewayService.getPlatformEntryStatus());
      setStatusError(null);
    } catch (err) {
      setStatus(null);
      setStatusError(err?.message ?? t("Error.generic", { ns: "common" }));
    } finally {
      statusInFlightRef.current = false;
      setStatusLoading(false);
    }
  }, [t]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await GatewayService.getPlatformEntry();
      setConfig(next);
      setForm(toPlatformForm(next));
      setLoadFailed(false);
      if (next.gateway_ready) refreshStatus();
    } catch (err) {
      setLoadFailed(true);
      toast.error(err?.message ?? t("GatewayPage.toastPlatformLoadFailed"));
    } finally {
      setLoading(false);
    }
  }, [refreshStatus, t, toast]);

  useEffect(() => {
    if (gatewayReady) load();
    else setLoading(false);
  }, [gatewayReady, load]);

  const dirty = Boolean(form && config && isPlatformFormDirty(form, config));

  // 把 dirty 回報給 GatewayPage，切換分頁前才會跳「尚未儲存」的確認
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  function setField(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
    // 上游欄位一改，之前的測試結果就不算數
    if (name === "upstream_host" || name === "upstream_port") setTestResult(null);
  }

  async function handleTest() {
    const target = toUpstreamTarget(form);
    if (!target) return;
    setTesting(true);
    try {
      setTestResult(await GatewayService.testPlatformEntryUpstream(target));
    } catch (err) {
      setTestResult({ reachable: false, detail: err?.message ?? t("GatewayPage.platformTestFailed") });
    } finally {
      setTesting(false);
    }
  }

  async function handleSave(e) {
    e.preventDefault();
    if (validatePlatformForm(form)) return;
    const payload = toPlatformPayload(form);
    const disabling = config.enabled && !payload.enabled;
    if (disabling) {
      const ok = await confirm({
        title: t("GatewayPage.platformDisableConfirmTitle"),
        message: t("GatewayPage.platformDisableConfirmMessage", { domain: config.domain }),
        confirmText: t("GatewayPage.platformDisableConfirmButton"),
        danger: true,
      });
      if (!ok) return;
    }
    setSaving(true);
    try {
      const next = await GatewayService.updatePlatformEntry(payload);
      setConfig(next);
      setForm(toPlatformForm(next));
      setTestResult(null);
      if (disabling) toast.success(t("GatewayPage.toastPlatformDisabled"));
      else if (next.enabled) toast.success(t("GatewayPage.toastPlatformSaved"));
      else toast.success(t("GatewayPage.toastPlatformSavedDraft"));
      refreshStatus();
    } catch (err) {
      toast.error(err?.message ?? t("GatewayPage.toastPlatformSaveFailed"));
    } finally {
      setSaving(false);
    }
  }

  if (!gatewayReady) {
    return <EmptyState icon="public" title={t("GatewayPage.emptyNotConfigured")} action={<button type="button" className={styles.btnPrimary} onClick={onGoToConnection}><MIcon name="settings_ethernet" size={16} />{t("GatewayPage.goToConnection")}</button>} />;
  }

  if (loading && !config) {
    return <LoadingState text={t("GatewayPage.platformLoading")} />;
  }

  if (!config || !form) {
    return loadFailed ? <ErrorState onRetry={load} /> : null;
  }

  const formError = validatePlatformForm(form);
  const busy = saving || testing;
  const badge = entryBadge(config, status);
  const needsCloudflare = form.enabled && form.enable_https && !config.cloudflare_ready;
  // 表單沒改但 Gateway 上的內容跑掉了（例如重裝過）：仍要能按一次重新套用
  const drifted = Boolean(status && !status.applied);
  const canSave = !busy && !formError && (dirty || drifted);

  return (
    <div className={styles.panelStack}>
      <form className={styles.card} onSubmit={handleSave}>
        <div className={styles.cardHead}>
          <div className={styles.statusRow}>
            <h2 className={styles.cardTitle}>{t("GatewayPage.platformTitle")}</h2>
            <span className={`${styles.badge} ${badge.tone}`}>
              <MIcon name={badge.icon} size={13} />
              {t(`GatewayPage.${badge.key}`)}
            </span>
          </div>
          <div className={styles.cardHeadActions}>
            <button
              type="button"
              className={styles.btnSecondary}
              onClick={handleTest}
              disabled={busy || !toUpstreamTarget(form)}
            >
              <MIcon name={testing ? "sync" : "network_check"} size={16} spin={testing} />
              {testing ? t("GatewayPage.platformTesting") : t("GatewayPage.platformTestUpstream")}
            </button>
            <button type="submit" className={styles.btnPrimary} disabled={!canSave}>
              {saving ? t("GatewayPage.platformSaving") : t("GatewayPage.platformSave")}
            </button>
          </div>
        </div>

        <label className={styles.checkRow}>
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => setField("enabled", e.target.checked)}
            disabled={saving}
          />
          <span>{t("GatewayPage.platformEnable")}</span>
        </label>

        <div className={styles.installGrid}>
          <label className={styles.field}>
            <span>{t("GatewayPage.platformDomain")}{form.enabled ? " *" : ""}</span>
            <input
              value={form.domain}
              onChange={(e) => setField("domain", e.target.value)}
              placeholder="skylab.example.com"
              spellCheck={false}
              disabled={saving}
            />
          </label>
          <label className={styles.field}>
            <span>{t("GatewayPage.platformUpstreamHost")}{form.enabled ? " *" : ""}</span>
            <input
              value={form.upstream_host}
              onChange={(e) => setField("upstream_host", e.target.value)}
              placeholder="192.168.100.20"
              spellCheck={false}
              disabled={saving}
            />
          </label>
          <label className={styles.field}>
            <span>{t("GatewayPage.platformUpstreamPort")}</span>
            <input
              type="number"
              min={1}
              max={65535}
              value={form.upstream_port}
              onChange={(e) => setField("upstream_port", e.target.value)}
              disabled={saving}
            />
          </label>
        </div>

        <label className={styles.checkRow}>
          <input
            type="checkbox"
            checked={form.enable_https}
            onChange={(e) => setField("enable_https", e.target.checked)}
            disabled={saving}
          />
          <span>{t("GatewayPage.platformHttps")}</span>
        </label>

        {formError && (
          <div className={styles.warningNote}>
            <MIcon name="error_outline" size={18} />
            {t(`GatewayPage.${formError}`)}
          </div>
        )}

        {needsCloudflare && (
          <div className={styles.warningNote}>
            <MIcon name="warning" size={18} />
            {t("GatewayPage.platformCloudflareRequired")}
          </div>
        )}

        {testResult && (
          <div className={testResult.reachable ? styles.successNote : styles.warningNote}>
            <MIcon name={testResult.reachable ? "check_circle" : "error"} size={18} />
            {testResult.detail}
          </div>
        )}

        <div className={styles.securityNote}>
          <MIcon name="info" size={20} />
          <div>
            <strong>{t("GatewayPage.platformImpactTitle")}</strong>
            <span>{t("GatewayPage.platformImpactHint")}</span>
          </div>
        </div>
      </form>

      <PlatformStatusCard
        config={config}
        status={status}
        error={statusError}
        loading={statusLoading}
        onRefresh={refreshStatus}
      />

      <PlatformTodoCard gatewayHost={config.gateway_host} />
    </div>
  );
}
