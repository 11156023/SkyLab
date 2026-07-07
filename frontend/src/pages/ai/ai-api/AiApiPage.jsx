import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./AiApiPage.module.scss";
import MIcon from "../../../components/MIcon";
import { AiApiService } from "../../../services/aiApi";
import { useToast } from "../../../hooks/useToast";

/* ── helpers ── */
const DURATION_OPTIONS = [
  { value: "1h", label: "1 小時" },
  { value: "1d", label: "1 天" },
  { value: "7d", label: "1 週" },
  { value: "30d", label: "1 個月" },
  { value: "never", label: "永不過期" },
];

const TABS = [
  { key: "apply",   label: "申請",      icon: "send" },
  { key: "keys",    label: "API Keys",  icon: "vpn_key" },
  { key: "records", label: "申請紀錄",  icon: "history" },
  { key: "usage",   label: "我的用量",  icon: "trending_up" },
];

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString("zh-TW") : "—";
}

function fmtExpiry(value) {
  if (!value) return "永不過期";
  const d = new Date(value);
  return d < new Date() ? `已過期（${d.toLocaleString()}）` : d.toLocaleString();
}

function isExpired(value) {
  if (!value) return false;
  return new Date(value) < new Date();
}

function maskKey(value) {
  if (!value || value.length <= 14) return value ?? "";
  return `${value.slice(0, 8)}••••••${value.slice(-6)}`;
}

function formatTokens(n) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function statusStyle(status) {
  if (status === "approved") return "approved";
  if (status === "rejected") return "rejected";
  return "pending";
}

function statusLabel(status) {
  if (status === "approved") return "已通過";
  if (status === "rejected") return "已拒絕";
  return "待審核";
}

function credStatusInfo(item) {
  if (item.revoked_at) return { label: "已替換", cls: "inactive" };
  if (isExpired(item.expires_at)) return { label: "已過期", cls: "expired" };
  return { label: "使用中", cls: "active" };
}

/* ── Empty ── */
function EmptyState({ icon, title, desc }) {
  return (
    <div className={styles.empty}>
      <div className={styles.emptyIcon}>
        <MIcon name={icon} size={40} />
      </div>
      <h2 className={styles.emptyTitle}>{title}</h2>
      <p className={styles.emptyDesc}>{desc}</p>
    </div>
  );
}

/* ── Stat card ── */
function StatCard({ label, value, icon, iconCls }) {
  return (
    <div className={styles.statCard}>
      <div className={`${styles.statIcon} ${iconCls ? styles[iconCls] : ""}`}>
        <MIcon name={icon} size={20} />
      </div>
      <div className={styles.statInfo}>
        <span className={styles.statLabel}>{label}</span>
        <span className={styles.statValue}>{value}</span>
      </div>
    </div>
  );
}

