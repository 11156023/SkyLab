import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./GatewayPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import { useQueryErrorEffect, useQueryView } from "../../../hooks/useQueryLoadState";
import { GatewayService } from "../../../services/gateway";
import {
  useGatewayCacheUpdater,
  usePlatformEntryQuery,
  usePlatformEntryStatusQuery,
} from "../../../queries/gateway";
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
    else if (status.certificate_matches_domain === false) {
      warnings.push(t("GatewayPage.platformWarnCertDomain", { domain: status.applied_domain ?? "" }));
    }
    if (status.upstream_reachable === false) {
      warnings.push(t("GatewayPage.platformWarnUpstream", { detail: status.upstream_detail ?? "" }));
    }
    if (status.dns_detail) warnings.push(status.dns_detail);
    else if (status.dns_record_ok === false) warnings.push(t("GatewayPage.platformWarnDns"));
    if (viaPlatform && config.gateway_host && status.observed_client_ip === config.gateway_host) {
      warnings.push(t("GatewayPage.platformWarnTrustedProxy", { ip: config.gateway_host }));
    } else if (viaPlatform && window.location.protocol === "https:" && status.observed_scheme !== "https") {
      warnings.push(t("GatewayPage.platformWarnScheme"));
    }
  }

  let certificateText = "—";
  if (status?.applied_https === false) certificateText = t("GatewayPage.platformStatusCertNone");
  else if (!status?.certificate && status?.applied_https) certificateText = t("GatewayPage.platformStatusCertFallback");
  else if (status?.certificate) {
    const date = formatDate(status.certificate_expires_at);
    certificateText = date
      ? t("GatewayPage.platformStatusCertReady", { name: status.certificate, date })
      : status.certificate;
  }

  let upstreamText = "—";
  if (status?.upstream_reachable === true) upstreamText = t("GatewayPage.platformStatusReachable");
  else if (status?.upstream_reachable === false) upstreamText = t("GatewayPage.platformStatusUnreachable");

  let dnsText = t("GatewayPage.platformStatusDnsManual");
  if (config.dns_managed) dnsText = status?.dns_record || "—";

  const details = status ? [
    [t("GatewayPage.platformStatusDomain"), status.applied_domain || t("GatewayPage.platformStatusNotApplied")],
    [t("GatewayPage.platformStatusUpstream"), status.applied_upstream || "—"],
    [t("GatewayPage.platformStatusDns"), dnsText],
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
function PlatformTodoCard({ gatewayHost, domain, dnsManaged, dnsProxied }) {
  const { t } = useTranslation("system");
  /* 網域在 Cloudflare 管理的 zone 內時，儲存就已經把 DNS 指過去了 */
  const managedKey = dnsProxied ? "GatewayPage.platformTodoDnsManagedProxied" : "GatewayPage.platformTodoDnsManaged";
  const dnsItem = dnsManaged
    ? ["dns", t("GatewayPage.platformTodoDnsManagedTitle"), t(managedKey, { domain })]
    : ["dns", t("GatewayPage.platformTodoDnsTitle"), t("GatewayPage.platformTodoDns")];
  const items = [
    dnsItem,
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
export default function GatewayPlatformEntryTab({ gatewayReady, onGoToConnection, onGoToCertificate, onDirtyChange }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const gatewayCache = useGatewayCacheUpdater();

  /* 平台入口設定走 TanStack Query（不輪詢、不因切回分頁重抓）；表單連同它所根據的那份設定一起記：
     沒有未存的修改時，重新抓回來的設定才換進表單（換分頁回來時先顯示快取、背景再更新） */
  const entryQuery = usePlatformEntryQuery({ enabled: gatewayReady });
  const entry = entryQuery.data;
  const [editor, setEditor] = useState(() => (
    entry === undefined ? { config: null, form: null } : { config: entry, form: toPlatformForm(entry) }
  ));
  const { config, form } = editor;
  useEffect(() => {
    if (entry === undefined) return;
    setEditor((prev) => (
      prev.form && prev.config && isPlatformFormDirty(prev.form, prev.config)
        ? prev
        : { config: entry, form: toPlatformForm(entry) }
    ));
  }, [entry]);
  /* 錯誤畫面在重抓期間維持、不閃回載入中，按重試才回到載入中（見 useQueryView） */
  const entryView = useQueryView(entryQuery);
  const loading = entryView.loading;
  const loadFailed = Boolean(entryView.error);
  /* 每次載入失敗提示一次：重抓時 error 會先清空再變回來，不能拿 error 物件當觸發條件 */
  useQueryErrorEffect(entryQuery, (err) => {
    toast.error(err?.message ?? t("GatewayPage.toastPlatformLoadFailed"));
  });

  /* Gateway 上實際套用的狀態：設定讀到、且 Gateway 就緒才查。檢查進行中又被要求刷新（例如儲存後）時
     不能丟掉，否則卡片停在儲存前的狀態：進行中那次的結果作廢，等它跑完再查一次（連按多次也只多跑一次） */
  const statusQuery = usePlatformEntryStatusQuery({ enabled: gatewayReady && Boolean(entry?.gateway_ready) });
  const status = statusQuery.isError ? null : statusQuery.data ?? null;
  const statusError = statusQuery.isError ? statusQuery.error?.message ?? t("Error.generic", { ns: "common" }) : null;
  const statusLoading = statusQuery.isFetching;
  const refreshStatus = () => gatewayCache.refreshPlatformEntryStatus();

  const dirty = Boolean(form && config && isPlatformFormDirty(form, config));

  // 把 dirty 回報給 GatewayPage，切換分頁前才會跳「尚未儲存」的確認
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  function setField(name, value) {
    setEditor((prev) => ({ ...prev, form: { ...prev.form, [name]: value } }));
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
        message: config.dns_managed
          ? `${t("GatewayPage.platformDisableConfirmMessage", { domain: config.domain })} ${t("GatewayPage.platformDisableConfirmDns")}`
          : t("GatewayPage.platformDisableConfirmMessage", { domain: config.domain }),
        confirmText: t("GatewayPage.platformDisableConfirmButton"),
        danger: true,
      });
      if (!ok) return;
    }
    setSaving(true);
    try {
      const next = await GatewayService.updatePlatformEntry(payload);
      setEditor({ config: next, form: toPlatformForm(next) });
      gatewayCache.setPlatformEntry(next);
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
    return loadFailed ? <ErrorState onRetry={() => entryView.retry()} /> : null;
  }

  const formError = validatePlatformForm(form);
  const busy = saving || testing;
  const badge = entryBadge(config, status);
  const needsCertificate = form.enabled && form.enable_https && !config.certificate_configured;
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

        <label className={styles.checkRow}>
          <input
            type="checkbox"
            checked={form.dns_proxied}
            onChange={(e) => setField("dns_proxied", e.target.checked)}
            disabled={saving}
          />
          <span>{t("GatewayPage.platformCloudflareProxy")}</span>
        </label>

        {form.dns_proxied && (
          <div className={styles.securityNote}>
            <MIcon name="cloud" size={20} />
            <div>
              <span>{t("GatewayPage.platformCloudflareProxyHint")}</span>
            </div>
          </div>
        )}

        {formError && (
          <div className={styles.warningNote}>
            <MIcon name="error_outline" size={18} />
            {t(`GatewayPage.${formError}`)}
          </div>
        )}

        {needsCertificate && (
          <div className={styles.warningNote}>
            <MIcon name="warning" size={18} />
            <div className={styles.noteText}>
              <span>{t("GatewayPage.platformCertificateRequired")}</span>
              {onGoToCertificate && (
                <button type="button" className={styles.btnSecondary} onClick={onGoToCertificate}>
                  <MIcon name="lock" size={16} />
                  {t("GatewayPage.goToCertificate")}
                </button>
              )}
            </div>
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

      <PlatformTodoCard gatewayHost={config.gateway_host} domain={config.domain} dnsManaged={config.dns_managed} dnsProxied={config.dns_proxied} />
    </div>
  );
}
