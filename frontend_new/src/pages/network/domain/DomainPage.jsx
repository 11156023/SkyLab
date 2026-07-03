import { useCallback, useEffect, useState } from "react";
import styles from "./DomainPage.module.scss";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import { CloudflareService } from "../../../services/cloudflare";

const DNS_TYPES = ["A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV"];

function formatDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* ── 供應商設定 Modal ───────────────────────────────────── */

function ConfigModal({ config, loading, onClose, onSubmit }) {
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
    <div className={styles.modalOverlay} onMouseDown={onClose}>
      <form className={styles.modal} onSubmit={submit} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>Cloudflare 連線設定</h2>
            <p>API Token 需具備 Zone / DNS 編輯權限；Token 只寫入不回讀。</p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label="關閉">
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
          <span>API Token{config?.has_api_token ? "（留空表示不變更）" : " *"}</span>
          <input
            type="password"
            value={form.api_token}
            onChange={(e) => set("api_token", e.target.value)}
            placeholder={config?.has_api_token ? "已設定，留空不變更" : "貼上 API Token"}
            required={!config?.has_api_token}
          />
        </label>

        <div className={styles.fieldRow}>
          <label className={styles.field}>
            <span>預設 DNS Target 類型</span>
            <select
              value={form.default_dns_target_type}
              onChange={(e) => set("default_dns_target_type", e.target.value)}
            >
              <option value="">未設定</option>
              <option value="A">A（IP 位址）</option>
              <option value="CNAME">CNAME（主機名）</option>
            </select>
          </label>
          <label className={styles.field}>
            <span>預設 DNS Target 值</span>
            <input
              value={form.default_dns_target_value}
              onChange={(e) => set("default_dns_target_value", e.target.value)}
              placeholder="例：140.131.x.x 或 gw.example.com"
            />
          </label>
        </div>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
            取消
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading}>
            {loading ? "儲存中..." : "儲存"}
          </button>
        </div>
      </form>
    </div>
  );
}

/* ── DNS record 編輯 Modal ─────────────────────────────── */

