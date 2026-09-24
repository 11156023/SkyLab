/**
 * SetupPage — 首次安裝初始化精靈（/setup）。
 *
 * 免登入頁面，只在後端 `system_setup.completed` 為 false 時有作用：
 *   開始 → 管理員 → PVE 連線（可略過）→ IP 網段（可略過）→ 完成並登入。
 * 每一步存檔都直接打 /api/v1/setup/*，重新整理後會依 status.steps 顯示「已設定」讓人直接往下走。
 * 完成後呼叫 markSetupCompleted()，登入頁就不會再把人導回來。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../components/MIcon";
import PasswordInput from "../../components/PasswordInput/PasswordInput";
import { LoadingSpinner } from "../../components/LoadingState/LoadingState";
import { useAuth } from "../../contexts/AuthContext";
import { useToast } from "../../hooks/useToast";
import { DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, setLanguage } from "../../i18n";
import { SetupService } from "../../services/setup";
import { markSetupCompleted, useSetupStatus } from "./useSetupStatus";
import styles from "./SetupPage.module.scss";

const STEP_ADMIN = 0;
const STEP_PROXMOX = 1;
const STEP_SUBNET = 2;
const STEP_FINISH = 3;

/* 語言用原生名稱顯示，不翻譯 */
const LANG_OPTIONS = [
  { key: "zh-TW", label: "繁體中文" },
  { key: "en", label: "English" },
  { key: "ja", label: "日本語" },
];

const IPV4_PATTERN = "^(\\d{1,3}\\.){3}\\d{1,3}$";
const MIN_PASSWORD_LENGTH = 8;

const EMPTY_ADMIN_FORM = {
  email: "",
  full_name: "",
  password: "",
  confirm: "",
  disable_default_admin: true,
};

const EMPTY_PROXMOX_FORM = {
  name: "",
  host: "",
  port: "8006",
  user: "root@pam",
  password: "",
  verify_ssl: false,
  ca_cert: "",
  api_timeout: "30",
  pool_name: "SkyLab",
  iso_storage: "local",
  data_storage: "local-lvm",
  default_node: "",
};

const EMPTY_SUBNET_FORM = {
  cidr: "",
  gateway: "",
  bridge_name: "vmbr1",
  gateway_vm_ip: "",
  dns_servers: "",
  forward_port_start: "30000",
  forward_port_end: "39999",
  forward_public_host: "",
};

/** 連線欄位的快照：這些改了就要重新測試 */
function connectionSignature(form) {
  return JSON.stringify([
    form.host.trim(),
    form.port,
    form.user.trim(),
    form.password,
    form.verify_ssl,
    form.ca_cert.trim(),
    form.api_timeout,
  ]);
}

function connectionPayload(form) {
  const payload = {
    host: form.host.trim(),
    port: Number(form.port) || 8006,
    user: form.user.trim(),
    password: form.password,
    verify_ssl: Boolean(form.verify_ssl),
    api_timeout: Number(form.api_timeout) || 30,
  };
  if (form.verify_ssl && form.ca_cert.trim()) payload.ca_cert = form.ca_cert.trim();
  return payload;
}

/* ─── 外框 ─────────────────────────────────────────────── */

function PageShell({ children }) {
  return (
    <div className={styles.page}>
      <div className={styles.glow} aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <div className={styles.card}>{children}</div>
    </div>
  );
}

function Stepper({ current, steps }) {
  return (
    <ol className={styles.stepper} aria-label="steps">
      {steps.map((label, index) => {
        const state = index < current ? "done" : index === current ? "active" : "todo";
        return (
          <li key={label} className={`${styles.step} ${styles[`step_${state}`]}`} aria-current={state === "active" ? "step" : undefined}>
            <span className={styles.stepIndex}>
              {state === "done" ? <MIcon name="check" size={16} /> : index + 1}
            </span>
            <span className={styles.stepLabel}>{label}</span>
          </li>
        );
      })}
    </ol>
  );
}

function Notice({ icon = "info", tone = "info", children }) {
  return (
    <div className={`${styles.notice} ${styles[`notice_${tone}`]}`}>
      <MIcon name={icon} size={20} />
      <div>{children}</div>
    </div>
  );
}

/* ─── 歡迎：選語言 ───────────────────────────────────────── */

