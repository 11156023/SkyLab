import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import styles from "./DomainPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { CloudflareService } from "../../../services/cloudflare";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { ReverseProxyPanel } from "../../network/reverse-proxy/ReverseProxyPage";
import { formatDateTime } from "../../../utils/formatDate";

const TAB_KEYS = ["dns", "reverse-proxy"];

const DNS_TYPES = ["A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV"];

/* ── 供應商設定 Modal ───────────────────────────────────── */

function ConfigModal({ config, loading, closing = false, onClose, onSubmit }) {
  const { t } = useTranslation("system");
  const [form, setForm] = useState({
    account_id: config?.account_id ?? "",
    api_token: "",
    default_dns_target_type: config?.default_dns_target_type ?? "",
    default_dns_target_value: config?.default_dns_target_value ?? "",
  });

  function set(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  function submit(e) {
    e.preventDefault();
    const body = {
      account_id: form.account_id.trim() || null,
      default_dns_target_type: form.default_dns_target_type.trim() || null,
      default_dns_target_value: form.default_dns_target_value.trim() || null,
    };
    if (form.api_token.trim()) body.api_token = form.api_token.trim();
    onSubmit(body);
  }

  return (
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onMouseDown={onClose}
    >
      <form className={styles.modal} onSubmit={submit} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>{t("DomainPage.configModalTitle")}</h2>
            <p>{t("DomainPage.configModalDesc")}</p>
            <p className={styles.modalGuide}>
              {t("DomainPage.tokenGuideText")}{" "}
              <a href="https://dash.cloudflare.com/profile/api-tokens" target="_blank" rel="noreferrer">
                {t("DomainPage.tokenGuideLink")}
                <MIcon name="open_in_new" size={13} />
              </a>
            </p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label={t("DomainPage.close")}>
            <MIcon name="close" size={18} />
          </button>
        </div>

        <label className={styles.field}>
          <span>Account ID</span>
          <input
            value={form.account_id}
            onChange={(e) => set("account_id", e.target.value)}
            placeholder="Cloudflare Account ID"
          />
        </label>

        <label className={styles.field}>
          <span>API Token{config?.has_api_token ? t("DomainPage.leaveBlankUnchanged") : " *"}</span>
          <input
            type="password"
            value={form.api_token}
            onChange={(e) => set("api_token", e.target.value)}
            placeholder={config?.has_api_token ? t("DomainPage.apiTokenSetPlaceholder") : t("DomainPage.apiTokenPastePlaceholder")}
            required={!config?.has_api_token}
          />
        </label>

        <div className={styles.fieldRow}>
          <label className={styles.field}>
            <span>{t("DomainPage.defaultDnsTargetType")}</span>
            <select
              value={form.default_dns_target_type}
              onChange={(e) => set("default_dns_target_type", e.target.value)}
            >
              <option value="">{t("DomainPage.notSet")}</option>
              <option value="A">{t("DomainPage.dnsTypeA")}</option>
              <option value="CNAME">{t("DomainPage.dnsTypeCname")}</option>
            </select>
          </label>
          <label className={styles.field}>
            <span>{t("DomainPage.defaultDnsTargetValue")}</span>
            <input
              value={form.default_dns_target_value}
              onChange={(e) => set("default_dns_target_value", e.target.value)}
              placeholder={t("DomainPage.dnsTargetValuePlaceholder")}
            />
          </label>
        </div>
        <p className={styles.fieldHint}>{t("DomainPage.dnsTargetHint")}</p>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
            {t("DomainPage.cancel")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading}>
            {loading ? t("DomainPage.saving") : t("DomainPage.save")}
          </button>
        </div>
      </form>
    </div>
  );
}

/* ── DNS record 編輯 Modal ─────────────────────────────── */

function RecordModal({ record, loading, closing = false, onClose, onSubmit }) {
  const { t } = useTranslation("system");
  const isEdit = Boolean(record);
  const [form, setForm] = useState({
    type: record?.type ?? "A",
    name: record?.name ?? "",
    content: record?.content ?? "",
    ttl: record?.ttl ?? 1,
    proxied: record?.proxied ?? false,
    comment: record?.comment ?? "",
  });

  function set(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  function submit(e) {
    e.preventDefault();
    const body = {
      type: form.type,
      name: form.name.trim(),
      content: form.content.trim(),
      ttl: Number(form.ttl) || 1,
      proxied: form.proxied,
    };
    if (form.comment.trim()) body.comment = form.comment.trim();
    onSubmit(body);
  }

  return (
    <div
      className={`${styles.modalOverlay} ${closing ? styles.modalOverlayOut : ""}`}
      onMouseDown={onClose}
    >
      <form className={styles.modal} onSubmit={submit} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>{isEdit ? t("DomainPage.recordModalEditTitle") : t("DomainPage.recordModalCreateTitle")}</h2>
            <p>{t("DomainPage.ttlHint")}</p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label={t("DomainPage.close")}>
            <MIcon name="close" size={18} />
          </button>
        </div>

        <div className={styles.fieldRow}>
          <label className={styles.field}>
            <span>{t("DomainPage.recordType")}</span>
            <select value={form.type} onChange={(e) => set("type", e.target.value)}>
              {DNS_TYPES.map((type) => (
                <option key={type} value={type}>{type}</option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span>TTL</span>
            <input
              type="number"
              min={1}
              value={form.ttl}
              onChange={(e) => set("ttl", e.target.value)}
            />
          </label>
        </div>

        <label className={styles.field}>
          <span>{t("DomainPage.recordName")}</span>
          <input
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder={t("DomainPage.recordNamePlaceholder")}
            required
          />
        </label>

        <label className={styles.field}>
          <span>{t("DomainPage.recordContent")}</span>
          <input
            value={form.content}
            onChange={(e) => set("content", e.target.value)}
            placeholder={t("DomainPage.recordContentPlaceholder")}
            required
          />
        </label>

        <label className={styles.field}>
          <span>{t("DomainPage.recordComment")}</span>
          <input
            value={form.comment}
            onChange={(e) => set("comment", e.target.value)}
            placeholder={t("DomainPage.optional")}
          />
        </label>

        <label className={styles.checkRow}>
          <input
            type="checkbox"
            checked={form.proxied}
            onChange={(e) => set("proxied", e.target.checked)}
          />
          <span>{t("DomainPage.proxiedLabel")}</span>
        </label>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
            {t("DomainPage.cancel")}
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading}>
            {loading ? t("DomainPage.saving") : t("DomainPage.save")}
          </button>
        </div>
      </form>
    </div>
  );
}

/* ── 主頁 ───────────────────────────────────────────────── */

export default function DomainPage() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const [config, setConfig] = useState(null);
  const [zones, setZones] = useState([]);
  const [selectedZone, setSelectedZone] = useState(null);
  const [records, setRecords] = useState([]);
  const [loadingZones, setLoadingZones] = useState(true);
  const [loadingRecords, setLoadingRecords] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState("");
  const [modal, setModal] = useState(null); // { kind: "config" } | { kind: "record", record? } | { kind: "deleteRecord", record }
  const modalPresence = useDialogPresence(modal);

  // 分頁狀態放在網址 ?tab=，讓 /domain?tab=reverse-proxy 這類連結（舊反向代理頁）能直接開到指定分頁
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab");
  const activeTab = TAB_KEYS.includes(requestedTab) ? requestedTab : TAB_KEYS[0];
  const selectTab = (key) => {
    const next = new URLSearchParams(searchParams);
    if (key === TAB_KEYS[0]) next.delete("tab");
    else next.set("tab", key);
    setSearchParams(next, { replace: true });
  };

  const fetchConfig = useCallback(async () => {
    try {
      setConfig(await CloudflareService.getConfig());
    } catch (err) {
      toast.error(err?.message ?? t("DomainPage.toastLoadConfigFailed"));
    }
  }, [toast, t]);

  const fetchZones = useCallback(async () => {
    setLoadingZones(true);
    try {
      const res = await CloudflareService.listZones({ per_page: 50 });
      const items = res?.items ?? [];
      setZones(items);
      setSelectedZone((prev) => prev ?? items[0] ?? null);
    } catch (err) {
      // 未設定連線時後端會回錯誤，front 只顯示空狀態
      if (err?.status !== 400) toast.error(err?.message ?? t("DomainPage.toastLoadZonesFailed"));
    } finally {
      setLoadingZones(false);
    }
  }, [toast, t]);

  const fetchRecords = useCallback(async (zoneId, keyword) => {
    setLoadingRecords(true);
    try {
      const res = await CloudflareService.listDnsRecords(zoneId, {
        per_page: 100,
        search: keyword || undefined,
      });
      setRecords(res?.items ?? []);
    } catch (err) {
      toast.error(err?.message ?? t("DomainPage.toastLoadRecordsFailed"));
    } finally {
      setLoadingRecords(false);
    }
  }, [toast, t]);

  useEffect(() => {
    fetchConfig();
    fetchZones();
  }, [fetchConfig, fetchZones]);

  useEffect(() => {
    if (selectedZone) fetchRecords(selectedZone.id, "");
    setSearch("");
  }, [selectedZone, fetchRecords]);

  async function handleSaveConfig(body) {
    setSaving(true);
    try {
      const updated = await CloudflareService.updateConfig(body);
      setConfig(updated);
      toast.success(t("DomainPage.toastConfigSaved"));
      setModal(null);
      fetchZones();
    } catch (err) {
      toast.error(err?.message ?? t("DomainPage.toastSaveConfigFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    try {
      const res = await CloudflareService.testConfig();
      if (res.success) toast.success(res.message || t("DomainPage.toastConnectSuccess"));
      else toast.error(res.message || t("DomainPage.toastConnectFailed"));
      fetchConfig();
    } catch (err) {
      toast.error(err?.message ?? t("DomainPage.toastConnectTestFailed"));
    } finally {
      setTesting(false);
    }
  }

  async function handleSaveRecord(body) {
    if (!selectedZone) return;
    setSaving(true);
    try {
      if (modal?.record) {
        await CloudflareService.updateDnsRecord(selectedZone.id, modal.record.id, body);
        toast.success(t("DomainPage.toastRecordUpdated"));
      } else {
        await CloudflareService.createDnsRecord(selectedZone.id, body);
        toast.success(t("DomainPage.toastRecordCreated"));
      }
      setModal(null);
      fetchRecords(selectedZone.id, search);
    } catch (err) {
      toast.error(err?.message ?? t("DomainPage.toastSaveRecordFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteRecord() {
    if (!selectedZone || !modal?.record) return;
    setSaving(true);
    try {
      await CloudflareService.deleteDnsRecord(selectedZone.id, modal.record.id);
      toast.success(t("DomainPage.toastRecordDeleted"));
      setModal(null);
      fetchRecords(selectedZone.id, search);
    } catch (err) {
      toast.error(err?.message ?? t("DomainPage.toastDeleteFailed"));
    } finally {
      setSaving(false);
    }
  }

  const isConfigured = config?.is_configured;

  return (
    <div className={styles.page}>
      <PageHeader title={t("DomainPage.pageTitle")} subtitle={t("DomainPage.pageSubtitle")}>
        <div className={styles.headerActions} data-guide="domain-connect">
          <button
            type="button"
            className={styles.btnSecondary}
            onClick={() => window.open("https://dash.cloudflare.com", "_blank")}
          >
            <MIcon name="open_in_new" size={16} />
            Cloudflare Dashboard
          </button>
          <button type="button" className={styles.btnSecondary} onClick={handleTest} disabled={testing || !isConfigured}>
            <MIcon name="wifi_tethering" size={16} />
            {testing ? t("DomainPage.testing") : t("DomainPage.testConnection")}
          </button>
          <button type="button" className={styles.btnPrimary} onClick={() => setModal({ kind: "config" })}>
            <MIcon name="settings" size={16} />
            {t("DomainPage.connectionSettings")}
          </button>
        </div>
      </PageHeader>

      {config && (
        <div className={styles.configBar} data-guide="domain-status">
          <span className={`${styles.badge} ${isConfigured ? styles.badge_success : styles.badge_danger}`}>
            <MIcon name={isConfigured ? "check_circle" : "error"} size={13} />
            {isConfigured ? t("DomainPage.connected") : t("DomainPage.notSet")}
          </span>
          {config.account_id && <span className={styles.configMeta}>{t("DomainPage.accountLabel")}{config.account_id}</span>}
          {config.last_verified_at && (
            <span className={styles.configMeta}>{t("DomainPage.lastVerifiedLabel")}{formatDateTime(config.last_verified_at)}</span>
          )}
        </div>
      )}

      <div className={styles.tabs}>
        <button
          type="button"
          className={`${styles.tab} ${activeTab === "dns" ? styles.tabActive : ""}`}
          onClick={() => selectTab("dns")}
        >
          <MIcon name="dns" size={16} />
          {t("DomainPage.tabDns")}
        </button>
        <button
          type="button"
          className={`${styles.tab} ${activeTab === "reverse-proxy" ? styles.tabActive : ""}`}
          onClick={() => selectTab("reverse-proxy")}
        >
          <MIcon name="swap_horiz" size={16} />
          {t("DomainPage.tabReverseProxy")}
        </button>
      </div>

      {activeTab === "reverse-proxy" ? (
        <div className={styles.tabBody}>
          <ReverseProxyPanel />
        </div>
      ) : !isConfigured && !loadingZones ? (
        <EmptyState
          icon="domain"
          title={t("DomainPage.emptyNotConnected")}
        />
      ) : (
        <div className={styles.workbench}>
          {/* Zone 側欄 */}
          <div className={styles.zonePanel} data-guide="domain-zones">
            <h2 className={styles.panelTitle}>{t("DomainPage.zonesTitle", { count: zones.length })}</h2>
            {loadingZones ? (
              <LoadingState />
            ) : zones.length === 0 ? (
              <p className={styles.panelEmpty}>{t("DomainPage.noZonesFound")}</p>
            ) : (
              <div className={styles.zoneList}>
                {zones.map((zone) => (
                  <button
                    key={zone.id}
                    type="button"
                    className={selectedZone?.id === zone.id ? styles.zoneItemActive : styles.zoneItem}
                    onClick={() => setSelectedZone(zone)}
                  >
                    <span className={styles.zoneName}>{zone.name}</span>
                    <span className={`${styles.badge} ${zone.status === "active" ? styles.badge_success : styles.badge_muted}`}>
                      {zone.status}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* DNS record 主區 */}
          <div className={styles.recordPanel}>
            <div className={styles.recordToolbar} data-guide="domain-records">
              <div className={styles.searchBox}>
                <MIcon name="search" size={16} />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && selectedZone) fetchRecords(selectedZone.id, search);
                  }}
                  placeholder={t("DomainPage.recordSearchPlaceholder")}
                  disabled={!selectedZone}
                />
              </div>
              <button
                type="button"
                className={styles.btnPrimary}
                onClick={() => setModal({ kind: "record" })}
                disabled={!selectedZone}
              >
                <MIcon name="add" size={16} />
                {t("DomainPage.addRecord")}
              </button>
            </div>

            {!selectedZone ? (
              <p className={styles.panelEmpty}>{t("DomainPage.selectZoneFirst")}</p>
            ) : loadingRecords ? (
              <LoadingState text={t("DomainPage.loadingRecords")} />
            ) : records.length === 0 ? (
              <p className={styles.panelEmpty}>{t("DomainPage.noRecordsInZone")}</p>
            ) : (
              <div className={styles.list}>
                {records.map((r) => (
                  <div key={r.id} className={styles.row}>
                    <div className={styles.rowIcon}>
                      <MIcon name="dns" size={20} />
                    </div>
                    <div className={styles.rowMain}>
                      <span className={styles.rowName}>{r.name}</span>
                      <span className={styles.rowMeta}>
                        {r.type} · {r.content} · TTL {r.ttl === 1 ? "Auto" : r.ttl}
                        {r.comment ? ` · ${r.comment}` : ""}
                      </span>
                    </div>
                    {/* 一眼分出哪些紀錄是本系統的對外網址建的、哪些是外部自己加的 */}
                    <span
                      className={`${styles.badge} ${r.managed_by_system ? styles.badge_success : styles.badge_muted}`}
                      title={r.managed_by_system
                        ? t("DomainPage.managedBySystemHint", { vmid: r.managed_vmid ?? "-" })
                        : t("DomainPage.externalRecordHint")}
                    >
                      <MIcon name={r.managed_by_system ? "verified" : "public"} size={12} />
                      {r.managed_by_system
                        ? t("DomainPage.managedBySystem", { vmid: r.managed_vmid ?? "-" })
                        : t("DomainPage.externalRecord")}
                    </span>
                    {r.proxied != null && (
                      <span className={`${styles.badge} ${r.proxied ? styles.badge_info : styles.badge_muted}`}>
                        {r.proxied ? "Proxied" : "DNS only"}
                      </span>
                    )}
                    <div className={styles.rowActions}>
                      <button
                        type="button"
                        className={styles.actionBtn}
                        title={t("DomainPage.edit")}
                        onClick={() => setModal({ kind: "record", record: r })}
                      >
                        <MIcon name="edit" size={16} />
                      </button>
                      <button
                        type="button"
                        className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                        title={t("DomainPage.delete")}
                        onClick={() => setModal({ kind: "deleteRecord", record: r })}
                      >
                        <MIcon name="delete" size={16} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {modalPresence.item?.kind === "config" && (
        <ConfigModal
          config={config}
          loading={saving}
          closing={modalPresence.closing}
          onClose={() => setModal(null)}
          onSubmit={handleSaveConfig}
        />
      )}
      {modalPresence.item?.kind === "record" && (
        <RecordModal
          record={modalPresence.item.record}
          loading={saving}
          closing={modalPresence.closing}
          onClose={() => setModal(null)}
          onSubmit={handleSaveRecord}
        />
      )}
      {modalPresence.item?.kind === "deleteRecord" && (
        <div
          className={`${styles.modalOverlay} ${modalPresence.closing ? styles.modalOverlayOut : ""}`}
          onMouseDown={() => setModal(null)}
        >
          <div className={styles.confirm} onMouseDown={(e) => e.stopPropagation()}>
            <div className={styles.confirmIcon}>
              <MIcon name="warning" size={24} />
            </div>
            <h2>{t("DomainPage.deleteRecordTitle")}</h2>
            <p>
              {t("DomainPage.deleteRecordConfirm", { name: modalPresence.item.record.name, type: modalPresence.item.record.type })}
            </p>
            <div className={styles.modalActions}>
              <button type="button" className={styles.btnSecondary} onClick={() => setModal(null)}>
                {t("DomainPage.cancel")}
              </button>
              <button type="button" className={styles.btnDanger} disabled={saving} onClick={handleDeleteRecord}>
                {saving ? t("DomainPage.deleting") : t("DomainPage.delete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
