import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./settings.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { LdapConfigService } from "../../../services/ldapConfig";
import { useToast } from "../../../hooks/useToast";
import { useUnsavedChangesGuard } from "../../../contexts/UnsavedChangesContext";

/**
 * LDAP（系統管理 → LDAP）：LDAP / Active Directory 登入的連線、服務帳號與角色對映。
 * 2026-09 從「系統設定」的分頁拆成獨立頁面。
 */

/** 表單值 → API partial payload（bind_password 留空表示不變更） */
function toPayload(form) {
  return {
    enabled: form.enabled,
    server_uri: form.server_uri,
    use_starttls: form.use_starttls,
    bind_dn: form.bind_dn,
    bind_password: form.bind_password || null,
    user_search_base: form.user_search_base,
    user_filter_template: form.user_filter_template,
    email_attribute: form.email_attribute,
    name_attribute: form.name_attribute,
    teacher_group_dn: form.teacher_group_dn || null,
    admin_group_dn: form.admin_group_dn || null,
    auto_create_users: form.auto_create_users,
    connect_timeout_seconds: form.connect_timeout_seconds,
  };
}

function buildForm(config) {
  return {
    enabled: config.enabled,
    server_uri: config.server_uri,
    use_starttls: config.use_starttls,
    bind_dn: config.bind_dn,
    bind_password: "",
    user_search_base: config.user_search_base,
    user_filter_template: config.user_filter_template,
    email_attribute: config.email_attribute,
    name_attribute: config.name_attribute,
    teacher_group_dn: config.teacher_group_dn ?? "",
    admin_group_dn: config.admin_group_dn ?? "",
    auto_create_users: config.auto_create_users,
    connect_timeout_seconds: config.connect_timeout_seconds,
  };
}