/* ── Credential card ── */
function CredentialCard({ item, onRefresh }) {
  const toast = useToast();
  const [showKey, setShowKey] = useState(false);
  const [editing, setEditing] = useState(false);
  const [nameInput, setNameInput] = useState(item.api_key_name);
  const [busy, setBusy] = useState(false);
  const info = credStatusInfo(item);
  const inactive = Boolean(item.revoked_at);
  const expired = isExpired(item.expires_at);

  const copy = async (label, value) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(`${label} 已複製`);
    } catch {
      toast.error(`${label} 複製失敗`);
    }
  };

  const doRotate = async () => {
    if (!window.confirm("刷新後舊金鑰會失效，確定繼續？")) return;
    setBusy(true);
    try {
      await AiApiService.rotateCredential(item.id);
      toast.success("API Key 已刷新");
      onRefresh();
    } catch (e) {
      toast.error(e?.message ?? "刷新失敗");
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    if (!window.confirm("確定刪除此金鑰？此操作無法復原。")) return;
    setBusy(true);
    try {
      await AiApiService.revokeCredential(item.id);
      toast.success("API Key 已刪除");
      onRefresh();
    } catch (e) {
      toast.error(e?.message ?? "刪除失敗");
    } finally {
      setBusy(false);
    }
  };

  const doRename = async () => {
    if (!nameInput.trim()) return;
    setBusy(true);
    try {
      await AiApiService.updateCredential(item.id, { api_key_name: nameInput.trim() });
      toast.success("名稱已更新");
      setEditing(false);
      onRefresh();
    } catch (e) {
      toast.error(e?.message ?? "更新失敗");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.credCard}>
      {/* Top row: name + badge */}
      <div className={styles.credHeader}>
        <div className={styles.credNameRow}>
          {editing ? (
            <div className={styles.renameRow}>
              <input
                type="text"
                className={styles.renameInput}
                value={nameInput}
                maxLength={20}
                onChange={(e) => setNameInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") doRename();
                  if (e.key === "Escape") { setNameInput(item.api_key_name); setEditing(false); }
                }}
                autoFocus
              />
              <button type="button" className={styles.btnIcon} onClick={doRename} disabled={busy}>
                <MIcon name="check" size={14} />
              </button>
              <button type="button" className={styles.btnIcon} onClick={() => { setNameInput(item.api_key_name); setEditing(false); }}>
                <MIcon name="close" size={14} />
              </button>
            </div>
          ) : (
            <div className={styles.nameWithEdit}>
              <span className={styles.credName}>{item.api_key_name}</span>
              <button type="button" className={styles.btnIconSm} onClick={() => { setNameInput(item.api_key_name); setEditing(true); }}>
                <MIcon name="edit" size={12} />
              </button>
            </div>
          )}
          <span className={`${styles.badge} ${styles[`badge_${info.cls}`]}`}>
            <span className={styles.dot} />
            {info.label}
          </span>
        </div>
        <div className={styles.credMeta}>
          <span>Prefix：{item.api_key_prefix}</span>
          <span>建立：{fmtTime(item.created_at)}</span>
          <span className={expired ? styles.textDanger : ""}>到期：{fmtExpiry(item.expires_at)}</span>
          {item.revoked_at && <span>失效：{fmtTime(item.revoked_at)}</span>}
        </div>
      </div>

      {/* Credentials display */}
      <div className={styles.credFields}>
        <div className={styles.credField}>
          <div className={styles.credFieldLabel}>
            <MIcon name="link" size={14} /> Base URL
          </div>
          <div className={styles.credFieldValue}>{item.base_url}</div>
        </div>
        <div className={styles.credField}>
          <div className={styles.credFieldLabel}>
            <MIcon name="vpn_key" size={14} /> API Key
          </div>
          <div className={styles.credFieldValue}>
            {showKey ? item.api_key : maskKey(item.api_key)}
          </div>
        </div>
      </div>

      {/* Actions */}
      <div className={styles.credActions}>
        <button type="button" className={styles.btnOutline} onClick={() => setShowKey((v) => !v)}>
          <MIcon name={showKey ? "visibility_off" : "visibility"} size={16} />
          {showKey ? "隱藏" : "顯示"}
        </button>
        <button type="button" className={styles.btnOutline} onClick={() => copy("Base URL", item.base_url)}>
          <MIcon name="content_copy" size={16} /> Base URL
        </button>
        <button type="button" className={styles.btnOutline} onClick={() => copy("API Key", item.api_key)}>
          <MIcon name="content_copy" size={16} /> API Key
        </button>
        <button type="button" className={styles.btnOutline} onClick={doRotate} disabled={inactive || busy}>
          <MIcon name="refresh" size={16} /> 刷新
        </button>
        <button type="button" className={`${styles.btnOutline} ${styles.btnDanger}`} onClick={doDelete} disabled={busy}>
          <MIcon name="delete" size={16} /> 刪除
        </button>
      </div>
    </div>
  );
}