function LanguageWelcome({ onContinue }) {
  const { t, i18n } = useTranslation("login");
  const current = SUPPORTED_LANGUAGES.includes(i18n.language) ? i18n.language : DEFAULT_LANGUAGE;
  return (
    <div className={styles.welcome}>
      <h1 className={styles.welcomeTitle}>{t("SetupPage.welcomeTitle")}</h1>
      <div className={styles.langList} role="radiogroup" aria-label={t("SetupPage.languageLabel")}>
        {LANG_OPTIONS.map((option) => (
          <button
            key={option.key}
            type="button"
            role="radio"
            aria-checked={current === option.key}
            lang={option.key}
            className={`${styles.langBtn} ${current === option.key ? styles.langBtnActive : ""}`}
            onClick={() => setLanguage(option.key)}
          >
            {option.label}
          </button>
        ))}
      </div>
      <button type="button" className={styles.btnPrimary} onClick={onContinue}>
        {t("SetupPage.continue")}
        <MIcon name="arrow_forward" size={18} />
      </button>
    </div>
  );
}

/* ─── 步驟 1：管理員 ─────────────────────────────────────── */

function AdminStep({ alreadyDone, savedEmail, onSaved, onBack, onNext }) {
  const { t } = useTranslation("login");
  const toast = useToast();
  const [form, setForm] = useState(EMPTY_ADMIN_FORM);
  const [editing, setEditing] = useState(!alreadyDone);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = (name, value) => setForm((prev) => ({ ...prev, [name]: value }));

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    if (form.password.length < MIN_PASSWORD_LENGTH) {
      setError(t("SetupPage.passwordTooShort"));
      return;
    }
    if (form.password !== form.confirm) {
      setError(t("SetupPage.passwordMismatch"));
      return;
    }
    setSaving(true);
    try {
      const result = await SetupService.createAdmin({
        email: form.email.trim(),
        full_name: form.full_name.trim() || null,
        password: form.password,
        disable_default_admin: Boolean(form.disable_default_admin),
      });
      toast.success(result.created ? t("SetupPage.adminSaved") : t("SetupPage.adminTakenOver"));
      onSaved({ email: result.email, password: form.password, result });
    } catch (err) {
      setError(err?.message ?? t("SetupPage.adminSaveFailed"));
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>{t("SetupPage.adminTitle")}</h2>
        <Notice icon="check_circle" tone="success">
          {t("SetupPage.adminDoneNotice")}
          {savedEmail ? <> <code className={styles.code}>{savedEmail}</code></> : null}
        </Notice>
        <div className={styles.actions}>
          <button type="button" className={styles.btnSecondary} onClick={onBack}>
            <MIcon name="arrow_back" size={18} />
            {t("SetupPage.back")}
          </button>
          <div className={styles.actionGroup}>
            <button type="button" className={styles.btnGhost} onClick={() => setEditing(true)}>
              {t("SetupPage.adminReconfigure")}
            </button>
            <button type="button" className={styles.btnPrimary} onClick={onNext}>
              {t("SetupPage.next")}
              <MIcon name="arrow_forward" size={18} />
            </button>
          </div>
        </div>
      </section>
    );
  }

  return (
    <form className={styles.section} onSubmit={handleSubmit}>
      <h2 className={styles.sectionTitle}>{t("SetupPage.adminTitle")}</h2>
      <p className={styles.sectionDesc}>{t("SetupPage.adminDesc")}</p>

      <div className={styles.formGrid}>
        <label className={styles.field}>
          <span>{t("SetupPage.emailLabel")} *</span>
          <input
            type="email"
            autoComplete="username"
            value={form.email}
            onChange={(e) => set("email", e.target.value)}
            placeholder={t("SetupPage.emailPlaceholder")}
            disabled={saving}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{t("SetupPage.fullNameLabel")}</span>
          <input
            value={form.full_name}
            onChange={(e) => set("full_name", e.target.value)}
            placeholder={t("SetupPage.fullNamePlaceholder")}
            disabled={saving}
          />
        </label>
        <label className={styles.field}>
          <span>{t("SetupPage.passwordLabel")} *</span>
          <PasswordInput
            autoComplete="new-password"
            value={form.password}
            onChange={(e) => set("password", e.target.value)}
            placeholder={t("SetupPage.passwordPlaceholder")}
            minLength={MIN_PASSWORD_LENGTH}
            disabled={saving}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{t("SetupPage.confirmPasswordLabel")} *</span>
          <PasswordInput
            autoComplete="new-password"
            value={form.confirm}
            onChange={(e) => set("confirm", e.target.value)}
            placeholder={t("SetupPage.passwordPlaceholder")}
            minLength={MIN_PASSWORD_LENGTH}
            disabled={saving}
            required
          />
        </label>
      </div>

      <label className={styles.checkRow}>
        <input
          type="checkbox"
          checked={Boolean(form.disable_default_admin)}
          onChange={(e) => set("disable_default_admin", e.target.checked)}
          disabled={saving}
        />
        <span>
          {t("SetupPage.disableDefaultAdmin")}
          <small>{t("SetupPage.disableDefaultAdminHint")}</small>
        </span>
      </label>

      {error && <p className={styles.error}>{error}</p>}

      <div className={styles.actions}>
        <button type="button" className={styles.btnSecondary} onClick={onBack} disabled={saving}>
          <MIcon name="arrow_back" size={18} />
          {t("SetupPage.back")}
        </button>
        <button type="submit" className={styles.btnPrimary} disabled={saving}>
          {saving ? t("SetupPage.saving") : t("SetupPage.saveAndNext")}
          {!saving && <MIcon name="arrow_forward" size={18} />}
        </button>
      </div>
    </form>
  );
}

