import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import styles from "./settings.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { ProxmoxConfigService } from "../../../services/proxmoxConfig";
import PageHeader from "../../../components/PageHeader/PageHeader";

/**
 * PVE 連線（系統管理 → PVE 連線）：每筆連線是一個獨立的 PVE 入口（單台或叢集），
 * 該叢集自己的資源設定（pool / storage / 網段 / 預設節點）都在新增·編輯表單裡。
 * 2026-09 從「系統設定」的分頁拆成獨立頁面；舊網址 /settings 會導向這裡。
 */

/* ── PVE 多連線管理 ─────────────────────────────────── */
const EMPTY_CONNECTION_FORM = {
  name: "",
  host: "",
  port: 8006,
  user: "root@pam",
  password: "",
  verify_ssl: false,
  ca_cert: "",
  api_timeout: 30,
  pool_name: "SkyLab",
  iso_storage: "local",
  data_storage: "local-lvm",
  task_check_interval: 2,
  gateway_ip: "",
  local_subnet: "",
  default_node: "",
  enabled: true,
  is_default: false,
};

/** 編輯既有連線時，把 API 回傳的連線資料轉成表單狀態 */
function connectionToForm(conn) {
  return {
    ...EMPTY_CONNECTION_FORM,
    name: conn.name,
    host: conn.host,
    port: conn.port,
    user: conn.user,
    verify_ssl: conn.verify_ssl,
    api_timeout: conn.api_timeout,
    pool_name: conn.pool_name ?? "",
    iso_storage: conn.iso_storage ?? "",
    data_storage: conn.data_storage ?? "",
    task_check_interval: conn.task_check_interval ?? 2,
    gateway_ip: conn.gateway_ip ?? "",
    local_subnet: conn.local_subnet ?? "",
    default_node: conn.default_node ?? "",
    enabled: conn.enabled,
    is_default: conn.is_default,
  };
}