/* ── Request row ── */
function RequestRow({ item }) {
  const st = statusStyle(item.status);
  return (
    <div className={styles.requestRow}>
      <div className={styles.requestInfo}>
        <span className={styles.requestName}>{item.api_key_name}</span>
        <span className={`${styles.badge} ${styles[`badge_${st}`]}`}>
          <span className={styles.dot} />
          {statusLabel(item.status)}
        </span>
      </div>
      <p className={styles.requestPurpose}>{item.purpose}</p>
      <div className={styles.requestMeta}>
        <span>申請：{fmtTime(item.created_at)}</span>
        <span>審核：{item.reviewed_at ? fmtTime(item.reviewed_at) : "尚未處理"}</span>
        {item.review_comment && <span>備註：{item.review_comment}</span>}
      </div>
    </div>
  );
}

/* ── Usage stat card ── */
function UsageStatCard({ label, value }) {
  return (
    <div className={styles.usageStatCard}>
      <span className={styles.usageStatLabel}>{label}</span>
      <span className={styles.usageStatValue}>{value}</span>
    </div>
  );
}

/* ── Usage: by-model / by-call-type breakdown ── */
function UsageBreakdown({ icon, title, entries, formatter }) {
  if (!entries || Object.keys(entries).length === 0) return null;
  return (
    <div className={styles.usageBreakdown}>
      <div className={styles.usageBreakdownTitle}>
        <MIcon name={icon} size={14} /> {title}
      </div>
      <div className={styles.usageBreakdownList}>
        {Object.entries(entries).map(([key, stats]) => (
          <div key={key} className={styles.usageBreakdownRow}>
            <span className={styles.usageBreakdownKey}>{formatter ? formatter(key) : key}</span>
            <span>{stats.requests ?? stats.calls ?? 0} 次</span>
            <span>↑ {formatTokens(stats.input_tokens)}</span>
            <span>↓ {formatTokens(stats.output_tokens)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatModelDisplay(modelName) {
  if (!modelName) return "-";
  const trimmed = modelName.trim();
  if (!trimmed) return "-";
  const match = trimmed.match(/models--([^/]+)--([^/]+)/);
  if (!match) return trimmed;
  return `${match[1]}/${match[2]}`;
}

/* ── My Usage Tab ── */
function MyUsageTab() {
  const [preset, setPreset] = useState("30d");
  const [proxyData, setProxyData] = useState(null);
  const [templateData, setTemplateData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [proxyError, setProxyError] = useState(false);
  const [templateError, setTemplateError] = useState(false);

  const { start, end } = useMemo(() => {
    const now = new Date();
    const e = now.toISOString().split("T")[0];
    const s = new Date(now);
    if (preset === "7d") s.setDate(s.getDate() - 7);
    else if (preset === "30d") s.setDate(s.getDate() - 30);
    else s.setDate(s.getDate() - 90);
    return { start: s.toISOString().split("T")[0], end: e };
  }, [preset]);

  const load = useCallback(async () => {
    setLoading(true);
    setProxyError(false);
    setTemplateError(false);
    const [proxyRes, tplRes] = await Promise.allSettled([
      AiApiService.getMyProxyUsage({ start_date: start, end_date: end }),
      AiApiService.getMyTemplateUsage({ start_date: start, end_date: end }),
    ]);
    if (proxyRes.status === "fulfilled") setProxyData(proxyRes.value);
    else setProxyError(true);
    if (tplRes.status === "fulfilled") setTemplateData(tplRes.value);
    else setTemplateError(true);
    setLoading(false);
  }, [start, end]);

  useEffect(() => { load(); }, [load]);

  const PRESETS = [
    { value: "7d", label: "7 天" },
    { value: "30d", label: "30 天" },
    { value: "90d", label: "90 天" },
  ];

  return (
    <div className={styles.usageTab}>
      <div className={styles.usageDateRow}>
        {PRESETS.map((p) => (
          <button
            key={p.value}
            type="button"
            className={`${styles.segmentBtn} ${preset === p.value ? styles.segmentActive : ""}`}
            onClick={() => setPreset(p.value)}
          >
            {p.label}
          </button>
        ))}
        <span className={styles.usageDateRange}>{start} ~ {end}</span>
      </div>

      {loading ? (
        <div className={styles.loadingText}>載入中…</div>
      ) : (
        <>
          {/* Proxy usage */}
          <div className={styles.usagePanel}>
            <div className={styles.usagePanelHeader}>
              <h3 className={styles.usagePanelTitle}>Proxy 用量</h3>
              <p className={styles.usagePanelDesc}>直接呼叫 AI API 的 Token 用量。</p>
            </div>
            {proxyError ? (
              <p className={styles.textDanger}>無法取得 Proxy 用量資料。</p>
            ) : proxyData ? (
              <>
                <div className={styles.usageStatsGrid}>
                  <UsageStatCard label="總呼叫次數" value={proxyData.total_requests} />
                  <UsageStatCard label="輸入 Tokens" value={formatTokens(proxyData.total_input_tokens)} />
                  <UsageStatCard label="輸出 Tokens" value={formatTokens(proxyData.total_output_tokens)} />
                </div>
                <UsageBreakdown
                  icon="bar_chart"
                  title="按模型"
                  entries={proxyData.by_model}
                  formatter={formatModelDisplay}
                />
              </>
            ) : (
              <p className={styles.noData}>此時段無 Proxy 呼叫紀錄。</p>
            )}
          </div>

          {/* Template usage */}
          <div className={styles.usagePanel}>
            <div className={styles.usagePanelHeader}>
              <h3 className={styles.usagePanelTitle}>Template 用量</h3>
              <p className={styles.usagePanelDesc}>使用 AI Template API 的 Token 用量。</p>
            </div>
            {templateError ? (
              <p className={styles.textDanger}>無法取得 Template 用量資料。</p>
            ) : templateData ? (
              <>
                <div className={styles.usageStatsGrid}>
                  <UsageStatCard label="總呼叫次數" value={templateData.total_calls} />
                  <UsageStatCard label="輸入 Tokens" value={formatTokens(templateData.total_input_tokens)} />
                  <UsageStatCard label="輸出 Tokens" value={formatTokens(templateData.total_output_tokens)} />
                </div>
                <UsageBreakdown
                  icon="auto_awesome"
                  title="按呼叫類型"
                  entries={templateData.by_call_type}
                />
              </>
            ) : (
              <p className={styles.noData}>此時段無 Template 呼叫紀錄。</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ───────────────────────────── Main ───────────────────────────── */

export default function AiApiPage() {
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("records");

  /* ── Form state ── */
  const [apiKeyName, setApiKeyName] = useState("test");
  const [purpose, setPurpose] = useState("");
  const [duration, setDuration] = useState("never");
  const [submitting, setSubmitting] = useState(false);

  /* ── Data ── */
  const [credentials, setCredentials] = useState([]);
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [credRes, reqRes] = await Promise.all([
        AiApiService.listMyCredentials(),
        AiApiService.listMyRequests(),
      ]);
      setCredentials(credRes?.data ?? []);
      setRequests(reqRes?.data ?? []);
    } catch (e) {
      toast.error(e?.message ?? "載入 AI API 資料失敗");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const activeCredentials = credentials.filter((c) => !c.revoked_at && !isExpired(c.expires_at));
  const expiredCredentials = credentials.filter((c) => !c.revoked_at && isExpired(c.expires_at));
  const approvedRequests = requests.filter((r) => r.status === "approved");

  /* ── Submit request ── */
  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await AiApiService.createRequest({
        purpose: purpose.trim(),
        api_key_name: apiKeyName.trim(),
        duration,
      });
      setPurpose("");
      setApiKeyName("test");
      setDuration("never");
      toast.success("AI API 申請已送出");
      load();
    } catch (e) {
      toast.error(e?.message ?? "申請失敗");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.page}>
      {/* ── Header ── */}
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <div className={styles.titleRow}>
            <h1 className={styles.pageTitle}>AI API 金鑰申請與管理</h1>
            <span className={styles.breadcrumb}>SkyLab AI API</span>
          </div>
          <p className={styles.pageSubtitle}>申請、管理與查詢 AI API 金鑰。</p>
        </div>
      </div>

      {/* ── Stat cards ── */}
      <div className={styles.statRow}>
        <StatCard label="申請紀錄" value={requests.length} icon="history" />
        <StatCard label="使用中金鑰" value={activeCredentials.length} icon="key" iconCls="statIconOk" />
        <StatCard label="過期金鑰" value={expiredCredentials.length} icon="cancel" iconCls="statIconErr" />
        <StatCard label="已通過申請" value={approvedRequests.length} icon="check_circle" iconCls="statIconOk" />
      </div>

      {/* ── Tabs ── */}
      <div className={styles.tabs}>
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={`${styles.tab} ${activeTab === tab.key ? styles.tabActive : ""}`}
            onClick={() => setActiveTab(tab.key)}
          >
            <MIcon name={tab.icon} size={16} />
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Content ── */}
      <div className={styles.content}>
        {/* ---- Tab: 申請 ---- */}
        {activeTab === "apply" && (
          <div className={styles.panel}>
            <div className={styles.panelHeader}>
              <h2 className={styles.panelTitle}>送出新申請</h2>
              <p className={styles.panelDesc}>填寫用途後送審。</p>
            </div>

            <div className={styles.formGroup}>
              <label className={styles.formLabel} htmlFor="ai-key-name">金鑰名稱</label>
              <input
                id="ai-key-name"
                type="text"
                className={styles.formInput}
                value={apiKeyName}
                onChange={(e) => setApiKeyName(e.target.value)}
                placeholder="例如：課程專案用、測試用、我的 App"
                maxLength={20}
              />
            </div>

            <div className={styles.formGroup}>
              <label className={styles.formLabel} htmlFor="ai-purpose">申請目的</label>
              <textarea
                id="ai-purpose"
                className={styles.formTextarea}
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                placeholder="例如：課程專題串接聊天模型、工具原型開發、知識庫問答測試或自動化腳本整合。"
                rows={5}
              />
            </div>

            <div className={styles.formGroup}>
              <label className={styles.formLabel} htmlFor="ai-duration">金鑰有效期限</label>
              <select
                id="ai-duration"
                className={styles.formSelect}
                value={duration}
                onChange={(e) => setDuration(e.target.value)}
              >
                {DURATION_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>

            <div className={styles.formFooter}>
              <span className={styles.formHint}>用途需至少 10 字。</span>
              <button
                type="button"
                className={styles.btnPrimary}
                onClick={handleSubmit}
                disabled={purpose.trim().length < 10 || submitting}
              >
                <MIcon name="send" size={16} />
                {submitting ? "送出中…" : "送出申請"}
              </button>
            </div>
          </div>
        )}

        {/* ---- Tab: API Keys ---- */}
        {activeTab === "keys" && (
          <div className={styles.panel}>
            <div className={styles.panelHeader}>
              <h2 className={styles.panelTitle}>我的 API Keys</h2>
              <p className={styles.panelDesc}>查看、複製、刷新或刪除金鑰。</p>
            </div>
            {loading ? (
              <div className={styles.loadingText}>載入中…</div>
            ) : credentials.length === 0 ? (
              <EmptyState
                icon="vpn_key"
                title="尚無金鑰"
                desc="目前還沒有任何已核發的 AI API Key。當申請通過後，新的金鑰會出現在這裡。"
              />
            ) : (
              <div className={styles.credList}>
                {credentials.map((item) => (
                  <CredentialCard key={item.id} item={item} onRefresh={load} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* ---- Tab: 申請紀錄 ---- */}
        {activeTab === "records" && (
          <div className={styles.panel}>
            <div className={styles.panelHeader}>
              <h2 className={styles.panelTitle}>申請紀錄</h2>
              <p className={styles.panelDesc}>近期申請狀態。</p>
            </div>
            {loading ? (
              <div className={styles.loadingText}>載入中…</div>
            ) : requests.length === 0 ? (
              <EmptyState
                icon="history"
                title="尚無紀錄"
                desc="目前還沒有 AI API 申請紀錄。"
              />
            ) : (
              <div className={styles.requestList}>
                {requests.map((item) => (
                  <RequestRow key={item.id} item={item} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* ---- Tab: 我的用量 ---- */}
        {activeTab === "usage" && <MyUsageTab />}
      </div>
    </div>
  );
}
