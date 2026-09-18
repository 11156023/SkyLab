import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./AiApiPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { AiApiService } from "../../../services/aiApi";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
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

export function buildAiProxyBaseUrl(baseUrl) {
  const root = String(baseUrl ?? "").trim().replace(/\/+$/, "");
  if (!root) return "";
  if (root.endsWith("/api/v1/ai-proxy")) return root;
  if (root.endsWith("/api/v1")) return `${root}/ai-proxy`;
  return `${root}/api/v1/ai-proxy`;
}

export function buildApiExample(language, baseUrl) {
  const proxyBaseUrl = buildAiProxyBaseUrl(baseUrl) || "BASE_URL";
  const endpoint = `${proxyBaseUrl}/responses`;

  if (language === "python") {
    return `from openai import OpenAI

client = OpenAI(
    api_key="YOUR_API_KEY",
    base_url="${proxyBaseUrl}",
)

response = client.responses.create(
    model="MODEL_NAME",
    input="INPUT",
)

print(response.output_text)`;
  }

  if (language === "cmd") {
    return `curl -X POST "${endpoint}" ^
  -H "Authorization: Bearer YOUR_API_KEY" ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"MODEL_NAME\",\"input\":\"INPUT\"}"`;
  }

  return `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "YOUR_API_KEY",
  baseURL: "${proxyBaseUrl}",
});

const response = await client.responses.create({
  model: "MODEL_NAME",
  input: "INPUT",
});

console.log(response.output_text);`;
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
  const deprecated = inactive || expired;
  const proxyBaseUrl = buildAiProxyBaseUrl(item.base_url);

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
    <div
      className={`${styles.credCard} ${deprecated ? styles.credCardDeprecated : ""}`}
      data-guide="ai-keys-content"
    >
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
              <span className={`${styles.credName} ${deprecated ? styles.credNameDeprecated : ""}`}>
                {item.api_key_name}
              </span>
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
          <div className={styles.credFieldValueRow}>
            <div className={styles.credFieldValue} title={proxyBaseUrl}>{proxyBaseUrl}</div>
            <button
              type="button"
              className={styles.credCopyButton}
              onClick={() => copy("Base URL", proxyBaseUrl)}
              aria-label={t("AiApiPage.copyBaseUrl")}
              title={t("AiApiPage.copyBaseUrl")}
            >
              <MIcon name="content_copy" size={15} />
            </button>
          </div>
        </div>
        <div className={styles.credField}>
          <div className={styles.credFieldLabel}>
            <MIcon name="vpn_key" size={14} /> API Key
          </div>
          <div className={`${styles.credFieldValue} ${deprecated ? styles.credValueDeprecated : ""}`}>
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
        <button type="button" className={styles.btnOutline} onClick={() => copy("API Key", item.api_key)}>
          <MIcon name="content_copy" size={16} /> API Key
        </button>
        {/* 重新產生金鑰是破壞性動作（舊金鑰立即失效），不叫「刷新」也不長得像刷新 */}
        <button type="button" className={`${styles.btnOutline} ${styles.btnOutlineDanger}`} onClick={doRotate} disabled={inactive || busy}>
          <MIcon name="autorenew" size={16} /> {t("AiApiPage.actionRotate")}
        </button>
        <button type="button" className={`${styles.btnOutline} ${styles.btnOutlineDanger}`} onClick={doDelete} disabled={busy}>
          <MIcon name="delete" size={16} /> {t("AiApiPage.actionDelete")}
        </button>
      </div>
    </div>
  );
}

