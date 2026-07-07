import { useEffect, useState } from "react";
import styles from "./SettingsPage.module.scss";
import MIcon from "../../../components/MIcon";
import { LdapConfigService } from "../../../services/ldapConfig";
import { useToast } from "../../../hooks/useToast";

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

export default function LdapTab() {
  const toast = useToast();
  const [config, setConfig] = useState(null);
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    LdapConfigService.get()
      .then((cfg) => {
        if (cancelled) return;
        setConfig(cfg);
        setForm(buildForm(cfg));
      })
      .catch((err) => toast.error(err?.message ?? "載入 LDAP 設定失敗"));
    return () => {
      cancelled = true;
    };
  }, [toast]);

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await LdapConfigService.update(toPayload(form));
      setConfig(updated);
      setForm(buildForm(updated));
      toast.success("LDAP 設定已儲存");
    } catch (err) {
      toast.error(`儲存失敗：${err?.message ?? "未知錯誤"}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    try {
      const result = await LdapConfigService.test(toPayload(form));
      if (result.ok) toast.success(result.message || "LDAP 連線測試成功");
      else toast.error(result.message || "LDAP 連線測試失敗");
    } catch (err) {
      toast.error(`測試失敗：${err?.message ?? "未知錯誤"}`);
    } finally {
      setTesting(false);
    }
  }

  if (!form) return <div className={styles.loading}>載入 LDAP 設定...</div>;

  return (
    <form className={styles.panelStack} onSubmit={handleSave}>
      <div className={styles.card}>
        <h2 className={styles.cardTitle}>LDAP / Active Directory 登入</h2>
        <p className={styles.cardDesc}>
          啟用後登入頁會顯示「校園帳號」分頁，以校方目錄帳號驗證登入。
        </p>
        <label className={styles.checkRow}>
          <input
            type="checkbox"
            checked={Boolean(form.enabled)}
            onChange={(e) => setField("enabled", e.target.checked)}
          />
          <span>啟用 LDAP 登入</span>
          <em className={styles.fieldHint}>啟用前建議先以「測試連線」驗證設定</em>
        </label>
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span>伺服器 URI *</span>
            <input
              value={form.server_uri}
              onChange={(e) => setField("server_uri", e.target.value)}
              placeholder="ldap://dc.example.edu:389 或 ldaps://..."
              required
            />
          </label>
          <label className={styles.field}>
            <span>連線逾時（秒）</span>
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
          <span>使用 StartTLS</span>
          <em className={styles.fieldHint}>
            在 ldap:// 連線上升級為加密連線（ldaps:// 不需要）
          </em>
        </label>
      </div>

      <div className={styles.card}>
        <h2 className={styles.cardTitle}>服務帳號與使用者搜尋</h2>
        <p className={styles.cardDesc}>以服務帳號 bind 後搜尋使用者，再以使用者密碼驗證。</p>
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span>Bind DN</span>
            <input
              value={form.bind_dn}
              onChange={(e) => setField("bind_dn", e.target.value)}
              placeholder="CN=svc-skylab,OU=Service,DC=example,DC=edu"
            />
          </label>
          <label className={styles.field}>
            <span>Bind 密碼</span>
            <input
              type="password"
              value={form.bind_password}
              onChange={(e) => setField("bind_password", e.target.value)}
              placeholder={config?.bind_password_set ? "已設定（留空表示不變）" : "輸入服務帳號密碼"}
            />
          </label>
          <label className={styles.field}>
            <span>使用者搜尋 Base DN</span>
            <input
              value={form.user_search_base}
              onChange={(e) => setField("user_search_base", e.target.value)}
              placeholder="OU=Users,DC=example,DC=edu"
            />
          </label>
          <label className={styles.field}>
            <span>使用者過濾範本</span>
            <input
              value={form.user_filter_template}
              onChange={(e) => setField("user_filter_template", e.target.value)}
              placeholder="(sAMAccountName={username}) 或 (uid={username})"
            />
            <em className={styles.fieldHint}>{"{username}"} 會代入登入時輸入的帳號</em>
          </label>
          <label className={styles.field}>
            <span>Email 屬性</span>
            <input
              value={form.email_attribute}
              onChange={(e) => setField("email_attribute", e.target.value)}
              placeholder="mail"
            />
          </label>
          <label className={styles.field}>
            <span>姓名屬性</span>
            <input
              value={form.name_attribute}
              onChange={(e) => setField("name_attribute", e.target.value)}
              placeholder="displayName"
            />
          </label>
        </div>
      </div>

      <div className={styles.card}>
        <h2 className={styles.cardTitle}>帳號建立與角色對映</h2>
        <p className={styles.cardDesc}>
          首次登入自動建立帳號（預設 student），依群組 DN 對映角色。
        </p>
        <label className={styles.checkRow}>
          <input
            type="checkbox"
            checked={Boolean(form.auto_create_users)}
            onChange={(e) => setField("auto_create_users", e.target.checked)}
          />
          <span>自動建立帳號</span>
          <em className={styles.fieldHint}>關閉後僅已存在的本地帳號可用 LDAP 登入</em>
        </label>
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span>教師群組 DN（選填）</span>
            <input
              value={form.teacher_group_dn}
              onChange={(e) => setField("teacher_group_dn", e.target.value)}
              placeholder="CN=Teachers,OU=Groups,DC=example,DC=edu"
            />
            <em className={styles.fieldHint}>使用者屬於此群組時建立為 teacher 角色</em>
          </label>
          <label className={styles.field}>
            <span>管理員群組 DN（選填）</span>
            <input
              value={form.admin_group_dn}
              onChange={(e) => setField("admin_group_dn", e.target.value)}
              placeholder="CN=SkyLabAdmins,OU=Groups,DC=example,DC=edu"
            />
            <em className={styles.fieldHint}>使用者屬於此群組時建立為 admin 角色</em>
          </label>
        </div>
      </div>

      <div className={styles.cardActions}>
        <button
          type="button"
          className={styles.btnSecondary}
          onClick={handleTest}
          disabled={testing}
        >
          <MIcon name="wifi_tethering" size={16} />
          {testing ? "測試中..." : "測試連線"}
        </button>
        <button type="submit" className={styles.btnPrimary} disabled={saving}>
          {saving ? "儲存中..." : "儲存 LDAP 設定"}
        </button>
      </div>
    </form>
  );
}
