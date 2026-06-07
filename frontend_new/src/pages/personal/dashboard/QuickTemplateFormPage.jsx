import { useContext, useEffect, useState } from "react";
import rawData from "virtual:templates";
import styles from "./QuickTemplateFormPage.module.scss";
import MIcon from "../../../components/MIcon";
import AiSidePanel from "../requests/AiSidePanel";
import { useToast } from "../../../hooks/useToast";
import { LayoutContext } from "../../../layout/DashboardLayout";

/* Load quick templates from virtual:templates */
const QUICK_TEMPLATES = Object.entries(rawData)
  .filter(([key]) => !["metadata.json", "versions.json", "github-versions.json"].includes(key))
  .map(([, value]) => value)
  .filter(Boolean);
const getQuickTemplate = (slug) => QUICK_TEMPLATES.find((t) => t.slug === slug);

function normalizeHostname(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63);
}

/* ── Access mode / firewall option configs ── */
const ACCESS_MODES = (canPort) => [
  { value: "private",        label: "不公開",   icon: "lock",   disabled: false },
  { value: "public-website", label: "公開網站", icon: "public", disabled: !canPort },
  { value: "public-port",    label: "公開 Port", icon: "power", disabled: !canPort },
];

const FIREWALL_PRESETS = (canWebsite) => [
  { value: "safe",     label: "安全", icon: "shield",     disabled: false },
  { value: "website",  label: "網站", icon: "public",     disabled: !canWebsite },
  { value: "internal", label: "內部", icon: "hub",        disabled: false },
];

const TOGGLE_OPTIONS = [
  { value: "on",  label: "開啟" },
  { value: "off", label: "關閉" },
];

/* ── Option button group ── */
function OptionGroup({ options, value, onChange }) {
  return (
    <div className={styles.optionGrid}>
      {options.map((opt) => {
        const active = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            disabled={opt.disabled}
            onClick={() => !opt.disabled && onChange(opt.value)}
            aria-pressed={active}
            className={`${styles.optionBtn} ${active ? styles.optionBtnActive : ""}`}
          >
            <MIcon name={opt.icon} size={18} />
            <span>{opt.label}</span>
          </button>
        );
      })}
    </div>
  );
}