function ConnectionForm({ initial, isEdit, saving, closing = false, onSubmit, onCancel }) {
  const { t } = useTranslation("system");
  const [form, setForm] = useState(initial);
  const set = (name, value) => setForm((prev) => ({ ...prev, [name]: value }));

  function handleSubmit(e) {
    e.preventDefault();
    const payload = {
      name: form.name.trim(),
      host: form.host.trim(),
      port: Number(form.port) || 8006,
      user: form.user.trim(),
      verify_ssl: Boolean(form.verify_ssl),
      api_timeout: Number(form.api_timeout) || 30,
      pool_name: form.pool_name.trim() || "SkyLab",
      iso_storage: form.iso_storage.trim() || "local",
      data_storage: form.data_storage.trim() || "local-lvm",
      task_check_interval: Number(form.task_check_interval) || 2,
      gateway_ip: form.gateway_ip.trim() || null,
      local_subnet: form.local_subnet.trim() || null,
      default_node: form.default_node.trim() || null,
      enabled: Boolean(form.enabled),
      is_default: Boolean(form.is_default),
    };
    if (isEdit) {
      payload.password = form.password ? form.password : null;
      payload.ca_cert = form.ca_cert?.trim() ? form.ca_cert.trim() : null;
    } else {
      payload.password = form.password;
      if (form.ca_cert?.trim()) payload.ca_cert = form.ca_cert.trim();
    }
    onSubmit(payload);
  }

  return (
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onMouseDown={onCancel}
    >
    <form className={styles.modal} onSubmit={handleSubmit} onMouseDown={(e) => e.stopPropagation()}>
      <span className={styles.modalTitle}>
        <MIcon name="device_hub" size={18} />
        {isEdit ? t("SettingsPage.editConnection") : t("SettingsPage.addConnection")}
      </span>
      <h3 className={styles.sectionTitle}>{t("SettingsPage.connectionSettingsTitle")}</h3>
      <div className={styles.modalFormGrid}>
        <label className={styles.field}>
          <span>{t("SettingsPage.connectionName")}</span>
          <input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder={t("SettingsPage.connectionNamePlaceholder")} required />
        </label>
        <label className={styles.field}>
          <span>Host *</span>
          <input value={form.host} onChange={(e) => set("host", e.target.value)} placeholder={t("SettingsPage.hostPlaceholder")} required />
        </label>
        <label className={styles.field}>
          <span>Port</span>
          <input type="number" min={1} max={65535} value={form.port} onChange={(e) => set("port", e.target.value)} />
        </label>
        <label className={styles.field}>
          <span>{t("SettingsPage.apiUser")}</span>
          <input value={form.user} onChange={(e) => set("user", e.target.value)} placeholder="root@pam" required />
        </label>
        <label className={styles.field}>
          <span>{t("SettingsPage.password")}{isEdit ? t("SettingsPage.leaveBlankUnchanged") : " *"}</span>
          <input
            type="password"
            value={form.password}
            onChange={(e) => set("password", e.target.value)}
            placeholder={isEdit ? t("SettingsPage.passwordSetPlaceholder") : t("SettingsPage.pvePasswordPlaceholder")}
            required={!isEdit}
          />
        </label>
        <label className={styles.field}>
          <span>{t("SettingsPage.apiTimeoutSeconds")}</span>
          <input type="number" min={1} max={300} value={form.api_timeout} onChange={(e) => set("api_timeout", e.target.value)} />
        </label>
      </div>
      <label className={styles.checkRow}>
        <input type="checkbox" checked={Boolean(form.verify_ssl)} onChange={(e) => set("verify_ssl", e.target.checked)} />
        <span>{t("SettingsPage.verifySslCert")}</span>
      </label>
      {form.verify_ssl && (
        <label className={styles.field}>
          <span>{t("SettingsPage.caCertPem")}{isEdit ? t("SettingsPage.leaveBlankUnchanged") : ""}</span>
          <textarea
            rows={5}
            value={form.ca_cert}
            onChange={(e) => set("ca_cert", e.target.value)}
            placeholder="-----BEGIN CERTIFICATE-----"
            spellCheck={false}
          />
        </label>
      )}

      <h3 className={styles.sectionTitle}>{t("SettingsPage.clusterResourceSettingsTitle")}</h3>
      <p className={styles.cardDesc}>
        {t("SettingsPage.clusterResourceSettingsDesc")}
      </p>
      <div className={styles.modalFormGrid}>
        <label className={styles.field}>
          <span>{t("SettingsPage.poolName")}</span>
          <input value={form.pool_name} onChange={(e) => set("pool_name", e.target.value)} placeholder="SkyLab" />
        </label>
        <label className={styles.field}>
          <span>ISO Storage</span>
          <input value={form.iso_storage} onChange={(e) => set("iso_storage", e.target.value)} placeholder="local" />
        </label>
        <label className={styles.field}>
          <span>Data Storage</span>
          <input value={form.data_storage} onChange={(e) => set("data_storage", e.target.value)} placeholder="local-lvm" />
        </label>
        <label className={styles.field}>
          <span>{t("SettingsPage.taskCheckInterval")}</span>
          <input
            type="number"
            min={1}
            max={60}
            value={form.task_check_interval}
            onChange={(e) => set("task_check_interval", e.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span>Gateway IP</span>
          <input value={form.gateway_ip} onChange={(e) => set("gateway_ip", e.target.value)} placeholder={t("SettingsPage.optional")} />
        </label>
        <label className={styles.field}>
          <span>{t("SettingsPage.localSubnet")}</span>
          <input value={form.local_subnet} onChange={(e) => set("local_subnet", e.target.value)} placeholder={t("SettingsPage.localSubnetPlaceholder")} />
        </label>
        <label className={styles.field}>
          <span>{t("SettingsPage.defaultNode")}</span>
          <input value={form.default_node} onChange={(e) => set("default_node", e.target.value)} placeholder={t("SettingsPage.defaultNodePlaceholder")} />
        </label>
      </div>

      <div className={styles.toggleGrid}>
        <label className={styles.checkRow}>
          <input type="checkbox" checked={Boolean(form.enabled)} onChange={(e) => set("enabled", e.target.checked)} />
          <span>{t("SettingsPage.enableThisConnection")}</span>
        </label>
        <label className={styles.checkRow}>
          <input type="checkbox" checked={Boolean(form.is_default)} onChange={(e) => set("is_default", e.target.checked)} />
          <span>{t("SettingsPage.setAsDefaultConnection")}</span>
        </label>
      </div>
      <div className={styles.modalActions}>
        <button type="button" className={styles.btnSecondary} onClick={onCancel}>{t("SettingsPage.cancel")}</button>
        <button type="submit" className={styles.btnPrimary} disabled={saving}>
          {saving ? t("SettingsPage.saving") : t("SettingsPage.saveConnection")}
        </button>
      </div>
    </form>
    </div>
  );
}

function ConnectionsSection({ connections, loading, onRefresh }) {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  const [editing, setEditing] = useState(null); // null | "new" | connection 物件
  const editPresence = useDialogPresence(editing);
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState(null);

  async function handleSubmit(payload) {
    setSaving(true);
    try {
      if (editing === "new") {
        await ProxmoxConfigService.createConnection(payload);
        toast.success(t("SettingsPage.toastConnectionAdded"));
      } else {
        await ProxmoxConfigService.updateConnection(editing.id, payload);
        toast.success(t("SettingsPage.toastConnectionUpdated"));
      }
      setEditing(null);
      onRefresh();
    } catch (err) {
      toast.error(err?.message ?? t("SettingsPage.toastSaveConnectionFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(conn) {
    const ok = await confirm({
      title: t("SettingsPage.deleteConnectionTitle"),
      message: t("SettingsPage.deleteConnectionMessage", { name: conn.name }),
      confirmText: t("SettingsPage.delete"),
      danger: true,
    });
    if (!ok) return;
    setBusyId(conn.id);
    try {
      await ProxmoxConfigService.deleteConnection(conn.id);
      toast.success(t("SettingsPage.toastConnectionDeleted"));
      onRefresh();
    } catch (err) {
      toast.error(err?.message ?? t("SettingsPage.toastDeleteConnectionFailed"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleTest(conn) {
    setBusyId(conn.id);
    try {
      const res = await ProxmoxConfigService.testConnectionById(conn.id);
      if (res.success) toast.success(res.message || t("SettingsPage.toastConnectSuccess"));
      else toast.error(res.message || t("SettingsPage.toastConnectFailed"));
    } catch (err) {
      toast.error(err?.message ?? t("SettingsPage.toastConnectTestFailed"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleSync(conn) {
    setBusyId(conn.id);
    try {
      const res = await ProxmoxConfigService.syncConnection(conn.id);
      if (res.success) {
        toast.success(t("SettingsPage.toastSyncComplete", { nodes: res.nodes?.length ?? 0, storage: res.storage_count ?? 0 }));
        onRefresh();
      } else {
        toast.error(res.error || t("SettingsPage.toastSyncFailed"));
      }
    } catch (err) {
      toast.error(err?.message ?? t("SettingsPage.toastSyncFailed"));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className={styles.panelStack}>
      <div className={styles.card}>
        <div className={styles.cardHead}>
          <h2 className={styles.cardTitle}>{t("SettingsPage.connectionsListTitle")}</h2>
          <button type="button" className={styles.btnSecondary} onClick={() => setEditing("new")}>
            <MIcon name="add" size={16} />
            {t("SettingsPage.addConnection")}
          </button>
        </div>
        {loading ? (
          <LoadingState text={t("SettingsPage.loadingConnections")} />
        ) : connections.length === 0 ? (
          <p className={styles.cardDesc}>
            {t("SettingsPage.noConnectionsYet")}
          </p>
        ) : (
          <div className={styles.list}>
            {connections.map((conn) => (
              <div key={conn.id} className={styles.nodeRow}>
                <div className={styles.rowMain}>
                  <span className={styles.rowName}>
                    {conn.name}
                    {conn.is_default && <span className={`${styles.badge} ${styles.badge_info}`}>{t("SettingsPage.default")}</span>}
                    {!conn.enabled && <span className={`${styles.badge} ${styles.badge_danger}`}>{t("SettingsPage.disabled")}</span>}
                  </span>
                  <span className={styles.rowMeta}>
                    {conn.host}:{conn.port} · {conn.user} · {t("SettingsPage.nodeCount", { count: conn.node_count })}
                  </span>
                </div>
                <button type="button" className={styles.btnSecondary} disabled={busyId === conn.id} onClick={() => handleTest(conn)}>
                  <MIcon name="wifi_tethering" size={16} />
                  {t("SettingsPage.test")}
                </button>
                <button type="button" className={styles.btnSecondary} disabled={busyId === conn.id} onClick={() => handleSync(conn)}>
                  <MIcon name="sync" size={16} />
                  {t("SettingsPage.sync")}
                </button>
                <button type="button" className={styles.btnSecondary} disabled={busyId === conn.id} onClick={() => setEditing(conn)}>
                  <MIcon name="edit" size={16} />
                  {t("SettingsPage.edit")}
                </button>
                <button type="button" className={styles.btnSecondary} disabled={busyId === conn.id} onClick={() => handleDelete(conn)}>
                  <MIcon name="delete" size={16} />
                  {t("SettingsPage.delete")}
                </button>
              </div>
            ))}
          </div>
        )}
        <p className={styles.cardHint}>
          {t("SettingsPage.nodeMetricsHintPrefix")}{" "}
          <Link to="/monitoring" className={styles.inlineLink}>{t("SettingsPage.nodeMetricsHintLink")}</Link>
          {" "}{t("SettingsPage.nodeMetricsHintSuffix")}
        </p>
      </div>

      {editPresence.open && (
        <ConnectionForm
          key={editPresence.item === "new" ? "new" : editPresence.item.id}
          isEdit={editPresence.item !== "new"}
          saving={saving}
          closing={editPresence.closing}
          initial={
            editPresence.item === "new" ? EMPTY_CONNECTION_FORM : connectionToForm(editPresence.item)
          }
          onSubmit={handleSubmit}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  );
}

/* ── Page ──────────────────────────────────────────── */
export default function PveConnectionsPage() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchConnections = useCallback(() => {
    setLoading(true);
    ProxmoxConfigService.listConnections()
      .then(setConnections)
      .catch((err) => toast.error(err?.message ?? t("SettingsPage.toastLoadConnectionsFailed")))
      .finally(() => setLoading(false));
  }, [toast, t]);

  useEffect(() => {
    fetchConnections();
  }, [fetchConnections]);

  return (
    <div className={styles.page}>
      <PageHeader title={t("SettingsPage.pveConnectionsTitle")} subtitle={t("SettingsPage.pveConnectionsSubtitle")} />
      <div className={styles.content}>
        <ConnectionsSection connections={connections} loading={loading} onRefresh={fetchConnections} />
      </div>
    </div>
  );
}
