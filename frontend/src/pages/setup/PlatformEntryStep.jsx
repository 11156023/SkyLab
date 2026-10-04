/**
 * 初始化精靈步驟：平台入口（可略過）。
 *
 * 讓 SkyLab 主系統自己也經 Gateway 的 nginx 對外。要先完成 Gateway 步驟；
 * 表單驗證與送出內容和閘道頁的「平台入口」分頁共用（platformEntryForm.js）。
 * 系統不簽發 HTTPS 憑證：要開 HTTPS 而 Gateway 還沒設定憑證時，在這裡順便填
 * 管理員自備的憑證／私鑰路徑（之後在閘道頁「HTTPS 憑證」分頁管理）。
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../../components/MIcon";
import { LoadingSpinner } from "../../components/LoadingState/LoadingState";
import { useToast } from "../../hooks/useToast";
import { SetupService } from "../../services/setup";
import {
  SUGGESTED_CERT_PATH,
  SUGGESTED_KEY_PATH,
  toCertificatePayload,
  validateCertificateForm,
} from "../system/gateway/certificateForm";
import {
  isPlatformFormDirty,
  isValidUpstreamHost,
  toPlatformForm,
  toPlatformPayload,
  toUpstreamTarget,
  validatePlatformForm,
} from "../system/gateway/platformEntryForm";
import { Notice } from "./wizardParts";
import styles from "./SetupPage.module.scss";

const IPV4_PATTERN = /^(\d{1,3}\.){3}\d{1,3}$/;

/** 還沒填過上游時的預設值：管理員現在就是直接連著部署機在跑精靈，
 *  網址列是 IP 的話，那個位址多半就是 Gateway 要轉送的目標 */
function withDetectedUpstream(form) {
  if (form.upstream_host) return form;
  const { hostname, port, protocol } = window.location;
  // 127.x 是管理員自己這台的 loopback，Gateway 連不到，不拿來當預設值
  if (!IPV4_PATTERN.test(hostname) || hostname.startsWith("127.") || !isValidUpstreamHost(hostname)) return form;
  return {
    ...form,
    upstream_host: hostname,
    upstream_port: port || (protocol === "https:" ? "443" : "80"),
  };
}