/* ── Toggle button group (on/off) ── */
function ToggleGroup({ value, onChange }) {
  return (
    <div className={styles.toggleGrid}>
      {TOGGLE_OPTIONS.map((opt) => {
        const active = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            aria-pressed={active}
            className={`${styles.toggleBtn} ${active ? styles.toggleBtnActive : ""}`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

/* ── Page ── */
export default function QuickTemplateFormPage({ slug, onBack, onSubmitted }) {
  const toast = useToast();
  const { setCompactFooter } = useContext(LayoutContext);
  useEffect(() => { setCompactFooter(true); return () => setCompactFooter(false); }, [setCompactFooter]);

  const template = getQuickTemplate(slug);

  const interfacePort = template?.interface_port ?? null;
  const description   = template?.description_zh || template?.description || "";
  const logo          = template?.logo || "";
  const canPublic     = Boolean(interfacePort);

  /* Basic settings */
  const [hostname, setHostname] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors]     = useState({});

  /* Advanced settings */
  const [showAdvanced, setShowAdvanced]     = useState(false);
  const [accessMode, setAccessMode]         = useState("private");
  const [firewallPreset, setFirewallPreset] = useState("safe");
  const [enableHttps, setEnableHttps]       = useState("on");
  const [autoDomain, setAutoDomain]         = useState("on");
  const [externalPort, setExternalPort]     = useState("");

  const [logoFailed, setLogoFailed] = useState(false);

  /* Prefill hostname when template loads */
  useEffect(() => {
    if (!template) return;
    setHostname((prev) => prev || normalizeHostname(
      `${template.slug}-${Date.now().toString().slice(-6)}`,
    ));
  }, [template]);

  function update(setter, key) {
    return (val) => {
      setter(val);
      if (errors[key]) setErrors((prev) => ({ ...prev, [key]: "" }));
    };
  }

  function handleSubmit(e) {
    e.preventDefault();
    const errs = {};
    const hostnameRegex = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/;

    if (!hostname.trim()) {
      errs.hostname = "容器名稱為必填項";
    } else if (!hostnameRegex.test(hostname)) {
      errs.hostname = "僅允許小寫字母、數字和連字符，且不能以連字符開頭或結尾";
    }
    if (!password) {
      errs.password = "密碼為必填項";
    } else if (password.length < 8) {
      errs.password = "密碼至少需要 8 個字符";
    }

    if (Object.keys(errs).length > 0) {
      setErrors(errs);
      return;
    }

    // Stage 1: UI only — backend wiring pending
    toast.success(`已建立 ${template?.name || slug}（UI 階段，尚未串接後端）`);
    onSubmitted?.();
  }

  if (!template) {
    return (
      <div className={styles.page}>
        <div className={styles.notFound}>
          <MIcon name="error_outline" size={40} />
          <h2>找不到模板</h2>
          <p>無法載入「{slug}」的模板資料。</p>
          <button type="button" className={styles.btnSecondary} onClick={onBack}>
            <MIcon name="arrow_back" size={16} />
            返回首頁
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      {/* Header */}
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>快速建立</h1>
          <p className={styles.pageSubtitle}>選擇模板一鍵建立練習環境，送出後系統會自動審核並佈建</p>
        </div>
        <button type="button" className={styles.backBtn} onClick={onBack}>
          <MIcon name="arrow_back" size={18} />
          返回
        </button>
      </div>

      <div className={styles.body}>
        <div className={styles.formScroll}>
        <form id="quick-template-form" className={styles.form} onSubmit={handleSubmit}>

          {/* ── Template header card ── */}
          <div className={styles.templateHeader}>
            <div className={styles.templateLogo}>
              {logo && !logoFailed ? (
                <img
                  src={logo}
                  alt={`${template.name} logo`}
                  width={44}
                  height={44}
                  loading="lazy"
                  onError={() => setLogoFailed(true)}
                />
              ) : (
                <MIcon name="layers" size={28} />
              )}
            </div>
            <div className={styles.templateMeta}>
              <div className={styles.templateTitleRow}>
                <h2 className={styles.templateName}>{template.name}</h2>
                <button
                  type="button"
                  className={styles.templateExitLink}
                  onClick={onBack}
                  title="改用完整申請表單"
                >
                  <MIcon name="tune" size={14} />
                  完整設定
                </button>
              </div>
              {description && (
                <p className={styles.templateDesc}>{description}</p>
              )}
              {interfacePort && (
                <div className={styles.templateChips}>
                  <span className={styles.portChip}>
                    <MIcon name="bolt" size={12} />
                    Port {interfacePort}
                  </span>
                </div>
              )}
              <p className={styles.templateStatus}>
                <MIcon name="schedule" size={13} />
                正在準備基礎映像，完成後即可建立。
              </p>
            </div>
          </div>

          {/* ── Advanced settings (collapsible) ── */}
          <div className={styles.section}>
            <button
              type="button"
              className={styles.advancedToggle}
              onClick={() => setShowAdvanced((v) => !v)}
              aria-expanded={showAdvanced}
            >
              <div className={styles.advancedToggleText}>
                <span className={styles.advancedToggleTitle}>進階設定</span>
                <span className={styles.advancedToggleDesc}>
                  公開存取、防火牆、HTTPS、自動網域
                </span>
              </div>
              <MIcon
                name={showAdvanced ? "keyboard_arrow_up" : "keyboard_arrow_down"}
                size={22}
              />
            </button>

            {showAdvanced && (
              <div className={styles.advancedBody}>
                <fieldset className={styles.field}>
                  <legend className={styles.fieldLabel}>公開存取</legend>
                  <OptionGroup
                    options={ACCESS_MODES(canPublic)}
                    value={accessMode}
                    onChange={setAccessMode}
                  />
                </fieldset>

                <fieldset className={styles.field}>
                  <legend className={styles.fieldLabel}>防火牆預設</legend>
                  <OptionGroup
                    options={FIREWALL_PRESETS(canPublic)}
                    value={firewallPreset}
                    onChange={setFirewallPreset}
                  />
                </fieldset>

                {accessMode === "public-website" && (
                  <div className={styles.fieldGrid}>
                    <fieldset className={styles.field}>
                      <legend className={styles.fieldLabel}>HTTPS</legend>
                      <ToggleGroup value={enableHttps} onChange={setEnableHttps} />
                    </fieldset>
                    <fieldset className={styles.field}>
                      <legend className={styles.fieldLabel}>自動網域</legend>
                      <ToggleGroup value={autoDomain} onChange={setAutoDomain} />
                    </fieldset>
                  </div>
                )}

                {accessMode === "public-port" && (
                  <div className={styles.field}>
                    <label className={styles.fieldLabel} htmlFor="external-port">
                      外部 Port
                    </label>
                    <input
                      id="external-port"
                      type="number"
                      min={1}
                      max={65535}
                      className={styles.input}
                      placeholder={interfacePort ? String(interfacePort) : "8080"}
                      value={externalPort}
                      onChange={(e) => setExternalPort(e.target.value)}
                    />
                  </div>
                )}

                <div className={styles.infoHint}>
                  {interfacePort ? (
                    <p>模板預設服務 Port：{interfacePort}</p>
                  ) : (
                    <p>此模板沒有預設服務 Port，公開網站與公開 Port 會停用。</p>
                  )}
                  {firewallPreset === "internal" && (
                    <p>內部模式不會自動建立對外公開規則。</p>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* ── Basic settings ── */}
          <div className={`${styles.section} ${styles.sectionPadded}`}>
            <h3 className={styles.sectionTitle}>基本設定</h3>

            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="quick-hostname">
                容器名稱 <span className={styles.required}>*</span>
              </label>
              <input
                id="quick-hostname"
                className={styles.input}
                placeholder={`${slug}-xxxxxx`}
                value={hostname}
                onChange={(e) => update(setHostname, "hostname")(e.target.value)}
                onBlur={(e) => setHostname(normalizeHostname(e.target.value))}
              />
              {errors.hostname && <p className={styles.fieldError}>{errors.hostname}</p>}
            </div>

            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="quick-password">
                Root 密碼 <span className={styles.required}>*</span>
              </label>
              <input
                id="quick-password"
                type="password"
                className={styles.input}
                placeholder="至少 8 個字元"
                value={password}
                onChange={(e) => update(setPassword, "password")(e.target.value)}
              />
              {errors.password && <p className={styles.fieldError}>{errors.password}</p>}
            </div>
          </div>

        </form>

        {/* ── Actions（與 RequestFormPage 一致：在 formScroll 底部） ── */}
        <div className={styles.actions}>
          <button type="button" className={styles.btnSecondary} onClick={onBack}>
            取消
          </button>
          <button type="submit" form="quick-template-form" className={styles.btnPrimary}>
            <MIcon name="bolt" size={16} />
            建立資源
          </button>
        </div>
        </div>

        {/* ── AI side panel ── */}
        <aside className={styles.aside}>
          <AiSidePanel className={styles.aiPanel} />
        </aside>
      </div>
    </div>
  );
}