function RecordModal({ record, loading, onClose, onSubmit }) {
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
    <div className={styles.modalOverlay} onMouseDown={onClose}>
      <form className={styles.modal} onSubmit={submit} onMouseDown={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <div>
            <h2>{isEdit ? "編輯 DNS Record" : "新增 DNS Record"}</h2>
            <p>TTL 設 1 代表 Auto。</p>
          </div>
          <button type="button" className={styles.iconBtn} onClick={onClose} aria-label="關閉">
            <MIcon name="close" size={18} />
          </button>
        </div>

        <div className={styles.fieldRow}>
          <label className={styles.field}>
            <span>類型 *</span>
            <select value={form.type} onChange={(e) => set("type", e.target.value)}>
              {DNS_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
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
          <span>名稱 *</span>
          <input
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="例：www 或 www.example.com"
            required
          />
        </label>

        <label className={styles.field}>
          <span>內容 *</span>
          <input
            value={form.content}
            onChange={(e) => set("content", e.target.value)}
            placeholder="例：140.131.x.x"
            required
          />
        </label>

        <label className={styles.field}>
          <span>備註</span>
          <input
            value={form.comment}
            onChange={(e) => set("comment", e.target.value)}
            placeholder="選填"
          />
        </label>

        <label className={styles.checkRow}>
          <input
            type="checkbox"
            checked={form.proxied}
            onChange={(e) => set("proxied", e.target.checked)}
          />
          <span>經由 Cloudflare Proxy（橘色雲）</span>
        </label>

        <div className={styles.modalActions}>
          <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={loading}>
            取消
          </button>
          <button type="submit" className={styles.btnPrimary} disabled={loading}>
            {loading ? "儲存中..." : "儲存"}
          </button>
        </div>
      </form>
    </div>
  );
}

/* ── 主頁 ───────────────────────────────────────────────── */

export default function DomainPage() {
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

  const fetchConfig = useCallback(async () => {
    try {
      setConfig(await CloudflareService.getConfig());
    } catch (err) {
      toast.error(err?.message ?? "載入 Cloudflare 設定失敗");
    }
  }, [toast]);

  const fetchZones = useCallback(async () => {
    setLoadingZones(true);
    try {
      const res = await CloudflareService.listZones({ per_page: 50 });
      const items = res?.items ?? [];
      setZones(items);
      setSelectedZone((prev) => prev ?? items[0] ?? null);
    } catch (err) {
      // 未設定連線時後端會回錯誤，front 只顯示空狀態
      if (err?.status !== 400) toast.error(err?.message ?? "載入 Zone 失敗");
    } finally {
      setLoadingZones(false);
    }
  }, [toast]);

  const fetchRecords = useCallback(async (zoneId, keyword) => {
    setLoadingRecords(true);
    try {
      const res = await CloudflareService.listDnsRecords(zoneId, {
        per_page: 100,
        search: keyword || undefined,
      });
      setRecords(res?.items ?? []);
    } catch (err) {
      toast.error(err?.message ?? "載入 DNS 紀錄失敗");
    } finally {
      setLoadingRecords(false);
    }
  }, [toast]);

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
      toast.success("設定已儲存");
      setModal(null);
      fetchZones();
    } catch (err) {
      toast.error(err?.message ?? "儲存設定失敗");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    try {
      const res = await CloudflareService.testConfig();
      if (res.success) toast.success(res.message || "連線成功");
      else toast.error(res.message || "連線失敗");
      fetchConfig();
    } catch (err) {
      toast.error(err?.message ?? "連線測試失敗");
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
        toast.success("DNS 紀錄已更新");
      } else {
        await CloudflareService.createDnsRecord(selectedZone.id, body);
        toast.success("DNS 紀錄已建立");
      }
      setModal(null);
      fetchRecords(selectedZone.id, search);
    } catch (err) {
      toast.error(err?.message ?? "儲存 DNS 紀錄失敗");
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteRecord() {
    if (!selectedZone || !modal?.record) return;
    setSaving(true);
    try {
      await CloudflareService.deleteDnsRecord(selectedZone.id, modal.record.id);
      toast.success("DNS 紀錄已刪除");
      setModal(null);
      fetchRecords(selectedZone.id, search);
    } catch (err) {
      toast.error(err?.message ?? "刪除失敗");
    } finally {
      setSaving(false);
    }
  }

  const isConfigured = config?.is_configured;

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>網域管理</h1>
          <p className={styles.pageSubtitle}>
            用同一個工作台完成 Cloudflare 供應商連線、Zone 檢視，以及 DNS record 的新增、調整與刪除。
          </p>
        </div>
        <div className={styles.headerActions}>
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
            {testing ? "測試中..." : "測試連線"}
          </button>
          <button type="button" className={styles.btnPrimary} onClick={() => setModal({ kind: "config" })}>
            <MIcon name="settings" size={16} />
            連線設定
          </button>
        </div>
      </div>

      {config && (
        <div className={styles.configBar}>
          <span className={`${styles.badge} ${isConfigured ? styles.badge_success : styles.badge_danger}`}>
            <MIcon name={isConfigured ? "check_circle" : "error"} size={13} />
            {isConfigured ? "已連線" : "未設定"}
          </span>
          {config.account_id && <span className={styles.configMeta}>Account：{config.account_id}</span>}
          {config.last_verified_at && (
            <span className={styles.configMeta}>上次驗證：{formatDate(config.last_verified_at)}</span>
          )}
        </div>
      )}

      {!isConfigured && !loadingZones ? (
        <div className={styles.empty}>
          <div className={styles.emptyIcon}>
            <MIcon name="domain" size={40} />
          </div>
          <h2 className={styles.emptyTitle}>尚未連線 Cloudflare</h2>
          <p className={styles.emptyDesc}>點擊「連線設定」輸入 Account ID 與 API Token 後即可載入 Zone 與 DNS 紀錄</p>
        </div>
      ) : (
        <div className={styles.workbench}>
          {/* Zone 側欄 */}
          <div className={styles.zonePanel}>
            <h2 className={styles.panelTitle}>Zones（{zones.length}）</h2>
            {loadingZones ? (
              <div className={styles.loading}>載入中...</div>
            ) : zones.length === 0 ? (
              <p className={styles.panelEmpty}>找不到任何 Zone</p>
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
            <div className={styles.recordToolbar}>
              <div className={styles.searchBox}>
                <MIcon name="search" size={16} />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && selectedZone) fetchRecords(selectedZone.id, search);
                  }}
                  placeholder="搜尋紀錄名稱，Enter 查詢"
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
                新增紀錄
              </button>
            </div>

            {!selectedZone ? (
              <p className={styles.panelEmpty}>請先選擇左側 Zone</p>
            ) : loadingRecords ? (
              <div className={styles.loading}>載入 DNS 紀錄...</div>
            ) : records.length === 0 ? (
              <p className={styles.panelEmpty}>此 Zone 尚無 DNS 紀錄</p>
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
                    {r.proxied != null && (
                      <span className={`${styles.badge} ${r.proxied ? styles.badge_info : styles.badge_muted}`}>
                        {r.proxied ? "Proxied" : "DNS only"}
                      </span>
                    )}
                    <div className={styles.rowActions}>
                      <button
                        type="button"
                        className={styles.actionBtn}
                        title="編輯"
                        onClick={() => setModal({ kind: "record", record: r })}
                      >
                        <MIcon name="edit" size={16} />
                      </button>
                      <button
                        type="button"
                        className={`${styles.actionBtn} ${styles.actionBtnDanger}`}
                        title="刪除"
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

      {modal?.kind === "config" && (
        <ConfigModal
          config={config}
          loading={saving}
          onClose={() => setModal(null)}
          onSubmit={handleSaveConfig}
        />
      )}
      {modal?.kind === "record" && (
        <RecordModal
          record={modal.record}
          loading={saving}
          onClose={() => setModal(null)}
          onSubmit={handleSaveRecord}
        />
      )}
      {modal?.kind === "deleteRecord" && (
        <div className={styles.modalOverlay} onMouseDown={() => setModal(null)}>
          <div className={styles.confirm} onMouseDown={(e) => e.stopPropagation()}>
            <div className={styles.confirmIcon}>
              <MIcon name="warning" size={24} />
            </div>
            <h2>刪除 DNS 紀錄</h2>
            <p>
              確定要刪除 <strong>{modal.record.name}</strong>（{modal.record.type}）嗎？此操作無法復原。
            </p>
            <div className={styles.modalActions}>
              <button type="button" className={styles.btnSecondary} onClick={() => setModal(null)}>
                取消
              </button>
              <button type="button" className={styles.btnDanger} disabled={saving} onClick={handleDeleteRecord}>
                {saving ? "刪除中..." : "刪除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