export default function PlatformEntryStep({ gatewayReady, onSaved, onSkip, onBack, onNext }) {
  const { t } = useTranslation("login");
  /* 欄位名稱與錯誤訊息沿用閘道頁那一份，兩邊用語才會一致 */
  const { t: ts } = useTranslation("system");
  const toast = useToast();
  const [loading, setLoading] = useState(gatewayReady);
  const [config, setConfig] = useState(null);
  const [form, setForm] = useState(null);
  const [cert, setCert] = useState({ ssl_certificate_path: "", ssl_certificate_key_path: "" });
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!gatewayReady) return undefined;
    let cancelled = false;
    SetupService.getPlatformEntry()
      .then((current) => {
        if (cancelled) return;
        setConfig(current);
        // 精靈裡存檔就是要啟用，不提供「存成草稿」
        setForm(withDetectedUpstream({ ...toPlatformForm(current), enabled: true }));
      })
      .catch((err) => {
        if (!cancelled) setError(err?.message ?? t("SetupPage.platformLoadFailed"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [gatewayReady, t]);

  function setField(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
    if (name === "upstream_host" || name === "upstream_port") setTestResult(null);
  }

  async function handleTest() {
    const target = toUpstreamTarget(form);
    if (!target) return;
    setError("");
    setTesting(true);
    try {
      setTestResult(await SetupService.testPlatformEntryUpstream(target));
    } catch (err) {
      setTestResult({ reachable: false, detail: err?.message ?? ts("GatewayPage.platformTestFailed") });
    } finally {
      setTesting(false);
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    const formError = validatePlatformForm(form);
    if (formError) {
      setError(ts(`GatewayPage.${formError}`));
      return;
    }
    const needsCert = form.enable_https && !config.certificate_configured;
    if (needsCert) {
      const certPayload = toCertificatePayload(cert);
      if (!certPayload.ssl_certificate_path || !certPayload.ssl_certificate_key_path) {
        setError(t("SetupPage.platformErrorCertificate"));
        return;
      }
      const certError = validateCertificateForm(cert);
      if (certError) {
        setError(ts(`GatewayPage.${certError}`));
        return;
      }
    }
    setSaving(true);
    try {
      const payload = { ...toPlatformPayload(form), ...(needsCert ? toCertificatePayload(cert) : {}) };
      const result = await SetupService.savePlatformEntry(payload);
      setConfig(result);
      toast.success(t("SetupPage.platformSaved"));
      onSaved(result);
    } catch (err) {
      setError(err?.message ?? t("SetupPage.platformSaveFailed"));
    } finally {
      setSaving(false);
    }
  }

  if (!gatewayReady) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>{t("SetupPage.platformTitle")}</h2>
        <Notice>{t("SetupPage.platformNeedsGateway")}</Notice>
        <div className={styles.actions}>
          <button type="button" className={styles.btnSecondary} onClick={onBack}>
            <MIcon name="arrow_back" size={18} />
            {t("SetupPage.back")}
          </button>
          <button type="button" className={styles.btnPrimary} onClick={onSkip}>
            {t("SetupPage.next")}
            <MIcon name="arrow_forward" size={18} />
          </button>
        </div>
      </section>
    );
  }

  if (loading || !form) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>{t("SetupPage.platformTitle")}</h2>
        {loading
          ? <div className={styles.center}><LoadingSpinner size={32} /></div>
          : <p className={styles.error}>{error}</p>}
        <div className={styles.actions}>
          <button type="button" className={styles.btnSecondary} onClick={onBack}>
            <MIcon name="arrow_back" size={18} />
            {t("SetupPage.back")}
          </button>
          <button type="button" className={styles.btnSecondary} onClick={onSkip}>
            {t("SetupPage.skip")}
          </button>
        </div>
      </section>
    );
  }

  const busy = saving || testing;
  const needsCert = form.enable_https && !config.certificate_configured;
  // 已經啟用而且表單沒動過：這一步等於做完了，主要按鈕改成「下一步」
  const alreadyApplied = config.enabled && !isPlatformFormDirty(form, config);

  return (
    <form className={styles.section} onSubmit={handleSubmit}>
      <h2 className={styles.sectionTitle}>{t("SetupPage.platformTitle")}</h2>
      <p className={styles.sectionDesc}>{t("SetupPage.platformDesc")}</p>

      {alreadyApplied && (
        <Notice icon="check_circle" tone="success">
          {t("SetupPage.platformDoneNotice")} <code className={styles.code}>{config.domain}</code>
        </Notice>
      )}

      <div className={styles.formGrid}>
        <label className={`${styles.field} ${styles.fieldWide}`}>
          <span>{ts("GatewayPage.platformDomain")} *</span>
          <input
            value={form.domain}
            onChange={(e) => setField("domain", e.target.value)}
            placeholder="skylab.example.com"
            spellCheck={false}
            disabled={busy}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{ts("GatewayPage.platformUpstreamHost")} *</span>
          <input
            value={form.upstream_host}
            onChange={(e) => setField("upstream_host", e.target.value)}
            placeholder="192.168.100.20"
            spellCheck={false}
            disabled={busy}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{ts("GatewayPage.platformUpstreamPort")}</span>
          <input
            type="number"
            min={1}
            max={65535}
            value={form.upstream_port}
            onChange={(e) => setField("upstream_port", e.target.value)}
            disabled={busy}
          />
        </label>
      </div>

      <label className={styles.checkRow}>
        <input
          type="checkbox"
          checked={form.enable_https}
          onChange={(e) => setField("enable_https", e.target.checked)}
          disabled={busy}
        />
        <span>{ts("GatewayPage.platformHttps")}</span>
      </label>

      {needsCert && (
        <div className={styles.formGrid}>
          <label className={`${styles.field} ${styles.fieldWide}`}>
            <span>{ts("GatewayPage.certPath")} *</span>
            <input
              value={cert.ssl_certificate_path}
              onChange={(e) => setCert((prev) => ({ ...prev, ssl_certificate_path: e.target.value }))}
              placeholder={SUGGESTED_CERT_PATH}
              spellCheck={false}
              disabled={busy}
            />
          </label>
          <label className={`${styles.field} ${styles.fieldWide}`}>
            <span>{ts("GatewayPage.certKeyPath")} *</span>
            <input
              value={cert.ssl_certificate_key_path}
              onChange={(e) => setCert((prev) => ({ ...prev, ssl_certificate_key_path: e.target.value }))}
              placeholder={SUGGESTED_KEY_PATH}
              spellCheck={false}
              disabled={busy}
            />
            <small className={styles.fieldHint}>{t("SetupPage.platformCertificateHint")}</small>
          </label>
        </div>
      )}

      <div className={styles.testRow}>
        <button
          type="button"
          className={styles.btnSecondary}
          onClick={handleTest}
          disabled={busy || !toUpstreamTarget(form)}
        >
          <MIcon name={testing ? "sync" : "network_check"} size={18} spin={testing} />
          {testing ? ts("GatewayPage.platformTesting") : ts("GatewayPage.platformTestUpstream")}
        </button>
      </div>

      {testResult && (
        <div className={`${styles.testResult} ${testResult.reachable ? styles.testResult_ok : styles.testResult_fail}`}>
          <MIcon name={testResult.reachable ? "check_circle" : "error"} size={20} />
          <div><strong>{testResult.detail}</strong></div>
        </div>
      )}

      <Notice>
        {t("SetupPage.platformAfterNotice", { host: config.gateway_host || "<Gateway IP>" })}
      </Notice>

      {error && <p className={styles.error}>{error}</p>}

      <div className={styles.actions}>
        <button type="button" className={styles.btnSecondary} onClick={onBack} disabled={busy}>
          <MIcon name="arrow_back" size={18} />
          {t("SetupPage.back")}
        </button>
        <div className={styles.actionGroup}>
          {alreadyApplied ? (
            <button type="button" className={styles.btnPrimary} onClick={onNext}>
              {t("SetupPage.next")}
              <MIcon name="arrow_forward" size={18} />
            </button>
          ) : (
            <>
              <button type="button" className={styles.btnSecondary} onClick={onSkip} disabled={busy}>
                {t("SetupPage.skip")}
              </button>
              <button type="submit" className={styles.btnPrimary} disabled={busy}>
                {saving ? t("SetupPage.saving") : t("SetupPage.saveAndNext")}
                {!saving && <MIcon name="arrow_forward" size={18} />}
              </button>
            </>
          )}
        </div>
      </div>
    </form>
  );
}