function LdapForm() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const [config, setConfig] = useState(null);
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const baseline = useMemo(() => (config ? buildForm(config) : null), [config]);
  const dirty = Boolean(form && baseline && Object.keys(form).some((key) => form[key] !== baseline[key]));
  useUnsavedChangesGuard(dirty);

  useEffect(() => {
    let cancelled = false;
    LdapConfigService.get()
      .then((cfg) => {
        if (cancelled) return;
        setConfig(cfg);
        setForm(buildForm(cfg));
      })
      .catch((err) => toast.error(err?.message ?? t("LdapTab.toastLoadFailed")));
    return () => {
      cancelled = true;
    };
  }, [toast, t]);

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await LdapConfigService.update(toPayload(form));
      setConfig(updated);
      setForm(buildForm(updated));
      toast.success(t("LdapTab.toastSaved"));
    } catch (err) {
      toast.error(t("LdapTab.toastSaveFailed", { message: err?.message ?? t("LdapTab.unknownError") }));
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    try {
      const result = await LdapConfigService.test(toPayload(form));
      if (result.ok) toast.success(result.message || t("LdapTab.toastConnectTestSuccess"));
      else toast.error(result.message || t("LdapTab.toastConnectTestFailed"));
    } catch (err) {
      toast.error(t("LdapTab.toastTestFailed", { message: err?.message ?? t("LdapTab.unknownError") }));
    } finally {
      setTesting(false);
    }
  }

  if (!form) return <LoadingState text={t("LdapTab.loading")} />;

  return (
    <form className={styles.panelStack} onSubmit={handleSave}>
      <div className={styles.card}>
        <h2 className={styles.cardTitle}>{t("LdapTab.loginSectionTitle")}</h2>
        <p className={styles.cardDesc}>
          {t("LdapTab.loginSectionDesc")}
        </p>
        <label className={styles.checkRow}>
          <input
            type="checkbox"
            checked={Boolean(form.enabled)}
            onChange={(e) => setField("enabled", e.target.checked)}
          />
          <span>{t("LdapTab.enableLdapLogin")}</span>
          <em className={styles.fieldHint}>{t("LdapTab.enableLdapLoginHint")}</em>
        </label>
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span>{t("LdapTab.serverUri")}</span>
            <input
              value={form.server_uri}
              onChange={(e) => setField("server_uri", e.target.value)}
              placeholder={t("LdapTab.serverUriPlaceholder")}
              required
            />
          </label>
          <label className={styles.field}>
            <span>{t("LdapTab.connectTimeoutSeconds")}</span>
            <input
              type="number"
              min={1}
              max={60}
              value={form.connect_timeout_seconds}
              onChange={(e) => setField("connect_timeout_seconds", e.target.valueAsNumber)}
            />
          </label>
        </div>
        <label className={styles.checkRow}>
          <input
            type="checkbox"
            checked={Boolean(form.use_starttls)}
            onChange={(e) => setField("use_starttls", e.target.checked)}
          />
          <span>{t("LdapTab.useStartTls")}</span>
          <em className={styles.fieldHint}>
            {t("LdapTab.useStartTlsHint")}
          </em>
        </label>
      </div>

      <div className={styles.card}>
        <h2 className={styles.cardTitle}>{t("LdapTab.serviceAccountSectionTitle")}</h2>
        <p className={styles.cardDesc}>{t("LdapTab.serviceAccountSectionDesc")}</p>
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span>{t("LdapTab.bindDn")}</span>
            <input
              value={form.bind_dn}
              onChange={(e) => setField("bind_dn", e.target.value)}
              placeholder="CN=svc-skylab,OU=Service,DC=example,DC=edu"
            />
          </label>
          <label className={styles.field}>
            <span>{t("LdapTab.bindPassword")}</span>
            <input
              type="password"
              value={form.bind_password}
              onChange={(e) => setField("bind_password", e.target.value)}
              placeholder={config?.bind_password_set ? t("LdapTab.bindPasswordSetPlaceholder") : t("LdapTab.bindPasswordEnterPlaceholder")}
            />
          </label>
          <label className={styles.field}>
            <span>{t("LdapTab.userSearchBase")}</span>
            <input
              value={form.user_search_base}
              onChange={(e) => setField("user_search_base", e.target.value)}
              placeholder="OU=Users,DC=example,DC=edu"
            />
          </label>
          <label className={styles.field}>
            <span>{t("LdapTab.userFilterTemplate")}</span>
            <input
              value={form.user_filter_template}
              onChange={(e) => setField("user_filter_template", e.target.value)}
              placeholder={t("LdapTab.userFilterTemplatePlaceholder")}
            />
            <em className={styles.fieldHint}>{t("LdapTab.userFilterTemplateHint")}</em>
          </label>
          <label className={styles.field}>
            <span>{t("LdapTab.emailAttribute")}</span>
            <input
              value={form.email_attribute}
              onChange={(e) => setField("email_attribute", e.target.value)}
              placeholder="mail"
            />
          </label>
          <label className={styles.field}>
            <span>{t("LdapTab.nameAttribute")}</span>
            <input
              value={form.name_attribute}
              onChange={(e) => setField("name_attribute", e.target.value)}
              placeholder="displayName"
            />
          </label>
        </div>
      </div>

      <div className={styles.card}>
        <h2 className={styles.cardTitle}>{t("LdapTab.roleMappingSectionTitle")}</h2>
        <p className={styles.cardDesc}>
          {t("LdapTab.roleMappingSectionDesc")}
        </p>
        <label className={styles.checkRow}>
          <input
            type="checkbox"
            checked={Boolean(form.auto_create_users)}
            onChange={(e) => setField("auto_create_users", e.target.checked)}
          />
          <span>{t("LdapTab.autoCreateUsers")}</span>
          <em className={styles.fieldHint}>{t("LdapTab.autoCreateUsersHint")}</em>
        </label>
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span>{t("LdapTab.teacherGroupDn")}</span>
            <input
              value={form.teacher_group_dn}
              onChange={(e) => setField("teacher_group_dn", e.target.value)}
              placeholder="CN=Teachers,OU=Groups,DC=example,DC=edu"
            />
            <em className={styles.fieldHint}>{t("LdapTab.teacherGroupDnHint")}</em>
          </label>
          <label className={styles.field}>
            <span>{t("LdapTab.adminGroupDn")}</span>
            <input
              value={form.admin_group_dn}
              onChange={(e) => setField("admin_group_dn", e.target.value)}
              placeholder="CN=SkyLabAdmins,OU=Groups,DC=example,DC=edu"
            />
            <em className={styles.fieldHint}>{t("LdapTab.adminGroupDnHint")}</em>
          </label>
        </div>
      </div>

      <div className={styles.saveBar}>
        {dirty && (
          <button
            type="button"
            className={styles.btnSecondary}
            disabled={saving}
            onClick={() => setForm(buildForm(config))}
          >
            {t("SettingsPage.restore")}
          </button>
        )}
        <button
          type="button"
          className={styles.btnSecondary}
          onClick={handleTest}
          disabled={testing}
        >
          <MIcon name="wifi_tethering" size={16} />
          {testing ? t("LdapTab.testing") : t("LdapTab.testConnection")}
        </button>
        <button type="submit" className={styles.btnPrimary} disabled={!dirty || saving}>
          {saving ? t("LdapTab.saving") : t("LdapTab.saveConfig")}
        </button>
      </div>
    </form>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function LdapPage() {
  const { t } = useTranslation("system");
  return (
    <div className={styles.page}>
      <PageHeader title={t("SettingsPage.ldapTitle")} subtitle={t("SettingsPage.ldapSubtitle")} />
      <div className={styles.content}>
        <LdapForm />
      </div>
    </div>
  );
}
