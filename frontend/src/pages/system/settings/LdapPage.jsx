import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./settings.module.scss";
import MIcon from "../../../components/MIcon";
import ErrorState from "../../../components/ErrorState/ErrorState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { LdapConfigService } from "../../../services/ldapConfig";
import { useLdapConfigCacheUpdater, useLdapConfigQuery } from "../../../queries/ldapConfig";
import { useToast } from "../../../hooks/useToast";
import { useLoadError, useQueryErrorEffect } from "../../../hooks/useQueryLoadState";
import { useUnsavedChangesGuard } from "../../../contexts/UnsavedChangesContext";

/**
 * LDAP（系統管理 → LDAP）：LDAP / Active Directory 登入的連線、服務帳號與角色對映。
 * 2026-09 從「系統設定」的分頁拆成獨立頁面。
 */

/**
 * 表單值 → API partial payload（bind_password 留空表示不變更）。
 * 群組 DN 清空時送空字串而不是 null：後端會略過 null 欄位（等於不變更），
 * 舊群組就會繼續被對映成老師／管理員；空字串會被存下，角色對映視為未設定。
 */
export function toPayload(form) {
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
    teacher_group_dn: (form.teacher_group_dn ?? "").trim(),
    admin_group_dn: (form.admin_group_dn ?? "").trim(),
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
  const [form, setForm] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const dirty = Boolean(form && baseline && Object.keys(form).some((key) => form[key] !== baseline[key]));
  useUnsavedChangesGuard(dirty);

  /* 設定走 TanStack Query 讀（不輪詢、不因切回分頁重抓）；表單只在第一次拿到資料時初始化，
     之後的背景重抓不會蓋掉正在編輯的欄位。載入失敗要看得見：改渲染重試區塊，不要停在轉圈畫面 */
  const configQuery = useLdapConfigQuery();
  const ldapCache = useLdapConfigCacheUpdater();
  /* bind 密碼欄的 placeholder 看伺服器是否已存密碼；存檔後 setConfig 會換成新回應 */
  const config = configQuery.data;
  /* 進頁時快取若已過期會重抓：那次重抓回來前不初始化表單（維持載入中）。不然表單會先填快取裡的舊設定，
     之後的新資料又被「只初始化一次」略過，存檔時把別的管理員剛改的設定整份蓋回去。
     重抓失敗時仍用手上的資料；5 秒內回到頁面不會重抓，快取就夠新 */
  const configReady = configQuery.data !== undefined && !(configQuery.isFetching && !configQuery.isFetchedAfterMount);
  useEffect(() => {
    if (baseline !== null || !configReady) return;
    const next = buildForm(configQuery.data);
    setForm(next);
    setBaseline(next);
  }, [baseline, configReady, configQuery.data]);

  /* 每次載入失敗提示一次：重抓時 error 會先清空再變回來，不能拿 error 物件當觸發條件 */
  useQueryErrorEffect(configQuery, (err) => {
    toast.error(err?.message ?? t("Error.generic", { ns: "common" }));
  });

  /* 重試區塊在重抓期間維持、不閃回轉圈，按重試才回到載入中（見 useLoadError） */
  const { error: loadError, retry: retryLoad } = useLoadError(configQuery);

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await LdapConfigService.update(toPayload(form));
      ldapCache.setConfig(updated);
      const next = buildForm(updated);
      setForm(next);
      setBaseline(next);
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

  if (!form && loadError) {
    return <ErrorState onRetry={() => retryLoad()} />;
  }
  if (!form) return <LoadingState text={t("LdapTab.loading")} />;

  return (
    <form className={styles.panelStack} onSubmit={handleSave}>
      <div className={styles.card} data-guide="ldap-login">
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

      <div className={styles.card} data-guide="ldap-service-account">
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

      <div className={styles.card} data-guide="ldap-role-mapping">
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

      <div className={styles.saveBar} data-guide="ldap-actions">
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
      <PageHeader title={t("SettingsPage.ldapTitle")} />
      <div className={styles.content}>
        <LdapForm />
      </div>
    </div>
  );
}
