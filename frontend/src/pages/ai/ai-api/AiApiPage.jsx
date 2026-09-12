import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./AiApiPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { AiApiService } from "../../../services/aiApi";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import { focusInvalidField } from "../../../utils/focusField";
import PageHeader from "../../../components/PageHeader/PageHeader";
import RrdChart from "../../../components/RrdChart/RrdChart";
import { formatDateTime, formatMonthDay } from "../../../utils/formatDate";

/* ── helpers ── */
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

/* ── Empty ── */
function EmptyState({ icon, title, guideId }) {
  return (
    <div data-guide={guideId}>
      <SharedEmptyState icon={icon} title={title} />
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
  const { t } = useTranslation("ai");
  const toast = useToast();
  const confirm = useConfirm();
  const [showKey, setShowKey] = useState(false);
  const [editing, setEditing] = useState(false);
  const [nameInput, setNameInput] = useState(item.api_key_name);
  const [busy, setBusy] = useState(false);

  function fmtExpiry(value) {
    if (!value) return t("AiApiPage.durationOptionNever");
    const label = formatDateTime(value);
    return isExpired(value) ? t("AiApiPage.expiredFormat", { date: label }) : label;
  }

  function credStatusInfo(it) {
    if (it.revoked_at) return { label: t("AiApiPage.credStatusReplaced"), cls: "inactive" };
    if (isExpired(it.expires_at)) return { label: t("AiApiPage.credStatusExpired"), cls: "expired" };
    return { label: t("AiApiPage.credStatusActive"), cls: "active" };
  }

  const info = credStatusInfo(item);
  const inactive = Boolean(item.revoked_at);
  const expired = isExpired(item.expires_at);

  const copy = async (label, value) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(t("AiApiPage.copiedSuccess", { label }));
    } catch {
      toast.error(t("AiApiPage.copiedError", { label }));
    }
  };

  const doRotate = async () => {
    const ok = await confirm({
      title: t("AiApiPage.rotateDialogTitle"),
      message: t("AiApiPage.rotateDialogMessage"),
      confirmText: t("AiApiPage.rotateDialogConfirm"),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await AiApiService.rotateCredential(item.id);
      toast.success(t("AiApiPage.rotateSuccess"));
      onRefresh();
    } catch (e) {
      toast.error(e?.message ?? t("AiApiPage.rotateError"));
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    const ok = await confirm({
      title: t("AiApiPage.deleteDialogTitle"),
      message: t("AiApiPage.deleteDialogMessage"),
      confirmText: t("AiApiPage.deleteDialogConfirm"),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await AiApiService.revokeCredential(item.id);
      toast.success(t("AiApiPage.deleteSuccess"));
      onRefresh();
    } catch (e) {
      toast.error(e?.message ?? t("AiApiPage.deleteError"));
    } finally {
      setBusy(false);
    }
  };

  const doRename = async () => {
    if (!nameInput.trim()) return;
    setBusy(true);
    try {
      await AiApiService.updateCredential(item.id, { api_key_name: nameInput.trim() });
      toast.success(t("AiApiPage.renameSuccess"));
      setEditing(false);
      onRefresh();
    } catch (e) {
      toast.error(e?.message ?? t("AiApiPage.renameError"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.credCard} data-guide="ai-keys-content">
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
          <span>{t("AiApiPage.metaPrefix", { value: item.api_key_prefix })}</span>
          <span>{t("AiApiPage.metaCreated", { value: formatDateTime(item.created_at) })}</span>
          <span className={expired ? styles.textDanger : ""}>{t("AiApiPage.metaExpiry", { value: fmtExpiry(item.expires_at) })}</span>
          {item.revoked_at && <span>{t("AiApiPage.metaRevoked", { value: formatDateTime(item.revoked_at) })}</span>}
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
      <div className={styles.credActions} data-guide="ai-key-actions">
        <button type="button" className={styles.btnOutline} onClick={() => setShowKey((v) => !v)}>
          <MIcon name={showKey ? "visibility_off" : "visibility"} size={16} />
          {showKey ? t("AiApiPage.actionHide") : t("AiApiPage.actionShow")}
        </button>
        <button type="button" className={styles.btnOutline} onClick={() => copy("Base URL", item.base_url)}>
          <MIcon name="content_copy" size={16} /> Base URL
        </button>
        <button type="button" className={styles.btnOutline} onClick={() => copy("API Key", item.api_key)}>
          <MIcon name="content_copy" size={16} /> API Key
        </button>
        {/* 重新產生金鑰是破壞性動作（舊金鑰立即失效），不叫「刷新」也不長得像刷新 */}
        <button type="button" className={`${styles.btnOutline} ${styles.btnDanger}`} onClick={doRotate} disabled={inactive || busy}>
          <MIcon name="autorenew" size={16} /> {t("AiApiPage.actionRotate")}
        </button>
        <button type="button" className={`${styles.btnOutline} ${styles.btnDanger}`} onClick={doDelete} disabled={busy}>
          <MIcon name="delete" size={16} /> {t("AiApiPage.actionDelete")}
        </button>
      </div>
    </div>
  );
}

/* ── Request row ── */
function RequestRow({ item }) {
  const { t } = useTranslation("ai");

  function statusLabel(status) {
    if (status === "approved") return t("AiApiPage.statusApproved");
    if (status === "rejected") return t("AiApiPage.statusRejected");
    return t("AiApiPage.statusPending");
  }

  const st = statusStyle(item.status);
  return (
    <div className={styles.requestRow} data-guide="ai-records-content">
      <div className={styles.requestInfo}>
        <span className={styles.requestName}>{item.api_key_name}</span>
        <span className={`${styles.badge} ${styles[`badge_${st}`]}`}>
          <span className={styles.dot} />
          {statusLabel(item.status)}
        </span>
      </div>
      <p className={styles.requestPurpose}>{item.purpose}</p>
      <div className={styles.requestMeta}>
        <span>{t("AiApiPage.requestMetaApply", { value: formatDateTime(item.created_at) })}</span>
        <span>{t("AiApiPage.requestMetaReview", { value: formatDateTime(item.reviewed_at, t("AiApiPage.requestNotReviewed")) })}</span>
        {item.review_comment && <span>{t("AiApiPage.requestMetaComment", { value: item.review_comment })}</span>}
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

/* ── Usage: 逐日 Tokens 折線圖（#7）── */
function DailyUsageChart({ daily }) {
  const { t } = useTranslation("ai");
  const data = useMemo(
    () => (daily ?? []).map((point) => ({
      time: formatMonthDay(point.date),
      input: point.input_tokens,
      output: point.output_tokens,
    })),
    [daily],
  );
  /* 區間內完全沒用量就不畫（統計卡已是 0），有值才值得佔版面 */
  if (data.length === 0 || data.every((point) => !point.input && !point.output)) return null;
  return (
    <RrdChart
      title={t("AiApiPage.usageDailyTitle")}
      data={data}
      series={[
        { key: "input", label: t("AiApiPage.usageStatInputTokens"), color: "--color-info" },
        { key: "output", label: t("AiApiPage.usageStatOutputTokens"), color: "--color-success" },
      ]}
      height={180}
    />
  );
}

/* ── Usage: by-model / by-call-type breakdown ── */
function UsageBreakdown({ icon, title, entries, formatter }) {
  const { t } = useTranslation("ai");
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
            <span>{t("AiApiPage.callCount", { count: stats.requests ?? stats.calls ?? 0 })}</span>
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
  const { t } = useTranslation("ai");
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
    { value: "7d", label: t("AiApiPage.preset7d") },
    { value: "30d", label: t("AiApiPage.preset30d") },
    { value: "90d", label: t("AiApiPage.preset90d") },
  ];

  return (
    <div className={styles.usageTab}>
      <div className={styles.usageDateRow} data-guide="ai-usage-panel">
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
        <LoadingState />
      ) : (
        <>
          {/* Proxy usage */}
          <div className={styles.usagePanel} data-guide="ai-proxy-usage">
            <div className={styles.usagePanelHeader}>
              <h3 className={styles.usagePanelTitle}>{t("AiApiPage.usageProxyTitle")}</h3>
              <p className={styles.usagePanelDesc}>{t("AiApiPage.usageProxyDesc")}</p>
            </div>
            {proxyError ? (
              <p className={styles.textDanger}>{t("AiApiPage.usageProxyError")}</p>
            ) : proxyData ? (
              <>
                <div className={styles.usageStatsGrid}>
                  <UsageStatCard label={t("AiApiPage.usageStatTotalCalls")} value={proxyData.total_requests} />
                  <UsageStatCard label={t("AiApiPage.usageStatInputTokens")} value={formatTokens(proxyData.total_input_tokens)} />
                  <UsageStatCard label={t("AiApiPage.usageStatOutputTokens")} value={formatTokens(proxyData.total_output_tokens)} />
                </div>
                <DailyUsageChart daily={proxyData.daily} />
                <UsageBreakdown
                  icon="bar_chart"
                  title={t("AiApiPage.usageBreakdownByModel")}
                  entries={proxyData.by_model}
                  formatter={formatModelDisplay}
                />
              </>
            ) : (
              <p className={styles.noData}>{t("AiApiPage.usageProxyEmpty")}</p>
            )}
          </div>

          {/* Template usage */}
          <div className={styles.usagePanel} data-guide="ai-template-usage">
            <div className={styles.usagePanelHeader}>
              <h3 className={styles.usagePanelTitle}>{t("AiApiPage.usageTemplateTitle")}</h3>
              <p className={styles.usagePanelDesc}>{t("AiApiPage.usageTemplateDesc")}</p>
            </div>
            {templateError ? (
              <p className={styles.textDanger}>{t("AiApiPage.usageTemplateError")}</p>
            ) : templateData ? (
              <>
                <div className={styles.usageStatsGrid}>
                  <UsageStatCard label={t("AiApiPage.usageStatTotalCalls")} value={templateData.total_calls} />
                  <UsageStatCard label={t("AiApiPage.usageStatInputTokens")} value={formatTokens(templateData.total_input_tokens)} />
                  <UsageStatCard label={t("AiApiPage.usageStatOutputTokens")} value={formatTokens(templateData.total_output_tokens)} />
                </div>
                <DailyUsageChart daily={templateData.daily} />
                <UsageBreakdown
                  icon="auto_awesome"
                  title={t("AiApiPage.usageBreakdownByCallType")}
                  entries={templateData.by_call_type}
                />
              </>
            ) : (
              <p className={styles.noData}>{t("AiApiPage.usageTemplateEmpty")}</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ───────────────────────────── Main ───────────────────────────── */

export default function AiApiPage() {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const [activeTab, setActiveTab] = useState("keys");

  const DURATION_OPTIONS = [
    { value: "1h", label: t("AiApiPage.durationOption1h") },
    { value: "1d", label: t("AiApiPage.durationOption1d") },
    { value: "7d", label: t("AiApiPage.durationOption7d") },
    { value: "30d", label: t("AiApiPage.durationOption30d") },
    { value: "never", label: t("AiApiPage.durationOptionNever") },
  ];

  const TABS = [
    { key: "apply",   label: t("AiApiPage.tabApply"),   icon: "send" },
    { key: "keys",    label: "API Keys",                icon: "vpn_key" },
    { key: "records", label: t("AiApiPage.tabRecords"), icon: "history" },
    { key: "usage",   label: t("AiApiPage.tabUsage"),   icon: "trending_up" },
  ];

  /* ── Form state ── */
  const [apiKeyName, setApiKeyName] = useState("test");
  const [purpose, setPurpose] = useState("");
  const [duration, setDuration] = useState("never");
  const [submitting, setSubmitting] = useState(false);
  const [purposeInvalid, setPurposeInvalid] = useState(false);
  const purposeInputRef = useRef(null);

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
      toast.error(e?.message ?? t("AiApiPage.loadError"));
    } finally {
      setLoading(false);
    }
  }, [toast, t]);

  useEffect(() => { load(); }, [load]);

  const activeCredentials = credentials.filter((c) => !c.revoked_at && !isExpired(c.expires_at));
  const expiredCredentials = credentials.filter((c) => !c.revoked_at && isExpired(c.expires_at));
  const approvedRequests = requests.filter((r) => r.status === "approved");

  /* ── Submit request ── */
  const handleSubmit = async () => {
    if (purpose.trim().length < 10) {
      setPurposeInvalid(true);
      focusInvalidField(purposeInputRef.current);
      return;
    }
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
      toast.success(t("AiApiPage.submitSuccess"));
      load();
    } catch (e) {
      toast.error(e?.message ?? t("AiApiPage.submitError"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.page}>
      {/* ── Header ── */}
      <PageHeader
        title="AI API"
        subtitle={t("AiApiPage.pageSubtitle")}
      />

      {/* ── Stat cards ── */}
      <div className={styles.statRow} data-guide="ai-stats">
        <StatCard label={t("AiApiPage.statLabelRequests")} value={requests.length} icon="history" />
        <StatCard label={t("AiApiPage.statLabelActiveKeys")} value={activeCredentials.length} icon="key" iconCls="statIconOk" />
        <StatCard label={t("AiApiPage.statLabelExpiredKeys")} value={expiredCredentials.length} icon="cancel" iconCls="statIconErr" />
        <StatCard label={t("AiApiPage.statLabelApprovedRequests")} value={approvedRequests.length} icon="check_circle" iconCls="statIconOk" />
      </div>

      {/* ── Tabs ── */}
      <div className={styles.tabs} data-guide="ai-tabs" role="tablist" aria-label={t("AiApiPage.tabsAriaLabel")}>
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={`${styles.tab} ${activeTab === tab.key ? styles.tabActive : ""}`}
            onClick={() => setActiveTab(tab.key)}
            data-guide-tab={tab.key}
            data-guide-has-content={tab.key !== "keys" || credentials.length > 0 ? "true" : "false"}
            role="tab"
            aria-selected={activeTab === tab.key}
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
              <h2 className={styles.panelTitle} data-guide="ai-form">{t("AiApiPage.applyPanelTitle")}</h2>
              <p className={styles.panelDesc}>{t("AiApiPage.applyPanelDesc")}</p>
            </div>

            <div className={styles.formGroup}>
              <label className={styles.formLabel} htmlFor="ai-key-name">{t("AiApiPage.formLabelKeyName")}</label>
              <input
                id="ai-key-name"
                type="text"
                className={styles.formInput}
                value={apiKeyName}
                onChange={(e) => setApiKeyName(e.target.value)}
                placeholder={t("AiApiPage.formPlaceholderKeyName")}
                maxLength={20}
                data-guide="ai-apply-name"
              />
            </div>

            <div className={styles.formGroup}>
              <label className={styles.formLabel} htmlFor="ai-purpose">{t("AiApiPage.formLabelPurpose")}</label>
              <textarea
                id="ai-purpose"
                ref={purposeInputRef}
                className={`${styles.formTextarea} ${purposeInvalid ? styles.fieldInvalid : ""}`}
                value={purpose}
                onChange={(e) => { setPurpose(e.target.value); setPurposeInvalid(false); }}
                placeholder={t("AiApiPage.formPlaceholderPurpose")}
                rows={5}
                data-guide="ai-apply-purpose"
              />
            </div>

            <div className={styles.formGroup}>
              <label className={styles.formLabel} htmlFor="ai-duration">{t("AiApiPage.formLabelDuration")}</label>
              <select
                id="ai-duration"
                className={styles.formSelect}
                value={duration}
                onChange={(e) => setDuration(e.target.value)}
                data-guide="ai-apply-duration"
              >
                {DURATION_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>

            <div className={styles.formFooter}>
              <span className={styles.formHint}>{t("AiApiPage.formHintPurpose")}</span>
              <button
                type="button"
                className={styles.btnPrimary}
                onClick={handleSubmit}
                disabled={submitting}
                data-guide="ai-submit"
              >
                <MIcon name="send" size={16} />
                {submitting ? t("AiApiPage.submitButtonSubmitting") : t("AiApiPage.submitButton")}
              </button>
            </div>
          </div>
        )}

        {/* ---- Tab: API Keys ---- */}
        {activeTab === "keys" && (
          <div className={styles.panel}>
            <div className={styles.panelHeader}>
              <h2 className={styles.panelTitle} data-guide="ai-keys-panel">{t("AiApiPage.keysPanelTitle")}</h2>
              <p className={styles.panelDesc}>{t("AiApiPage.keysPanelDesc")}</p>
            </div>
            {loading ? (
              <LoadingState />
            ) : credentials.length === 0 ? (
              <EmptyState
                icon="vpn_key"
                title={t("AiApiPage.keysEmptyTitle")}
                guideId="ai-keys-content"
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
              <h2 className={styles.panelTitle} data-guide="ai-records-panel">{t("AiApiPage.recordsPanelTitle")}</h2>
              <p className={styles.panelDesc}>{t("AiApiPage.recordsPanelDesc")}</p>
            </div>
            {loading ? (
              <LoadingState />
            ) : requests.length === 0 ? (
              <EmptyState
                icon="history"
                title={t("AiApiPage.recordsEmptyTitle")}
                guideId="ai-records-content"
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