/* ─── 步驟 2：PVE 連線 ───────────────────────────────────── */

function storageLabel(s, t) {
  return t("SetupPage.storageOption", {
    name: s.storage,
    type: s.storage_type ?? "-",
    avail: Math.round(s.avail_gb),
  }) + (s.is_shared ? "" : ` @ ${s.nodes.join(", ")}`);
}

function ProxmoxStep({ alreadyDone, onSaved, onSkip, onBack, onNext }) {
  const { t } = useTranslation("login");
  const toast = useToast();
  const [form, setForm] = useState(EMPTY_PROXMOX_FORM);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testedSignature, setTestedSignature] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = (name, value) => setForm((prev) => ({ ...prev, [name]: value }));

  const stale = Boolean(testResult?.success) && connectionSignature(form) !== testedSignature;
  const tested = Boolean(testResult?.success) && !stale;
  const isoStorages = useMemo(() => (testResult?.storages ?? []).filter((s) => s.can_iso), [testResult]);
  const dataStorages = useMemo(() => (testResult?.storages ?? []).filter((s) => s.can_vm), [testResult]);

  async function handleTest() {
    setError("");
    setTesting(true);
    try {
      const result = await SetupService.testProxmox(connectionPayload(form));
      setTestResult(result);
      setTestedSignature(connectionSignature(form));
      if (result.success) {
        // 用偵測到的資料帶入預設值：主節點、第一個能放 ISO／磁碟的 storage
        const primary = result.nodes.find((n) => n.is_primary) ?? result.nodes[0];
        const iso = result.storages.find((s) => s.can_iso && s.storage === "local")
          ?? result.storages.find((s) => s.can_iso);
        const data = result.storages.find((s) => s.can_vm && s.storage === "local-lvm")
          ?? result.storages.find((s) => s.can_vm);
        setForm((prev) => ({
          ...prev,
          default_node: prev.default_node || primary?.name || "",
          iso_storage: iso?.storage ?? prev.iso_storage,
          data_storage: data?.storage ?? prev.data_storage,
          name: prev.name || (result.is_cluster ? "cluster" : primary?.name || prev.host),
        }));
      }
    } catch (err) {
      setTestResult({ success: false, error: err?.message ?? t("SetupPage.testFailed") });
      setTestedSignature(null);
    } finally {
      setTesting(false);
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    if (!tested) {
      setError(t("SetupPage.testFirst"));
      return;
    }
    setSaving(true);
    try {
      const result = await SetupService.createProxmox({
        ...connectionPayload(form),
        name: form.name.trim(),
        pool_name: form.pool_name.trim() || "SkyLab",
        iso_storage: form.iso_storage.trim() || "local",
        data_storage: form.data_storage.trim() || "local-lvm",
        default_node: form.default_node.trim() || null,
        is_default: true,
        enabled: true,
      });
      if (result.sync_error) {
        toast.warning(t("SetupPage.proxmoxSavedSyncFailed", { error: result.sync_error }));
      } else {
        toast.success(t("SetupPage.proxmoxSaved", {
          nodes: result.nodes.length,
          storages: result.storage_count,
        }));
      }
      onSaved(result);
    } catch (err) {
      setError(err?.message ?? t("SetupPage.proxmoxSaveFailed"));
    } finally {
      setSaving(false);
    }
  }

  if (alreadyDone) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>{t("SetupPage.proxmoxTitle")}</h2>
        <Notice icon="check_circle" tone="success">{t("SetupPage.proxmoxDoneNotice")}</Notice>
        <div className={styles.actions}>
          <button type="button" className={styles.btnSecondary} onClick={onBack}>
            <MIcon name="arrow_back" size={18} />
            {t("SetupPage.back")}
          </button>
          <button type="button" className={styles.btnPrimary} onClick={onNext}>
            {t("SetupPage.next")}
            <MIcon name="arrow_forward" size={18} />
          </button>
        </div>
      </section>
    );
  }

  const busy = testing || saving;

  return (
    <form className={styles.section} onSubmit={handleSubmit}>
      <h2 className={styles.sectionTitle}>{t("SetupPage.proxmoxTitle")}</h2>
      <p className={styles.sectionDesc}>{t("SetupPage.proxmoxDesc")}</p>

      <div className={styles.formGrid}>
        <label className={styles.field}>
          <span>{t("SetupPage.hostLabel")} *</span>
          <input
            value={form.host}
            onChange={(e) => set("host", e.target.value)}
            placeholder={t("SetupPage.hostPlaceholder")}
            disabled={busy}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{t("SetupPage.portLabel")}</span>
          <input
            type="number"
            min={1}
            max={65535}
            value={form.port}
            onChange={(e) => set("port", e.target.value)}
            disabled={busy}
          />
        </label>
        <label className={styles.field}>
          <span>{t("SetupPage.apiUserLabel")} *</span>
          <input
            value={form.user}
            onChange={(e) => set("user", e.target.value)}
            placeholder="root@pam"
            disabled={busy}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{t("SetupPage.pvePasswordLabel")} *</span>
          <PasswordInput
            autoComplete="off"
            value={form.password}
            onChange={(e) => set("password", e.target.value)}
            placeholder={t("SetupPage.pvePasswordPlaceholder")}
            disabled={busy}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{t("SetupPage.apiTimeoutLabel")}</span>
          <input
            type="number"
            min={1}
            max={300}
            value={form.api_timeout}
            onChange={(e) => set("api_timeout", e.target.value)}
            disabled={busy}
          />
        </label>
      </div>

      <label className={styles.checkRow}>
        <input
          type="checkbox"
          checked={Boolean(form.verify_ssl)}
          onChange={(e) => set("verify_ssl", e.target.checked)}
          disabled={busy}
        />
        <span>{t("SetupPage.verifySsl")}</span>
      </label>
      {form.verify_ssl && (
        <label className={styles.field}>
          <span>{t("SetupPage.caCertLabel")}</span>
          <textarea
            rows={4}
            value={form.ca_cert}
            onChange={(e) => set("ca_cert", e.target.value)}
            placeholder="-----BEGIN CERTIFICATE-----"
            spellCheck={false}
            disabled={busy}
          />
        </label>
      )}

      <div className={styles.testRow}>
        <button
          type="button"
          className={styles.btnSecondary}
          onClick={handleTest}
          disabled={busy || !form.host.trim() || !form.user.trim() || !form.password}
        >
          <MIcon name={testing ? "sync" : "network_check"} size={18} className={testing ? styles.spin : undefined} />
          {testing ? t("SetupPage.testing") : t("SetupPage.testConnection")}
        </button>
        {stale && <span className={styles.hintWarn}>{t("SetupPage.retestHint")}</span>}
      </div>

      {testResult && !stale && (
        <div className={`${styles.testResult} ${testResult.success ? styles.testResult_ok : styles.testResult_fail}`}>
          <MIcon name={testResult.success ? "check_circle" : "error"} size={20} />
          <div>
            <strong>
              {testResult.success
                ? (testResult.is_cluster
                  ? t("SetupPage.testSuccessCluster", { count: testResult.nodes.length })
                  : t("SetupPage.testSuccessSingle"))
                : (testResult.error || t("SetupPage.testFailed"))}
            </strong>
            {testResult.success && (
              <div className={styles.nodeChips} aria-label={t("SetupPage.detectedNodes")}>
                {testResult.nodes.map((n) => (
                  <span key={n.name} className={styles.nodeChip}>
                    <MIcon name={n.is_primary ? "star" : "dns"} size={14} />
                    {n.name}
                    <small>{n.host}</small>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {tested && (
        <>
          <h3 className={styles.subTitle}>{t("SetupPage.resourceTitle")}</h3>
          <p className={styles.sectionDesc}>{t("SetupPage.resourceDesc")}</p>
          <div className={styles.formGrid}>
            <label className={styles.field}>
              <span>{t("SetupPage.connectionName")} *</span>
              <input
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
                placeholder={t("SetupPage.connectionNamePlaceholder")}
                disabled={busy}
                required
              />
            </label>
            <label className={styles.field}>
              <span>{t("SetupPage.poolName")}</span>
              <input
                value={form.pool_name}
                onChange={(e) => set("pool_name", e.target.value)}
                placeholder="SkyLab"
                disabled={busy}
              />
            </label>
            <label className={styles.field}>
              <span>{t("SetupPage.defaultNode")}</span>
              <select value={form.default_node} onChange={(e) => set("default_node", e.target.value)} disabled={busy}>
                <option value="">{t("SetupPage.autoChoose")}</option>
                {testResult.nodes.map((n) => (
                  <option key={n.name} value={n.name}>{n.name}</option>
                ))}
              </select>
            </label>
            <label className={styles.field}>
              <span>{t("SetupPage.isoStorage")}</span>
              {isoStorages.length > 0 ? (
                <select value={form.iso_storage} onChange={(e) => set("iso_storage", e.target.value)} disabled={busy}>
                  {isoStorages.map((s) => (
                    <option key={`${s.storage}-${s.nodes.join(",")}`} value={s.storage}>{storageLabel(s, t)}</option>
                  ))}
                </select>
              ) : (
                <>
                  <input value={form.iso_storage} onChange={(e) => set("iso_storage", e.target.value)} disabled={busy} />
                  <small className={styles.fieldHint}>{t("SetupPage.noIsoStorage")}</small>
                </>
              )}
            </label>
            <label className={styles.field}>
              <span>{t("SetupPage.dataStorage")}</span>
              {dataStorages.length > 0 ? (
                <select value={form.data_storage} onChange={(e) => set("data_storage", e.target.value)} disabled={busy}>
                  {dataStorages.map((s) => (
                    <option key={`${s.storage}-${s.nodes.join(",")}`} value={s.storage}>{storageLabel(s, t)}</option>
                  ))}
                </select>
              ) : (
                <>
                  <input value={form.data_storage} onChange={(e) => set("data_storage", e.target.value)} disabled={busy} />
                  <small className={styles.fieldHint}>{t("SetupPage.noDataStorage")}</small>
                </>
              )}
            </label>
          </div>
        </>
      )}

      {error && <p className={styles.error}>{error}</p>}

      <div className={styles.actions}>
        <button type="button" className={styles.btnSecondary} onClick={onBack} disabled={busy}>
          <MIcon name="arrow_back" size={18} />
          {t("SetupPage.back")}
        </button>
        <div className={styles.actionGroup}>
          <button type="button" className={styles.btnGhost} onClick={onSkip} disabled={busy}>
            {t("SetupPage.skip")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={busy || !tested}>
            {saving ? t("SetupPage.saving") : t("SetupPage.saveAndNext")}
            {!saving && <MIcon name="arrow_forward" size={18} />}
          </button>
        </div>
      </div>
    </form>
  );
}

/* ─── 步驟 3：IP 網段 ────────────────────────────────────── */

function SubnetStep({ alreadyDone, onSaved, onSkip, onBack, onNext }) {
  const { t } = useTranslation("login");
  const toast = useToast();
  const [form, setForm] = useState(EMPTY_SUBNET_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = (name, value) => setForm((prev) => ({ ...prev, [name]: value }));

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const result = await SetupService.configureSubnet({
        cidr: form.cidr.trim(),
        gateway: form.gateway.trim(),
        bridge_name: form.bridge_name.trim(),
        gateway_vm_ip: form.gateway_vm_ip.trim(),
        dns_servers: form.dns_servers.trim() || null,
        extra_blocked_subnets: [],
        forward_port_start: Number(form.forward_port_start) || 30000,
        forward_port_end: Number(form.forward_port_end) || 39999,
        forward_public_host: form.forward_public_host.trim() || null,
      });
      toast.success(t("SetupPage.subnetSaved", { total: result.total_ips }));
      onSaved(result);
    } catch (err) {
      setError(err?.message ?? t("SetupPage.subnetSaveFailed"));
    } finally {
      setSaving(false);
    }
  }

  if (alreadyDone) {
    return (
      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>{t("SetupPage.subnetTitle")}</h2>
        <Notice icon="check_circle" tone="success">{t("SetupPage.subnetDoneNotice")}</Notice>
        <div className={styles.actions}>
          <button type="button" className={styles.btnSecondary} onClick={onBack}>
            <MIcon name="arrow_back" size={18} />
            {t("SetupPage.back")}
          </button>
          <button type="button" className={styles.btnPrimary} onClick={onNext}>
            {t("SetupPage.next")}
            <MIcon name="arrow_forward" size={18} />
          </button>
        </div>
      </section>
    );
  }

  return (
    <form className={styles.section} onSubmit={handleSubmit}>
      <h2 className={styles.sectionTitle}>{t("SetupPage.subnetTitle")}</h2>
      <p className={styles.sectionDesc}>{t("SetupPage.subnetDesc")}</p>

      <div className={styles.formGrid}>
        <label className={styles.field}>
          <span>{t("SetupPage.cidrLabel")} *</span>
          <input
            value={form.cidr}
            onChange={(e) => set("cidr", e.target.value)}
            placeholder={t("SetupPage.cidrPlaceholder")}
            disabled={saving}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{t("SetupPage.bridgeLabel")} *</span>
          <input
            value={form.bridge_name}
            onChange={(e) => set("bridge_name", e.target.value)}
            placeholder={t("SetupPage.bridgePlaceholder")}
            disabled={saving}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{t("SetupPage.gatewayLabel")} *</span>
          <input
            value={form.gateway}
            onChange={(e) => set("gateway", e.target.value)}
            placeholder={t("SetupPage.gatewayPlaceholder")}
            pattern={IPV4_PATTERN}
            disabled={saving}
            required
          />
        </label>
        <label className={styles.field}>
          <span>{t("SetupPage.gatewayVmIpLabel")} *</span>
          <input
            value={form.gateway_vm_ip}
            onChange={(e) => set("gateway_vm_ip", e.target.value)}
            placeholder={t("SetupPage.gatewayVmIpPlaceholder")}
            pattern={IPV4_PATTERN}
            disabled={saving}
            required
          />
        </label>
        <label className={`${styles.field} ${styles.fieldWide}`}>
          <span>{t("SetupPage.dnsLabel")}</span>
          <input
            value={form.dns_servers}
            onChange={(e) => set("dns_servers", e.target.value)}
            placeholder={t("SetupPage.dnsPlaceholder")}
            disabled={saving}
          />
        </label>
      </div>

      <details className={styles.details}>
        <summary>{t("SetupPage.advancedToggle")}</summary>
        <div className={styles.formGrid}>
          {/* 起—迄是同一個欄位：一個標籤、一組成對控制項 */}
          <div className={styles.field}>
            <span>{t("SetupPage.forwardPortPool")}</span>
            <div className={styles.portPair}>
              <input
                type="number"
                min={1024}
                max={65535}
                aria-label={t("SetupPage.forwardPortPoolStart")}
                value={form.forward_port_start}
                onChange={(e) => set("forward_port_start", e.target.value)}
                disabled={saving}
              />
              <span aria-hidden="true">–</span>
              <input
                type="number"
                min={1024}
                max={65535}
                aria-label={t("SetupPage.forwardPortPoolEnd")}
                value={form.forward_port_end}
                onChange={(e) => set("forward_port_end", e.target.value)}
                disabled={saving}
              />
            </div>
          </div>
          <label className={`${styles.field} ${styles.fieldWide}`}>
            <span>{t("SetupPage.forwardPublicHost")}</span>
            <input
              value={form.forward_public_host}
              onChange={(e) => set("forward_public_host", e.target.value)}
              placeholder={t("SetupPage.forwardPublicHostPlaceholder")}
              disabled={saving}
            />
          </label>
        </div>
      </details>

      {error && <p className={styles.error}>{error}</p>}

      <div className={styles.actions}>
        <button type="button" className={styles.btnSecondary} onClick={onBack} disabled={saving}>
          <MIcon name="arrow_back" size={18} />
          {t("SetupPage.back")}
        </button>
        <div className={styles.actionGroup}>
          <button type="button" className={styles.btnGhost} onClick={onSkip} disabled={saving}>
            {t("SetupPage.skip")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={saving}>
            {saving ? t("SetupPage.saving") : t("SetupPage.saveAndNext")}
            {!saving && <MIcon name="arrow_forward" size={18} />}
          </button>
        </div>
      </div>
    </form>
  );
}

/* ─── 步驟 4：完成 ───────────────────────────────────────── */

function FinishStep({ steps, adminCreds, proxmoxResult, subnetResult, onBack }) {
  const { t } = useTranslation("login");
  const toast = useToast();
  const navigate = useNavigate();
  const { login } = useAuth();
  const [finishing, setFinishing] = useState(false);
  const [error, setError] = useState("");

  async function handleFinish() {
    setError("");
    setFinishing(true);
    try {
      await SetupService.complete();
      markSetupCompleted();
      if (adminCreds) {
        try {
          await login(adminCreds.email, adminCreds.password);
          navigate("/dashboard", { replace: true });
          return;
        } catch {
          // 完成了但自動登入失敗（例如 token 驗證暫時失敗）：退回登入頁手動登入
        }
      }
      toast.success(t("SetupPage.finishedGoLogin"));
      navigate("/login", { replace: true });
    } catch (err) {
      setError(err?.message ?? t("SetupPage.finishFailed"));
      setFinishing(false);
    }
  }

  const proxmoxText = proxmoxResult
    ? t("SetupPage.summaryProxmoxNodes", {
      name: proxmoxResult.name,
      host: proxmoxResult.host,
      count: proxmoxResult.nodes.length,
    })
    : steps.proxmox ? t("SetupPage.summaryConfigured") : t("SetupPage.summarySkipped");
  const subnetText = subnetResult
    ? `${subnetResult.cidr} · ${subnetResult.bridge_name}`
    : steps.subnet ? t("SetupPage.summaryConfigured") : t("SetupPage.summarySkipped");

  return (
    <section className={styles.section}>
      <h2 className={styles.sectionTitle}>{t("SetupPage.finishTitle")}</h2>
      <p className={styles.sectionDesc}>{t("SetupPage.finishDesc")}</p>

      <dl className={styles.summary}>
        <div>
          <dt><MIcon name="admin_panel_settings" size={18} />{t("SetupPage.summaryAdmin")}</dt>
          <dd>{adminCreds?.email ?? t("SetupPage.summaryConfigured")}</dd>
        </div>
        <div>
          <dt><MIcon name="dns" size={18} />{t("SetupPage.summaryProxmox")}</dt>
          <dd className={!proxmoxResult && !steps.proxmox ? styles.muted : undefined}>{proxmoxText}</dd>
        </div>
        <div>
          <dt><MIcon name="lan" size={18} />{t("SetupPage.summarySubnet")}</dt>
          <dd className={!subnetResult && !steps.subnet ? styles.muted : undefined}>{subnetText}</dd>
        </div>
      </dl>

      {error && <p className={styles.error}>{error}</p>}

      <div className={styles.actions}>
        <button type="button" className={styles.btnSecondary} onClick={onBack} disabled={finishing}>
          <MIcon name="arrow_back" size={18} />
          {t("SetupPage.back")}
        </button>
        <button type="button" className={styles.btnPrimary} onClick={handleFinish} disabled={finishing}>
          {finishing ? t("SetupPage.finishing") : t("SetupPage.finishAndLogin")}
          {!finishing && <MIcon name="rocket_launch" size={18} />}
        </button>
      </div>
    </section>
  );
}

/* ─── 頁面 ─────────────────────────────────────────────── */

export default function SetupPage() {
  const { t } = useTranslation("login");
  const { user } = useAuth();
  const { status, loading, error, refresh } = useSetupStatus();
  const [started, setStarted] = useState(false);
  const [step, setStep] = useState(STEP_ADMIN);
  const [adminCreds, setAdminCreds] = useState(null);
  const [adminDone, setAdminDone] = useState(false);
  const [proxmoxResult, setProxmoxResult] = useState(null);
  const [subnetResult, setSubnetResult] = useState(null);

  useEffect(() => {
    document.title = `${t("SetupPage.title")} · SkyLab`;
  }, [t]);

  const steps = useMemo(() => ({
    admin: Boolean(status?.steps?.admin) || adminDone,
    proxmox: Boolean(status?.steps?.proxmox) || Boolean(proxmoxResult),
    subnet: Boolean(status?.steps?.subnet) || Boolean(subnetResult),
  }), [status, adminDone, proxmoxResult, subnetResult]);

  const stepLabels = useMemo(() => [
    t("SetupPage.stepAdmin"),
    t("SetupPage.stepProxmox"),
    t("SetupPage.stepSubnet"),
    t("SetupPage.stepFinish"),
  ], [t]);

  const goTo = useCallback((next) => setStep(next), []);

  let body;
  if (loading && !status) {
    body = (
      <div className={styles.center}>
        <LoadingSpinner size={36} />
        <p className={styles.sectionDesc}>{t("SetupPage.loading")}</p>
      </div>
    );
  } else if (!status) {
    body = (
      <div className={styles.center}>
        <MIcon name="cloud_off" size={40} />
        <p className={styles.sectionDesc}>{error?.message || t("SetupPage.loadFailed")}</p>
        <button type="button" className={styles.btnPrimary} onClick={refresh}>
          <MIcon name="refresh" size={18} />
          {t("SetupPage.retry")}
        </button>
      </div>
    );
  } else if (status.completed) {
    body = (
      <div className={styles.center}>
        <MIcon name="verified" size={40} />
        <h2 className={styles.sectionTitle}>{t("SetupPage.completedTitle")}</h2>
        <p className={styles.sectionDesc}>{t("SetupPage.completedDesc")}</p>
        <a className={styles.btnPrimary} href={user ? "/dashboard" : "/login"}>
          {user ? t("SetupPage.goDashboard") : t("SetupPage.goLogin")}
          <MIcon name="arrow_forward" size={18} />
        </a>
      </div>
    );
  } else if (!started) {
    body = <LanguageWelcome onContinue={() => setStarted(true)} />;
  } else {
    body = (
      <>
        <Stepper current={step} steps={stepLabels} />
        {step === STEP_ADMIN && (
          <AdminStep
            alreadyDone={steps.admin}
            savedEmail={adminCreds?.email}
            onSaved={(saved) => {
              setAdminCreds({ email: saved.email, password: saved.password });
              setAdminDone(true);
              goTo(STEP_PROXMOX);
            }}
            onBack={() => setStarted(false)}
            onNext={() => goTo(STEP_PROXMOX)}
          />
        )}
        {step === STEP_PROXMOX && (
          <ProxmoxStep
            alreadyDone={steps.proxmox}
            onSaved={(result) => {
              setProxmoxResult(result);
              goTo(STEP_SUBNET);
            }}
            onSkip={() => goTo(STEP_SUBNET)}
            onBack={() => goTo(STEP_ADMIN)}
            onNext={() => goTo(STEP_SUBNET)}
          />
        )}
        {step === STEP_SUBNET && (
          <SubnetStep
            alreadyDone={steps.subnet}
            onSaved={(result) => {
              setSubnetResult(result);
              goTo(STEP_FINISH);
            }}
            onSkip={() => goTo(STEP_FINISH)}
            onBack={() => goTo(STEP_PROXMOX)}
            onNext={() => goTo(STEP_FINISH)}
          />
        )}
        {step === STEP_FINISH && (
          <FinishStep
            steps={steps}
            adminCreds={adminCreds}
            proxmoxResult={proxmoxResult}
            subnetResult={subnetResult}
            onBack={() => goTo(STEP_SUBNET)}
          />
        )}
      </>
    );
  }

  return (
    <PageShell>
      {/* 歡迎畫面自己有大標題，其餘畫面才掛「初始設定」頁首 */}
      {(started || !status || status.completed) && (
        <header className={styles.header}>
          <h1 className={styles.title}>{t("SetupPage.title")}</h1>
        </header>
      )}
      {body}
    </PageShell>
  );
}
