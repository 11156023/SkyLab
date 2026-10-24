import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./DomainPage.module.scss";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { useToast } from "../../../hooks/useToast";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { CloudflareService } from "../../../services/cloudflare";
import {
  useCloudflareCacheUpdater,
  useCloudflareConfigQuery,
  useCloudflareDnsRecordsQuery,
  useCloudflareZonesQuery,
} from "../../../queries/cloudflare";
import PageHeader from "../../../components/PageHeader/PageHeader";
import PasswordInput from "../../../components/PasswordInput/PasswordInput";
import { formatDateTime, formatShortDateTime } from "../../../utils/formatDate";

/* SRV 需要結構化 data（service/proto/weight/port/target），表單與後端都沒有，所以不提供 */
export const DNS_TYPES = ["A", "AAAA", "CNAME", "TXT", "MX", "NS"];
/* Cloudflare 對 MX 要求 priority；後端 schema 範圍 0–65535 */
const PRIORITY_TYPES = new Set(["MX"]);
const DEFAULT_MX_PRIORITY = 10;

/** 編輯既有紀錄時，類型不在清單內（例如外部建立的 SRV）也要列出，避免下拉框顯示成別的類型 */
export function recordTypeOptions(currentType) {
  return currentType && !DNS_TYPES.includes(currentType) ? [...DNS_TYPES, currentType] : DNS_TYPES;
}

/**
 * DNS record 表單 → API body。
 * - MX 一定帶 priority（Cloudflare 必填）。
 * - 編輯時一律帶 comment：空字串代表清除備註；新增時空白就不送。
 */
export function buildRecordBody(form, isEdit) {
  const body = {
    type: form.type,
    name: form.name.trim(),
    content: form.content.trim(),
    ttl: Number(form.ttl) || 1,
    proxied: form.proxied,
  };
  const comment = (form.comment ?? "").trim();
  if (isEdit || comment) body.comment = comment;
  if (PRIORITY_TYPES.has(form.type)) {
    const raw = String(form.priority ?? "").trim();
    const priority = raw === "" ? NaN : Number(raw);
    body.priority = Number.isInteger(priority) && priority >= 0 && priority <= 65535 ? priority : DEFAULT_MX_PRIORITY;
  }
  return body;
}

/**
 * Zone 清單重新載入後的選取：原本選的 zone 還在就換成新物件（沿用同一個 id），
 * 不在了（例如換了另一個帳號的 Token）就改選第一個，避免畫面停在舊帳號的紀錄。
 */
export function pickSelectedZone(prev, items) {
  const kept = prev ? items.find((z) => z.id === prev.id) : undefined;
  return kept ?? items[0] ?? null;
}

const EMPTY = Object.freeze([]);
/* 側欄的 zone 清單只取第一頁 50 個（與原本相同） */
const ZONE_PARAMS = Object.freeze({ per_page: 50 });