/* ── API documentation ── */
function ApiDocsTab({ credentials }) {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const [language, setLanguage] = useState("javascript");
  const credential = credentials.find((item) => !item.revoked_at && !isExpired(item.expires_at))
    ?? credentials[0];
  const baseUrl = buildAiProxyBaseUrl(credential?.base_url);
  const endpoint = baseUrl ? `${baseUrl}/responses` : "BASE_URL/responses";
  const code = buildApiExample(language, credential?.base_url);
  const languages = [
    { key: "javascript", label: "JavaScript" },
    { key: "python", label: "Python" },
    { key: "cmd", label: "CMD / cURL" },
  ];

  const copy = async (label, value) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(t("AiApiPage.copiedSuccess", { label }));
    } catch {
      toast.error(t("AiApiPage.copiedError", { label }));
    }
  };

  return (
    <div className={styles.docsLayout}>
      <section className={styles.docsPanel}>
        <div className={styles.docsIntro}>
          <span className={styles.docsEyebrow}>POST</span>
          <div>
            <h2 className={styles.docsTitle}>{t("AiApiPage.docsTitle")}</h2>
            <p className={styles.docsDescription}>{t("AiApiPage.docsDescription")}</p>
          </div>
        </div>

        <div className={styles.docsEndpointBlock}>
          <span className={styles.docsFieldLabel}>Base URL</span>
          <div className={styles.docsEndpointRow}>
            <code>{baseUrl || t("AiApiPage.docsBaseUrlUnavailable")}</code>
            <button
              type="button"
              className={styles.docsCopyButton}
              onClick={() => copy("Base URL", baseUrl)}
              disabled={!baseUrl}
            >
              <MIcon name="content_copy" size={16} />
              {t("AiApiPage.copy")}
            </button>
          </div>
        </div>

        <div className={styles.docsRequestLine}>
          <span>POST</span>
          <code>{endpoint}</code>
        </div>

        <div className={styles.docsParameters}>
          <div>
            <code>MODEL_NAME</code>
            <span>{t("AiApiPage.docsModelNameHelp")}</span>
          </div>
          <div>
            <code>INPUT</code>
            <span>{t("AiApiPage.docsInputHelp")}</span>
          </div>
        </div>
      </section>

      <section className={styles.codePanel}>
        <div className={styles.codePanelHeader}>
          <div>
            <h3>{t("AiApiPage.docsExampleTitle")}</h3>
            <p>{t("AiApiPage.docsExampleDescription")}</p>
          </div>
          <button
            type="button"
            className={styles.codeCopyButton}
            onClick={() => copy(t("AiApiPage.docsCode"), code)}
          >
            <MIcon name="content_copy" size={16} />
            {t("AiApiPage.copyCode")}
          </button>
        </div>
        <div className={styles.codeTabs} role="tablist" aria-label={t("AiApiPage.docsLanguageLabel")}>
          {languages.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={language === item.key}
              className={language === item.key ? styles.codeTabActive : styles.codeTab}
              onClick={() => setLanguage(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <pre className={styles.codeBlock}><code>{code}</code></pre>
        <p className={styles.codeHint}>
          {language === "javascript"
            ? t("AiApiPage.docsJavascriptHint")
            : language === "python"
              ? t("AiApiPage.docsPythonHint")
              : t("AiApiPage.docsCmdHint")}
        </p>
      </section>
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
    <div
      className={`${styles.requestRow} ${styles[`requestRow_${st}`] ?? ""}`}
      data-guide="ai-records-content"
    >
      <div className={styles.requestInfo}>
        <span className={`${styles.requestName} ${styles[`requestName_${st}`] ?? ""}`}>
          {item.api_key_name}
        </span>
        <span className={`${styles.badge} ${styles[`badge_${st}`]}`}>
          <span className={styles.dot} />
          {statusLabel(item.status)}
        </span>
      </div>
      <p className={`${styles.requestPurpose} ${styles[`requestPurpose_${st}`] ?? ""}`}>{item.purpose}</p>
      <div className={styles.requestMeta}>
        <span>{t("AiApiPage.requestMetaApply", { value: formatDateTime(item.created_at) })}</span>
        <span>{t("AiApiPage.requestMetaReview", { value: formatDateTime(item.reviewed_at, t("AiApiPage.requestNotReviewed")) })}</span>
        {item.review_comment && <span className={st === "rejected" ? styles.textDanger : ""}>{t("AiApiPage.requestMetaComment", { value: item.review_comment })}</span>}
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
            <span>{t("AiApiPage.callCount", { count: stats.calls ?? stats.requests ?? 0 })}</span>
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

/* ── Usage record row ── */
function UsageRecordRow({ item }) {
  const { t } = useTranslation("ai");

  const succeeded = ["success", "ok", "200", 200].includes(item.status);
  const statusCls = succeeded ? "success" : "error";
  const statusLabel =
    succeeded
      ? t("AiApiPage.recordStatusSuccess")
      : t("AiApiPage.recordStatusError");

  const callTypeLabels = {
    chat: "AiApiPage.callTypeChat",
    recommend: "AiApiPage.callTypeRecommend",
    chat_completion: "AiApiPage.callTypeChatCompletion",
  };
  const callTypeKey = callTypeLabels[item.call_type];

  return (
    <div className={styles.usageRecordRow}>
      <div className={styles.usageRecordTop}>
        <div className={styles.usageRecordHeading}>
          <span className={styles.usageRecordTime}>{formatDateTime(item.created_at)}</span>
          <span className={styles.usageRecordId}>ID {item.id}</span>
        </div>
        <span className={`${styles.badge} ${styles[`badge_${statusCls}`]}`}>
          <span className={styles.dot} />
          {statusLabel}
        </span>
      </div>
      <div className={styles.usageRecordDetails}>
        <div>
          <span>{t("AiApiPage.recordKey")}</span>
          <strong>{item.api_key_name} <small>{item.api_key_prefix}…</small></strong>
        </div>
        <div>
          <span>{t("AiApiPage.recordModel")}</span>
          <strong className={styles.usageRecordModel}>{formatModelDisplay(item.model_name)}</strong>
        </div>
        <div>
          <span>{t("AiApiPage.recordRequestType")}</span>
          <strong className={styles.usageRecordType}>{callTypeKey ? t(callTypeKey) : item.call_type || "—"}</strong>
        </div>
        <div>
          <span>{t("AiApiPage.recordInputTokens")}</span>
          <strong>{formatTokens(item.input_tokens)}</strong>
        </div>
        <div>
          <span>{t("AiApiPage.recordOutputTokens")}</span>
          <strong>{formatTokens(item.output_tokens)}</strong>
        </div>
        <div>
          <span>{t("AiApiPage.recordTotalTokens")}</span>
          <strong>{formatTokens(item.total_tokens ?? ((item.input_tokens ?? 0) + (item.output_tokens ?? 0)))}</strong>
        </div>
        <div>
          <span>{t("AiApiPage.recordLatency")}</span>
          <strong>{item.request_duration_ms == null ? "—" : t("AiApiPage.recordDuration", { seconds: (item.request_duration_ms / 1000).toFixed(1) })}</strong>
        </div>
      </div>
      {item.error_message && (
        <div className={styles.usageRecordError}>
          <MIcon name="error_outline" size={15} />
          <span>{item.error_message}</span>
        </div>
      )}
    </div>
  );
}

/* ── My Usage Tab ── */
const USAGE_RECORD_PAGE_SIZE = 20;

function MyUsageTab() {
  const { t } = useTranslation("ai");
  const [preset, setPreset] = useState("30d");
  const [usageData, setUsageData] = useState(null);
  const [usageError, setUsageError] = useState(false);
  const [records, setRecords] = useState([]);
  const [recordsCount, setRecordsCount] = useState(0);
  const [recordsError, setRecordsError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  const { start, end } = useMemo(() => {
    const now = new Date();
    const s = new Date(now);
    if (preset === "7d") s.setDate(s.getDate() - 7);
    else if (preset === "30d") s.setDate(s.getDate() - 30);
    else s.setDate(s.getDate() - 90);
    return { start: s.toISOString(), end: now.toISOString() };
  }, [preset]);

  const load = useCallback(async () => {
    setLoading(true);
    setUsageError(false);
    setRecordsError(false);
    const [usageRes, recRes] = await Promise.allSettled([
      AiApiService.getMyUsage({ start_date: start, end_date: end }),
      AiApiService.getMyUsageRecords({ start_date: start, end_date: end, limit: USAGE_RECORD_PAGE_SIZE }),
    ]);
    if (usageRes.status === "fulfilled") setUsageData(usageRes.value);
    else setUsageError(true);
    if (recRes.status === "fulfilled") {
      setRecords(recRes.value?.data ?? []);
      setRecordsCount(recRes.value?.count ?? 0);
    } else {
      setRecords([]);
      setRecordsCount(0);
      setRecordsError(true);
    }
    setLoading(false);
  }, [start, end]);

  useEffect(() => { load(); }, [load]);

  const hasMoreRecords = !recordsError && records.length > 0 && records.length < recordsCount;

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const res = await AiApiService.getMyUsageRecords({
        start_date: start,
        end_date: end,
        skip: records.length,
        limit: USAGE_RECORD_PAGE_SIZE,
      });
      setRecords((prev) => [...prev, ...(res?.data ?? [])]);
      setRecordsCount(res?.count ?? 0);
    } catch {
      /* 維持現有清單，不覆蓋成功資料 */
    } finally {
      setLoadingMore(false);
    }
  };

  const PRESETS = [
    { value: "7d", label: t("AiApiPage.preset7d") },
    { value: "30d", label: t("AiApiPage.preset30d") },
    { value: "90d", label: t("AiApiPage.preset90d") },
  ];

  const hasAnyUsage = (usageData?.total_requests ?? 0) > 0;

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
        <span className={styles.usageDateRange}>{start.slice(0, 10)} ~ {end.slice(0, 10)}</span>
      </div>

      {loading ? (
        <LoadingState />
      ) : (
        <>
          {/* ── 申請金鑰 API 用量總覽 ── */}
          <div className={styles.usagePanel} data-guide="ai-route-usage">
            <div className={styles.usagePanelHeader}>
              <h3 className={styles.usagePanelTitle}>{t("AiApiPage.usageTitle")}</h3>
            </div>
            {usageError ? (
              <p className={styles.textDanger}>{t("AiApiPage.usageError")}</p>
            ) : (
              <>
                <div className={styles.usageStatsGrid}>
                  <UsageStatCard label={t("AiApiPage.usageStatTotalCalls")} value={usageData?.total_requests ?? 0} />
                  <UsageStatCard label={t("AiApiPage.usageStatInputTokens")} value={formatTokens(usageData?.total_input_tokens)} />
                  <UsageStatCard label={t("AiApiPage.usageStatOutputTokens")} value={formatTokens(usageData?.total_output_tokens)} />
                </div>
                <DailyUsageChart daily={usageData?.daily} />
                <UsageBreakdown
                  icon="bar_chart"
                  title={t("AiApiPage.usageBreakdownByModel")}
                  entries={usageData?.by_model}
                  formatter={formatModelDisplay}
                />
                {!usageError && !hasAnyUsage && (
                  <p className={styles.noData}>{t("AiApiPage.usageEmpty")}</p>
                )}
              </>
            )}
          </div>

          {/* ── 細項呼叫紀錄 ── */}
          <div className={styles.usagePanel} data-guide="ai-usage-records">
            <div className={styles.usagePanelHeader}>
              <h3 className={styles.usagePanelTitle}>{t("AiApiPage.usageRecordsTitle")}</h3>
            </div>
            {recordsError ? (
              <p className={styles.textDanger}>{t("AiApiPage.usageRecordsError")}</p>
            ) : records.length === 0 ? (
              <p className={styles.noData}>{t("AiApiPage.usageRecordsEmpty")}</p>
            ) : (
              <>
                <div className={styles.usageRecordList}>
                  {records.map((item) => (
                    <UsageRecordRow key={`${item.route}-${item.id}`} item={item} />
                  ))}
                </div>
                {hasMoreRecords && (
                  <div className={styles.usageRecordsMoreRow}>
                    <button type="button" className={styles.btnOutline} onClick={loadMore} disabled={loadingMore}>
                      {loadingMore ? (
                        <>
                          <MIcon name="hourglass_empty" size={16} />
                          {t("AiApiPage.recordsLoadingMore")}
                        </>
                      ) : (
                        <>
                          <MIcon name="expand_more" size={16} />
                          {t("AiApiPage.recordsLoadMore")}
                        </>
                      )}
                    </button>
                    <span className={styles.usageDateRange}>
                      {t("AiApiPage.recordsShownCount", {
                        shown: records.length,
                        total: recordsCount,
                      })}
                    </span>
                  </div>
                )}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* ── Apply key dialog ── */
function ApplyKeyModal({
  closing = false,
  busy = false,
  apiKeyName,
  onApiKeyNameChange,
  purpose,
  onPurposeChange,
  purposeInvalid,
  purposeInputRef,
  duration,
  onDurationChange,
  durationOptions,
  onClose,
  onSubmit,
}) {
  const { t } = useTranslation("ai");

  useEffect(() => {
    const closeOnEscape = (event) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [busy, onClose]);

  return (
    <div
      className={`${styles.dialogOverlay} ${closing ? styles.dialogOverlayOut : ""}`}
      role="presentation"
      onMouseDown={() => { if (!busy) onClose(); }}
    >
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-apply-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className={styles.dialogHeader}>
          <div>
            <h2 id="ai-apply-dialog-title" className={styles.dialogTitle} data-guide="ai-form">{t("AiApiPage.applyPanelTitle")}</h2>
          </div>
          <button
            type="button"
            className={styles.dialogClose}
            onClick={onClose}
            disabled={busy}
            aria-label={t("AiApiPage.close")}
            data-guide="ai-apply-close"
          >
            <MIcon name="close" size={18} />
          </button>
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel} htmlFor="ai-key-name">{t("AiApiPage.formLabelKeyName")}</label>
          <input
            id="ai-key-name"
            type="text"
            className={styles.formInput}
            value={apiKeyName}
            onChange={(e) => onApiKeyNameChange(e.target.value)}
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
            onChange={(e) => onPurposeChange(e.target.value)}
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
            onChange={(e) => onDurationChange(e.target.value)}
            data-guide="ai-apply-duration"
          >
            {durationOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        <div className={styles.dialogFooter}>
          <span className={styles.formHint}>{t("AiApiPage.formHintPurpose")}</span>
          <div className={styles.dialogActions}>
            <button type="button" className={styles.btnOutline} onClick={onClose} disabled={busy}>
              {t("AiApiPage.cancel")}
            </button>
            <button type="button" className={styles.btnPrimary} onClick={onSubmit} disabled={busy} data-guide="ai-submit">
              <MIcon name="send" size={16} />
              {busy ? t("AiApiPage.submitButtonSubmitting") : t("AiApiPage.submitButton")}
            </button>
          </div>
        </div>
      </div>
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
    { key: "keys",    label: "API Keys" },
    { key: "docs",    label: t("AiApiPage.tabDocs") },
    { key: "records", label: t("AiApiPage.tabRecords") },
    { key: "usage",   label: t("AiApiPage.tabUsage") },
  ];

  /* ── Form state ── */
  const [apiKeyName, setApiKeyName] = useState("test");
  const [purpose, setPurpose] = useState("");
  const [duration, setDuration] = useState("never");
  const [submitting, setSubmitting] = useState(false);
  const [purposeInvalid, setPurposeInvalid] = useState(false);
  const purposeInputRef = useRef(null);
  const [showApplyModal, setShowApplyModal] = useState(false);
  const applyDialog = useDialogPresence(showApplyModal);

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
      setShowApplyModal(false);
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
      />

      {/* ── Tabs：共用 SegmentedControl，樣式對齊金鑰管理的狀態切換 ── */}
      <div data-guide="ai-tabs">
        <SegmentedControl
          ariaLabel={t("AiApiPage.tabsAriaLabel")}
          value={activeTab}
          onChange={setActiveTab}
          options={TABS.map((tab) => ({
            value: tab.key,
            label: tab.label,
            badge: tab.key === "keys" ? activeCredentials.length : tab.key === "records" ? requests.length : undefined,
            buttonProps: {
              "data-guide-tab": tab.key,
              "data-guide-has-content": tab.key !== "keys" || credentials.length > 0 ? "true" : "false",
            },
          }))}
        />
      </div>

      {/* ── Content ── */}
      <div className={styles.content}>
        {/* ---- Tab: API Keys ---- */}
        {activeTab === "keys" && (
          <div className={styles.panel}>
            <div className={styles.panelHeaderRow}>
              <div className={styles.panelHeader}>
                <h2 className={styles.panelTitle} data-guide="ai-keys-panel">{t("AiApiPage.keysPanelTitle")}</h2>
              </div>
              <button
                type="button"
                className={styles.btnAddKey}
                onClick={() => setShowApplyModal(true)}
                data-guide="ai-add-key"
              >
                <MIcon name="add" size={16} />
                {t("AiApiPage.addKeyButton")}
              </button>
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

        {/* ---- Tab: API 文件 ---- */}
        {activeTab === "docs" && <ApiDocsTab credentials={credentials} />}

        {/* ---- Tab: 申請紀錄 ---- */}
        {activeTab === "records" && (
          <div className={styles.panel}>
            <div className={styles.panelHeader}>
              <h2 className={styles.panelTitle} data-guide="ai-records-panel">{t("AiApiPage.recordsPanelTitle")}</h2>
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

      {/* ── 新增金鑰彈窗 ── */}
      {applyDialog.open && (
        <ApplyKeyModal
          closing={applyDialog.closing}
          busy={submitting}
          apiKeyName={apiKeyName}
          onApiKeyNameChange={setApiKeyName}
          purpose={purpose}
          onPurposeChange={(value) => { setPurpose(value); setPurposeInvalid(false); }}
          purposeInvalid={purposeInvalid}
          purposeInputRef={purposeInputRef}
          duration={duration}
          onDurationChange={setDuration}
          durationOptions={DURATION_OPTIONS}
          onClose={() => setShowApplyModal(false)}
          onSubmit={handleSubmit}
        />
      )}
    </div>
  );
}