/* 兩個 Modal 共用的「取消／儲存」列；儲存鈕是 submit，交給外層 form 處理 */
function ModalActions({ loading, onClose }) {
  const { t } = useTranslation("system");
  return (
    <>
      <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
        {t("DomainPage.cancel")}
      </button>
      <button type="submit" className={styles.btnPrimary} disabled={loading}>
        {loading ? t("DomainPage.saving") : t("DomainPage.save")}
      </button>
    </>
  );
}

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

  /* 外框（遮罩、標題列、Esc、焦點、捲動鎖）交給共用 Modal；儲存中 Esc／點遮罩／× 都不關 */
  return (
    <Modal
      as="form"
      onSubmit={submit}
      closing={closing}
      onClose={onClose}
      busy={loading}
      closeButton
      size="md"
      title={t("DomainPage.configModalTitle")}
      data-guide="domain-config-form"
      closeProps={{ "data-guide": "domain-modal-close" }}
      actions={<ModalActions loading={loading} onClose={onClose} />}
    >
      <label className={styles.field} data-guide="domain-config-credentials">
        <span>Account ID</span>
        <input
          value={form.account_id}
          onChange={(e) => set("account_id", e.target.value)}
          placeholder="Cloudflare Account ID"
        />
      </label>

      <label className={styles.field}>
        {/* 「去哪拿 Token」的連結放在欄位旁，需要時就在手邊 */}
        <span className={styles.labelRow}>
          <span>API Token{config?.has_api_token ? t("DomainPage.leaveBlankUnchanged") : " *"}</span>
          <a
            className={styles.fieldLink}
            href="https://dash.cloudflare.com/profile/api-tokens"
            target="_blank"
            rel="noreferrer"
          >
            {t("DomainPage.tokenGuideLink")}
            <MIcon name="open_in_new" size={13} />
          </a>
        </span>
        <PasswordInput
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
    </Modal>
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
    priority: record?.priority ?? DEFAULT_MX_PRIORITY,
  });

  function set(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }));
  }

  function submit(e) {
    e.preventDefault();
    onSubmit(buildRecordBody(form, isEdit));
  }

  return (
    <Modal
      as="form"
      onSubmit={submit}
      closing={closing}
      onClose={onClose}
      busy={loading}
      closeButton
      size="md"
      title={isEdit ? t("DomainPage.recordModalEditTitle") : t("DomainPage.recordModalCreateTitle")}
      description={t("DomainPage.ttlHint")}
      data-guide="domain-record-form"
      closeProps={{ "data-guide": "domain-modal-close" }}
      actions={<ModalActions loading={loading} onClose={onClose} />}
    >
      <div className={styles.fieldRow}>
        <label className={styles.field}>
          <span>{t("DomainPage.recordType")}</span>
          <select value={form.type} onChange={(e) => set("type", e.target.value)}>
            {recordTypeOptions(record?.type).map((type) => (
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

      {PRIORITY_TYPES.has(form.type) && (
        <label className={styles.field}>
          <span>{t("DomainPage.recordPriority")}</span>
          <input
            type="number"
            min={0}
            max={65535}
            step={1}
            value={form.priority}
            onChange={(e) => set("priority", e.target.value)}
            required
          />
        </label>
      )}

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
    </Modal>
  );
}

/* ── 主頁 ───────────────────────────────────────────────── */

export default function DomainPage() {
  const { t } = useTranslation("system");
  const toast = useToast();
  const confirm = useConfirm();
  /* 使用者點選的 zone id；實際選取由 pickSelectedZone 對照目前清單算出（不在了就改選第一個） */
  const [selectedZoneId, setSelectedZoneId] = useState(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  /* 輸入框的字與按 Enter 送出、實際拿去查詢的關鍵字，都記下是哪個 zone 的：
     換 zone（點選，或原本的 zone 在重新載入後不見了）時兩者自動變回空字串 */
  const [searchDraft, setSearchDraft] = useState({ zoneId: null, text: "" });
  const [submittedSearch, setSubmittedSearch] = useState({ zoneId: null, text: "" });
  const [modal, setModal] = useState(null); // { kind: "config" } | { kind: "record", record? }
  const modalPresence = useDialogPresence(modal);
  const cloudflareCache = useCloudflareCacheUpdater();

  /* 連線設定、zone 清單、DNS 紀錄走 TanStack Query；原本只在進頁與操作後重抓，不背景輪詢 */
  const configQuery = useCloudflareConfigQuery();
  const config = configQuery.data ?? null;
  const configError = configQuery.error;
  useEffect(() => {
    if (configError) toast.error(configError?.message ?? t("DomainPage.toastLoadConfigFailed"));
  }, [configError, toast, t]);

  const zonesQuery = useCloudflareZonesQuery(ZONE_PARAMS);
  const zones = zonesQuery.data?.items ?? EMPTY;
  const loadingZones = zonesQuery.data === undefined && (zonesQuery.isPending || zonesQuery.isFetching);
  const zonesError = zonesQuery.error;
  useEffect(() => {
    // 未設定連線時後端會回錯誤，front 只顯示空狀態
    if (zonesError && zonesError.status !== 400) toast.error(zonesError?.message ?? t("DomainPage.toastLoadZonesFailed"));
  }, [zonesError, toast, t]);
  const selectedZone = pickSelectedZone(selectedZoneId == null ? null : { id: selectedZoneId }, zones);
  const currentZoneId = selectedZone?.id ?? null;
  const search = searchDraft.zoneId === currentZoneId ? searchDraft.text : "";
  const recordKeyword = submittedSearch.zoneId === currentZoneId ? submittedSearch.text : "";
  const setSearch = (text) => setSearchDraft({ zoneId: currentZoneId, text });
  const setRecordKeyword = (text) => setSubmittedSearch({ zoneId: currentZoneId, text });

  /* 每個 zone＋關鍵字一份快取，畫面只看目前那組：快速切換 zone 時，較晚回來的舊 zone 回應
     只會寫進舊 zone 的快取，蓋不掉目前 zone 的紀錄（否則編輯／刪除會拿 A 的 record id 打到 B 的 zone） */
  const recordsQuery = useCloudflareDnsRecordsQuery(selectedZone?.id, {
    per_page: 100,
    search: recordKeyword || undefined,
  });
  const records = recordsQuery.data?.items ?? EMPTY;
  const loadingRecords = recordsQuery.data === undefined && (recordsQuery.isPending || recordsQuery.isFetching);
  const recordsError = recordsQuery.error;
  useEffect(() => {
    if (recordsError) toast.error(recordsError?.message ?? t("DomainPage.toastLoadRecordsFailed"));
  }, [recordsError, toast, t]);

  /* 換 zone 時清掉搜尋框與關鍵字（A → B → A 也不會把 A 先前打的字帶回來）；點同一個 zone 不動 */
  function selectZone(zone) {
    if (zone.id === currentZoneId) return;
    setSelectedZoneId(zone.id);
    setSearchDraft({ zoneId: zone.id, text: "" });
    setSubmittedSearch({ zoneId: zone.id, text: "" });
  }

  /* 按 Enter：關鍵字沒變就重抓，變了換 key 自然會抓 */
  function submitSearch() {
    if (search === recordKeyword) recordsQuery.refetch();
    else setRecordKeyword(search);
  }

  /* 新增／修改／刪除後：用輸入框目前的字重查，並把該 zone 其他關鍵字的快取一起標成過期 */
  function refreshRecords(zoneId) {
    setRecordKeyword(search);
    cloudflareCache.invalidateDnsRecords(zoneId);
  }

  async function handleSaveConfig(body) {
    setSaving(true);
    try {
      const updated = await CloudflareService.updateConfig(body);
      cloudflareCache.setConfig(updated);
      toast.success(t("DomainPage.toastConfigSaved"));
      setModal(null);
      /* 換了 Token 可能是另一個帳號：zone 與紀錄都重抓 */
      cloudflareCache.invalidateZones();
      cloudflareCache.invalidateDnsRecords();
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
      configQuery.refetch();
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
      refreshRecords(selectedZone.id);
    } catch (err) {
      toast.error(err?.message ?? t("DomainPage.toastSaveRecordFailed"));
    } finally {
      setSaving(false);
    }
  }

  /* 刪除 DNS 紀錄：共用確認框；送出中 saving 擋住重複點擊 */
  async function handleDeleteRecord(record) {
    if (!selectedZone || !record || saving) return;
    const ok = await confirm({
      title: t("DomainPage.deleteRecordTitle"),
      message: t("DomainPage.deleteRecordConfirm", { name: record.name, type: record.type }),
      confirmText: t("DomainPage.delete"),
      cancelText: t("DomainPage.cancel"),
      danger: true,
    });
    if (!ok) return;
    setSaving(true);
    try {
      await CloudflareService.deleteDnsRecord(selectedZone.id, record.id);
      toast.success(t("DomainPage.toastRecordDeleted"));
      refreshRecords(selectedZone.id);
    } catch (err) {
      toast.error(err?.message ?? t("DomainPage.toastDeleteFailed"));
    } finally {
      setSaving(false);
    }
  }

  const isConfigured = config?.is_configured;
  const accountId = config?.account_id;
  const verifiedAt = config?.last_verified_at;
  /* 32 字元的 Account ID 只留頭尾，完整值與精確時間放滑過提示（連線設定裡也看得到） */
  const statusMeta = [
    accountId && `${t("DomainPage.accountLabel")}${accountId.length > 12 ? `${accountId.slice(0, 4)}…${accountId.slice(-4)}` : accountId}`,
    verifiedAt && `${t("DomainPage.lastVerifiedLabel")}${formatShortDateTime(verifiedAt)}`,
  ].filter(Boolean).join(" · ");
  const statusTitle = [
    accountId && `${t("DomainPage.accountLabel")}${accountId}`,
    verifiedAt && `${t("DomainPage.lastVerifiedLabel")}${formatDateTime(verifiedAt)}`,
  ].filter(Boolean).join("\n");

  return (
    <div className={styles.page}>
      <PageHeader title={t("DomainPage.pageTitle")}>
        <div className={styles.headerActions} data-guide="domain-connect">
          {/* noopener,noreferrer：新分頁不能透過 window.opener 反向操作本站 */}
          <button
            type="button"
            className={styles.btnSecondary}
            onClick={() => window.open("https://dash.cloudflare.com", "_blank", "noopener,noreferrer")}
          >
            <MIcon name="open_in_new" size={16} />
            Cloudflare Dashboard
          </button>
          <button type="button" className={styles.btnSecondary} onClick={handleTest} disabled={testing || !isConfigured}>
            <MIcon name="wifi_tethering" size={16} />
            {testing ? t("DomainPage.testing") : t("DomainPage.testConnection")}
          </button>
          {/* 已連線才在頁首放「連線設定」；還沒連線時下方空狀態那顆就是入口（導覽也改點它） */}
          {isConfigured && (
            <button type="button" className={styles.btnPrimary} onClick={() => setModal({ kind: "config" })} data-guide="domain-settings-open">
              <MIcon name="settings" size={16} />
              {t("DomainPage.connectionSettings")}
            </button>
          )}
        </div>
      </PageHeader>

      {config && (
        <div className={styles.statusRow}>
          <div className={styles.connStatus} title={statusTitle || undefined} data-guide="domain-status">
            <span className={`${styles.badge} ${isConfigured ? styles.badge_success : styles.badge_danger}`}>
              <MIcon name={isConfigured ? "check_circle" : "error"} size={13} />
              {isConfigured ? t("DomainPage.connected") : t("DomainPage.notSet")}
            </span>
            {statusMeta && <span className={styles.connMeta}>{statusMeta}</span>}
          </div>
        </div>
      )}

      {!isConfigured && !loadingZones ? (
        <EmptyState
          icon="domain"
          title={t("DomainPage.emptyNotConnected")}
          action={<button type="button" className={styles.btnPrimary} onClick={() => setModal({ kind: "config" })} data-guide="domain-settings-open"><MIcon name="settings" size={16} />{t("DomainPage.connectionSettings")}</button>}
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
                    onClick={() => selectZone(zone)}
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
                    if (e.key === "Enter" && selectedZone) submitSearch();
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
                data-guide="domain-record-open"
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
                      <span className={styles.rowName} title={r.name}>{r.name}</span>
                      {(() => {
                        const meta = `${r.type} · ${r.content} · TTL ${r.ttl === 1 ? "Auto" : r.ttl}${r.comment ? ` · ${r.comment}` : ""}`;
                        return <span className={styles.rowMeta} title={meta}>{meta}</span>;
                      })()}
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
                        className={styles.actionBtnDanger}
                        title={t("DomainPage.delete")}
                        disabled={saving}
                        onClick={() => handleDeleteRecord(r)}
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
    </div>
  );
}
