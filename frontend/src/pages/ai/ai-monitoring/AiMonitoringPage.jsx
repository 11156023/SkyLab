import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import styles from "./AiMonitoringPage.module.scss";
import MIcon from "../../../components/MIcon";
import { formatDateTime, formatTime } from "../../../utils/formatDate";
import i18n from "../../../i18n";
import LoadingState from "../../../components/LoadingState/LoadingState";
import SharedEmptyState from "../../../components/EmptyState/EmptyState";
import { AiMonitoringService } from "../../../services/aiMonitoring";
import { useToast } from "../../../hooks/useToast";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";

export function presetToRange(preset) {
  const end = new Date();
  const start = new Date();
  const days = preset === "7d" ? 7 : preset === "30d" ? 30 : 90;
  start.setDate(start.getDate() - days);
  return { startDate: start.toISOString(), endDate: end.toISOString() };
}

export function presetToBucket(preset) {
  return preset === "7d" ? "hour" : "day";
}

export function formatTokens(n) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function formatDuration(ms) {
  if (ms == null) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

export function formatModelDisplay(modelName) {
  if (!modelName) return "—";
  const trimmed = modelName.trim();
  if (!trimmed) return "—";

  const match = trimmed.match(/models--([^/]+)--([^/]+)/);
  if (match) return `${match[1]}/${match[2]}`;

  if (/^(?:[A-Za-z]:[\\/]|[\\/])/.test(trimmed)) {
    const separator = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
    const basename = separator >= 0 ? trimmed.slice(separator + 1) : trimmed;
    return basename || trimmed;
  }

  return trimmed;
}

function modelKey(modelName) {
  return formatModelDisplay(modelName).toLocaleLowerCase();
}

export function mergeModelRows(usageModels = [], runtimeModels = []) {
  const rows = new Map();

  usageModels.forEach((model) => {
    rows.set(modelKey(model.model_name), {
      ...model,
      runtime_name: null,
      runtime_status: null,
      healthy_deployments: null,
      unhealthy_deployments: null,
    });
  });

  runtimeModels.forEach((runtimeModel) => {
    const key = modelKey(runtimeModel.name);
    const usage = rows.get(key);
    rows.set(key, {
      model_name: usage?.model_name ?? runtimeModel.name,
      total_calls: usage?.total_calls ?? 0,
      total_tokens: usage?.total_tokens ?? 0,
      failed_calls: usage?.failed_calls ?? 0,
      error_rate: usage?.error_rate ?? null,
      avg_latency_ms: usage?.avg_latency_ms ?? null,
      runtime_name: runtimeModel.name,
      runtime_status: runtimeModel.status ?? "unknown",
      healthy_deployments: runtimeModel.healthy_deployments ?? 0,
      unhealthy_deployments: runtimeModel.unhealthy_deployments ?? 0,
    });
  });

  return Array.from(rows.values()).sort((a, b) => b.total_calls - a.total_calls);
}

export function isOkStatus(status) {
  return (
    status === "success" ||
    status === 200 ||
    status === "200" ||
    status === "ok"
  );
}

function formatNumber(n) {
  if (n == null) return "—";
  return new Intl.NumberFormat(i18n.language).format(n);
}

function formatPercent(value) {
  if (value == null) return "—";
  return `${Number(value).toFixed(Number(value) % 1 === 0 ? 0 : 1)}%`;
}

function formatDelta(value, unit = "%") {
  if (value == null) return null;
  const number = Number(value);
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(number % 1 === 0 ? 0 : 1)}${unit}`;
}

function formatChartTime(value, bucket) {
  if (!value) return "—";
  const date = new Date(value);
  return date.toLocaleDateString(
    i18n.language,
    bucket === "hour"
      ? { month: "numeric", day: "numeric", hour: "2-digit" }
      : { month: "numeric", day: "numeric" },
  );
}

function EmptyState({ icon, title }) {
  return <SharedEmptyState icon={icon} title={title} />;
}

function StatusBadge({ status }) {
  const { t } = useTranslation("ai");
  const ok = isOkStatus(status);
  return (
    <span className={`${styles.badge} ${ok ? styles.badge_success : styles.badge_danger}`}>
      <span className={styles.dot} />
      {ok ? t("AiMonitoringPage.statusSuccess") : t("AiMonitoringPage.statusFail")}
    </span>
  );
}

function UserCell({ email, fullName, fallback }) {
  return (
    <div className={styles.userCell}>
      <div className={styles.userName}>{fullName || fallback || "—"}</div>
      {email ? <div className={styles.userEmail}>{email}</div> : null}
    </div>
  );
}

function MetricCard({ icon, tone, label, value, detail, delta, deltaTone }) {
  return (
    <div className={styles.metricCard}>
      <div className={`${styles.metricIcon} ${styles[`metricIcon_${tone}`]}`}>
        <MIcon name={icon} size={19} />
      </div>
      <div className={styles.metricBody}>
        <span className={styles.metricLabel}>{label}</span>
        <span className={styles.metricValue}>{value}</span>
        <span className={`${styles.metricDetail} ${deltaTone ? styles[`metricDetail_${deltaTone}`] : ""}`}>
          {delta || detail}
        </span>
      </div>
    </div>
  );
}

function TrendTooltip({ active, payload, label, bucket, t }) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload;
  return (
    <div className={styles.tooltip}>
      <div className={styles.tooltipTitle}>{formatChartTime(row?.bucket_start ?? label, bucket)}</div>
      <div className={styles.tooltipRow}>
        <span>{t("AiMonitoringPage.chartCalls")}</span>
        <strong>{formatNumber(row?.total_calls)}</strong>
      </div>
      <div className={styles.tooltipRow}>
        <span>{t("AiMonitoringPage.chartFailed")}</span>
        <strong className={styles.tooltipDanger}>{formatNumber(row?.failed_calls)}</strong>
      </div>
      <div className={styles.tooltipRow}>
        <span>{t("AiMonitoringPage.chartTokens")}</span>
        <strong>{formatTokens(row?.total_tokens)}</strong>
      </div>
      <div className={styles.tooltipRow}>
        <span>{t("AiMonitoringPage.chartErrorRate")}</span>
        <strong>{formatPercent(row?.error_rate)}</strong>
      </div>
      <div className={styles.tooltipRow}>
        <span>{t("AiMonitoringPage.chartLatency")}</span>
        <strong>{formatDuration(row?.avg_latency_ms)}</strong>
      </div>
    </div>
  );
}

const TREND_METRICS = {
  calls: { dataKey: "total_calls", labelKey: "AiMonitoringPage.chartCalls", color: "var(--color-primary)", format: formatNumber, allowDecimals: false },
  tokens: { dataKey: "total_tokens", labelKey: "AiMonitoringPage.chartTokens", color: "var(--color-info)", format: formatTokens, allowDecimals: false },
  failed: { dataKey: "failed_calls", labelKey: "AiMonitoringPage.chartFailed", color: "var(--color-danger)", format: formatNumber, allowDecimals: false },
  error: { dataKey: "error_rate", labelKey: "AiMonitoringPage.chartErrorRate", color: "var(--color-danger)", format: formatPercent, allowDecimals: true },
  latency: { dataKey: "avg_latency_ms", labelKey: "AiMonitoringPage.chartLatency", color: "var(--color-info)", format: formatDuration, allowDecimals: true },
};

function TrendChart({ series, bucket, loading, metric, t }) {
  if (loading) return <LoadingState text={t("AiMonitoringPage.loadingTrend")} />;
  if (!series?.length) {
    return <EmptyState icon="show_chart" title={t("AiMonitoringPage.emptyTrendTitle")} />;
  }
  const activeMetric = TREND_METRICS[metric] ?? TREND_METRICS.calls;

  return (
    <div className={styles.chartFrame}>
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={series} margin={{ top: 12, right: 12, left: -14, bottom: 4 }}>
          <CartesianGrid stroke="var(--color-border)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="bucket_start"
            tickFormatter={(value) => formatChartTime(value, bucket)}
            tick={{ fill: "var(--color-text-muted)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            minTickGap={28}
          />
          <YAxis
            allowDecimals={activeMetric.allowDecimals}
            tickFormatter={activeMetric.format}
            tick={{ fill: "var(--color-text-muted)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={52}
          />
          <Tooltip
            content={<TrendTooltip bucket={bucket} t={t} />}
            cursor={{ fill: "var(--color-hover)", opacity: 0.45 }}
          />
          <Line
            type="monotone"
            dataKey={activeMetric.dataKey}
            name={t(activeMetric.labelKey)}
            stroke={activeMetric.color}
            strokeWidth={2.5}
            dot={false}
            activeDot={{ r: 4, strokeWidth: 0 }}
            connectNulls
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function CompactHealthPanel({ items, loading, t, onOpen }) {
  return (
    <section className={`${styles.panel} ${styles.healthPanel}`} aria-labelledby="runtime-heading">
      <div className={styles.panelHeader}>
        <div>
          <h2 id="runtime-heading" className={styles.panelTitle}>
            <MIcon name="health_and_safety" size={18} />
            {t("AiMonitoringPage.healthTitle")}
          </h2>
        </div>
        {!loading && <span className={styles.healthIssueCount} aria-label={t("AiMonitoringPage.healthIssueCount", { count: items.length })}>{items.length}</span>}
      </div>

      {loading ? (
        <LoadingState text={t("AiMonitoringPage.loadingRuntime")} />
      ) : (
        <div className={styles.healthList}>
          {items.map((item) => {
            const tone = item.tone === "critical" ? "danger" : item.tone;
            return (
              <button type="button" key={item.key} className={styles.healthRow} onClick={() => onOpen(item.target)}>
                <span className={`${styles.healthDot} ${styles[`healthDot_${tone}`]}`} />
                <span className={styles.healthCopy}>
                  <strong>{item.title}</strong>
                  <small>{item.detail}</small>
                </span>
                <MIcon name="chevron_right" size={17} />
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

function ModelRuntimeBadge({ status, t }) {
  if (!status) return <span className={styles.runtimeUnavailable}>—</span>;
  const normalized = ["online", "degraded", "offline"].includes(status) ? status : "unknown";
  return (
    <span className={`${styles.runtimeBadge} ${styles[`runtimeBadge_${normalized}`]}`}>
      <span className={styles.dot} />
      {t(`AiMonitoringPage.modelStatus_${normalized}`)}
    </span>
  );
}

export function buildAttentionItems({ overview, runtime, overviewError, runtimeError }, t) {
  const rows = [];
  if (overviewError) rows.push({ key: "usage", tone: "critical", icon: "sync_problem", title: t("AiMonitoringPage.attentionUsageTitle"), detail: t("AiMonitoringPage.summaryLoadError"), target: "api" });
  const gateway = runtime?.gateway;
  if (runtimeError || (gateway && (gateway.status !== "available" || !gateway.readiness))) {
    rows.push({ key: "gateway", tone: "critical", icon: "cloud_off", title: t("AiMonitoringPage.attentionGatewayTitle"), detail: t("AiMonitoringPage.attentionGatewayDesc"), target: "runtime" });
  }
  const offline = runtime?.summary?.offline ?? 0;
  const degraded = runtime?.summary?.degraded ?? 0;
  if (offline + degraded > 0) rows.push({ key: "models", tone: offline > 0 ? "critical" : "warning", icon: "model_training", title: t("AiMonitoringPage.attentionModelsTitle", { count: offline + degraded }), detail: t("AiMonitoringPage.attentionModelsDesc", { offline, degraded }), target: "models" });
  const errorRate = overview?.summary?.error_rate;
  const errorDelta = overview?.comparison?.error_rate_delta;
  if (!overviewError && errorRate != null && (errorRate >= 5 || errorDelta > 0.5)) {
    rows.push({ key: "errors", tone: errorRate >= 10 ? "critical" : "warning", icon: "error_outline", title: t("AiMonitoringPage.attentionErrorRateTitle", { rate: formatPercent(errorRate) }), detail: errorDelta != null ? t("AiMonitoringPage.attentionErrorRateDesc", { delta: formatDelta(errorDelta, "pp") }) : t("AiMonitoringPage.attentionFailedCalls", { count: overview?.summary?.failed_calls ?? 0 }), target: "api-errors" });
  }
  return rows;
}

function DetailSummary({ summary, t }) {
  const items = [
    { key: "success", icon: "check_circle", tone: "success", label: t("AiMonitoringPage.successfulCalls"), value: formatNumber(summary?.successful_calls) },
    { key: "failed", icon: "error_outline", tone: "danger", label: t("AiMonitoringPage.statFailedCalls"), value: formatNumber(summary?.failed_calls) },
    { key: "users", icon: "groups", tone: "info", label: t("AiMonitoringPage.activeUsers"), value: formatNumber(summary?.active_users) },
  ];
  return (
    <div className={styles.detailSummary} aria-label={t("AiMonitoringPage.detailSummaryTitle")}>
      {items.map((item) => (
        <div className={styles.detailSummaryItem} key={item.key}>
          <span className={`${styles.detailSummaryIcon} ${styles[`detailSummaryIcon_${item.tone}`]}`}><MIcon name={item.icon} size={17} /></span>
          <span><small>{item.label}</small><strong>{item.value}</strong></span>
        </div>
      ))}
    </div>
  );
}

function DetailTable({ tab, calls, users, models, runtimeModels, query, statusFilter, onModelSelect, t }) {
  const q = query.trim().toLowerCase();

  if (tab === "models") {
    const visibleModels = mergeModelRows(models, runtimeModels).filter((model) => (
      !q
      || model.model_name.toLowerCase().includes(q)
      || (model.runtime_name ?? "").toLowerCase().includes(q)
      || (model.runtime_status ?? "").toLowerCase().includes(q)
    ));
    if (!visibleModels.length) return <EmptyState icon="model_training" title={t("AiMonitoringPage.emptyModelBreakdown")} />;
    return <div className={styles.tableWrap}><table className={styles.table}>
      <thead><tr><th className={styles.th}>{t("AiMonitoringPage.colModel")}</th><th className={styles.th}>{t("AiMonitoringPage.colRuntimeStatus")}</th><th className={styles.th}>{t("AiMonitoringPage.colDeployments")}</th><th className={styles.th}>{t("AiMonitoringPage.colCallCount")}</th><th className={styles.th}>{t("AiMonitoringPage.colTokensTotal")}</th><th className={styles.th}>{t("AiMonitoringPage.colFailRate")}</th><th className={styles.th}>{t("AiMonitoringPage.colAvgLatency")}</th></tr></thead>
      <tbody>{visibleModels.map((model) => <tr key={`${model.model_name}:${model.runtime_name ?? "usage"}`} className={styles.tr}><td className={`${styles.td} ${styles.monoCell}`}>{model.total_calls > 0 ? <button type="button" className={styles.modelDrilldown} onClick={() => onModelSelect(model.model_name)} title={t("AiMonitoringPage.viewModelCalls", { model: formatModelDisplay(model.model_name) })}><span>{formatModelDisplay(model.model_name)}</span><MIcon name="arrow_forward" size={15} /></button> : <span>{formatModelDisplay(model.model_name)}</span>}</td><td className={styles.td}><ModelRuntimeBadge status={model.runtime_status} t={t} /></td><td className={styles.td}>{model.runtime_status ? t("AiMonitoringPage.deploymentCount", { healthy: model.healthy_deployments, unhealthy: model.unhealthy_deployments }) : "—"}</td><td className={`${styles.td} ${styles.numericCell}`}>{formatNumber(model.total_calls)}</td><td className={`${styles.td} ${styles.numericCell}`}>{formatTokens(model.total_tokens)}</td><td className={`${styles.td} ${styles.numericCell}`}>{formatPercent(model.error_rate)}</td><td className={`${styles.td} ${styles.numericCell}`}>{formatDuration(model.avg_latency_ms)}</td></tr>)}</tbody>
    </table></div>;
  }

  if (tab === "users") {
    const visibleUsers = (users ?? []).filter((user) => {
      if (!q) return true;
      return (user.user_email ?? "").toLowerCase().includes(q)
        || (user.user_full_name ?? "").toLowerCase().includes(q);
    });
    if (!visibleUsers.length) return <EmptyState icon="groups" title={t("AiMonitoringPage.emptyUsersTitle")} />;
    return (
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead><tr>
            <th className={styles.th}>{t("AiMonitoringPage.colUser")}</th>
            <th className={styles.th}>{t("AiMonitoringPage.colCallCount")}</th>
            <th className={styles.th}>{t("AiMonitoringPage.colTokensTotal")}</th>
            <th className={styles.th}>{t("AiMonitoringPage.colAvgLatency")}</th>
            <th className={styles.th}>{t("AiMonitoringPage.colFailRate")}</th>
          </tr></thead>
          <tbody>{visibleUsers.map((user) => {
            const totalCalls = user.proxy_calls ?? 0;
            const totalTokens = (user.proxy_input_tokens ?? 0) + (user.proxy_output_tokens ?? 0);
            return <tr key={user.user_id} className={styles.tr}>
              <td className={styles.td}><UserCell email={user.user_email} fullName={user.user_full_name} fallback={user.user_id} /></td>
              <td className={`${styles.td} ${styles.numericCell}`}>{formatNumber(totalCalls)}</td>
              <td className={`${styles.td} ${styles.numericCell}`}>{formatTokens(totalTokens)}</td>
              <td className={`${styles.td} ${styles.numericCell}`}>{formatDuration(user.avg_latency_ms)}</td>
              <td className={`${styles.td} ${styles.numericCell}`}>{formatPercent(user.error_rate)}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>
    );
  }

  const visibleCalls = (calls ?? []).filter((call) => {
    if (statusFilter === "success" && !isOkStatus(call.status)) return false;
    if (statusFilter === "error" && isOkStatus(call.status)) return false;
    if (!q) return true;
    return (call.user_email ?? "").toLowerCase().includes(q)
      || (call.user_full_name ?? "").toLowerCase().includes(q)
      || (call.model_name ?? "").toLowerCase().includes(q)
      || (call.request_type ?? "").toLowerCase().includes(q);
  });

  if (!visibleCalls.length) return <EmptyState icon="analytics" title={t("AiMonitoringPage.emptyCallsTitle")} />;
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead><tr>
          <th className={styles.th}>{t("AiMonitoringPage.colTime")}</th>
          <th className={styles.th}>{t("AiMonitoringPage.colUser")}</th>
          <th className={styles.th}>{t("AiMonitoringPage.colModel")}</th>
          <th className={styles.th}>{t("AiMonitoringPage.colType")}</th>
          <th className={styles.th}>{t("AiMonitoringPage.colInput")}</th>
          <th className={styles.th}>{t("AiMonitoringPage.colOutput")}</th>
          <th className={styles.th}>{t("AiMonitoringPage.colDuration")}</th>
          <th className={styles.th}>{t("AiMonitoringPage.colStatus")}</th>
        </tr></thead>
        <tbody>{visibleCalls.map((call) => <tr key={call.id} className={styles.tr}>
          <td className={styles.td}>{formatDateTime(call.created_at)}</td>
          <td className={styles.td}><UserCell email={call.user_email} fullName={call.user_full_name} fallback={call.user_id} /></td>
          <td className={`${styles.td} ${styles.monoCell}`} title={call.model_name}>{formatModelDisplay(call.model_name)}</td>
          <td className={styles.td}>{call.request_type ?? "—"}</td>
          <td className={`${styles.td} ${styles.numericCell}`}>{formatTokens(call.input_tokens ?? 0)}</td>
          <td className={`${styles.td} ${styles.numericCell}`}>{formatTokens(call.output_tokens ?? 0)}</td>
          <td className={`${styles.td} ${styles.numericCell}`}>{formatDuration(call.request_duration_ms)}</td>
          <td className={styles.td}><StatusBadge status={call.status} /></td>
        </tr>)}</tbody>
      </table>
    </div>
  );
}

export default function AiMonitoringPage() {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const navigate = useNavigate();
  const [preset, setPreset] = useState("7d");
  const [trendMetric, setTrendMetric] = useState("calls");
  const [detailTab, setDetailTab] = useState("models");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [overview, setOverview] = useState(null);
  const [runtime, setRuntime] = useState(null);
  const [proxyCalls, setProxyCalls] = useState([]);
  const [users, setUsers] = useState([]);
  const [counts, setCounts] = useState({ proxy: 0, users: 0 });
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [runtimeLoading, setRuntimeLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(true);
  const [overviewError, setOverviewError] = useState(false);
  const [runtimeError, setRuntimeError] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [detailFocusRequest, setDetailFocusRequest] = useState(0);
  const detailSectionRef = useRef(null);
  const modelRows = useMemo(
    () => mergeModelRows(overview?.model_breakdown, runtime?.models),
    [overview?.model_breakdown, runtime?.models],
  );

  const PRESETS = [
    { value: "7d", label: t("AiMonitoringPage.preset7d") },
    { value: "30d", label: t("AiMonitoringPage.preset30d") },
    { value: "90d", label: t("AiMonitoringPage.preset90d") },
  ];
  const DETAIL_TABS = [
    { key: "models", label: t("AiMonitoringPage.tabModels"), icon: "model_training", count: modelRows.length },
    { key: "proxy", label: t("AiMonitoringPage.tabProxy"), icon: "swap_horiz", count: counts.proxy },
    { key: "users", label: t("AiMonitoringPage.tabUsers"), icon: "groups", count: counts.users },
  ];
  const STATUS_FILTERS = [
    { value: "all", label: t("AiMonitoringPage.statusFilterAll") },
    { value: "success", label: t("AiMonitoringPage.statusSuccess") },
    { value: "error", label: t("AiMonitoringPage.statusFail") },
  ];
  const TREND_OPTIONS = [
    { value: "calls", label: t("AiMonitoringPage.chartCalls") },
    { value: "tokens", label: t("AiMonitoringPage.chartTokens") },
    { value: "failed", label: t("AiMonitoringPage.chartFailed") },
    { value: "error", label: t("AiMonitoringPage.chartErrorRate") },
    { value: "latency", label: t("AiMonitoringPage.chartLatency") },
  ];

  const load = useCallback(async (silent = false) => {
    if (!silent) {
      setOverviewLoading(true);
      setRuntimeLoading(true);
      setDetailLoading(true);
    }
    const range = presetToRange(preset);
    const shared = { ...range, limit: 100 };

    // The overview and runtime cards are independent of the detail tables.
    // Resolve each group separately so a slow/unreachable gateway does not
    // keep already available usage data behind the page-level loading state.
    const overviewRequest = AiMonitoringService.overview({
      ...range,
      bucket: presetToBucket(preset),
      compare: true,
      source: "api_key",
    })
      .then((value) => {
        setOverview(value);
        setOverviewError(false);
      })
      .catch(() => {
        setOverviewError(true);
        if (!silent) toast.error(t("AiMonitoringPage.loadError"));
      })
      .finally(() => setOverviewLoading(false));

    const runtimeRequest = AiMonitoringService.runtime()
      .then((value) => {
        setRuntime(value);
        setRuntimeError(false);
      })
      .catch(() => {
        setRuntimeError(true);
      })
      .finally(() => setRuntimeLoading(false));

    const detailRequest = Promise.allSettled([
      AiMonitoringService.listProxyCalls(shared),
      AiMonitoringService.listUsersUsage({ ...shared, source: "api_key" }),
    ]).then(([proxyResult, usersResult]) => {
      if (proxyResult.status === "fulfilled") {
        setProxyCalls(proxyResult.value?.data ?? []);
        setCounts((current) => ({ ...current, proxy: proxyResult.value?.count ?? proxyResult.value?.data?.length ?? 0 }));
      }
      if (usersResult.status === "fulfilled") {
        setUsers(usersResult.value?.data ?? []);
        setCounts((current) => ({ ...current, users: usersResult.value?.count ?? usersResult.value?.data?.length ?? 0 }));
      }
    }).finally(() => {
      setDetailLoading(false);
    });

    await Promise.allSettled([overviewRequest, runtimeRequest, detailRequest]);
    setLastUpdated(new Date());
  }, [preset, t, toast]);

  useEffect(() => { load(); }, [load]);
  useAutoRefresh(() => load(true));

  useEffect(() => {
    if (!detailFocusRequest) return undefined;
    const frame = window.requestAnimationFrame(() => {
      detailSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [detailFocusRequest, detailTab, statusFilter]);

  const summary = overview?.summary;
  const comparison = overview?.comparison;
  const attentionItems = useMemo(
    () => buildAttentionItems({ overview, runtime, overviewError, runtimeError }, t),
    [overview, runtime, overviewError, runtimeError, t],
  );
  const detailQuery = query;
  const detailPlaceholder = detailTab === "users"
    ? t("AiMonitoringPage.searchPlaceholderUsers")
    : detailTab === "models" ? t("AiMonitoringPage.searchPlaceholderModels") : t("AiMonitoringPage.searchPlaceholderCalls");

  const selectModel = (modelName) => {
    setDetailTab("proxy");
    setStatusFilter("all");
    setQuery(modelName);
  };

  const openAttention = (target) => {
    if (target === "runtime") {
      navigate("/gateway");
      return;
    }
    if (target === "models") {
      setDetailTab("models");
      setStatusFilter("all");
    } else {
      setDetailTab("proxy");
      setStatusFilter(target === "api-errors" ? "error" : "all");
    }
    setQuery("");
    setDetailFocusRequest((current) => current + 1);
  };

  return (
    <div className={styles.page}>
      <PageHeader title={t("AiMonitoringPage.pageTitle")}>
        <div className={styles.pageActions}>
          <div className={styles.refreshMeta}>
            <span className={styles.refreshDot} />
            <span>{lastUpdated ? t("AiMonitoringPage.lastUpdated", { time: formatTime(lastUpdated, "—", { seconds: true }) }) : t("AiMonitoringPage.waitingForData")}</span>
          </div>
          <SegmentedControl
            options={PRESETS}
            value={preset}
            onChange={setPreset}
            ariaLabel={t("AiMonitoringPage.rangeLabel")}
          />
        </div>
      </PageHeader>

      <section className={styles.metricRow} aria-label={t("AiMonitoringPage.summaryTitle")}>
        <MetricCard
          icon="swap_calls"
          tone="primary"
          label={t("AiMonitoringPage.statCallCount")}
          value={summary ? formatNumber(summary.total_calls) : "—"}
          detail={t("AiMonitoringPage.previousPeriod")}
          delta={comparison ? formatDelta(comparison.total_calls_percent) : null}
          deltaTone={comparison?.total_calls_percent > 0 ? "neutral" : "positive"}
        />
        <MetricCard
          icon="data_usage"
          tone="info"
          label={t("AiMonitoringPage.statTokensTotal")}
          value={formatTokens(summary?.total_tokens)}
          detail={t("AiMonitoringPage.currentPeriod")}
        />
        <MetricCard
          icon="error_outline"
          tone="danger"
          label={t("AiMonitoringPage.statErrorRate")}
          value={formatPercent(summary?.error_rate)}
          detail={t("AiMonitoringPage.previousPeriod")}
          delta={comparison ? formatDelta(comparison.error_rate_delta, "pp") : null}
          deltaTone={comparison?.error_rate_delta > 0 ? "danger" : "positive"}
        />
      </section>

      <section className={`${styles.primaryGrid} ${!overviewLoading && !runtimeLoading && attentionItems.length === 0 ? styles.primaryGridSingle : ""}`}>
        <div className={`${styles.panel} ${styles.trendPanel}`}>
          <div className={styles.panelHeader}>
            <div>
              <h2 className={styles.panelTitle}><MIcon name="timeline" size={18} />{t("AiMonitoringPage.trendTitle")}</h2>
            </div>
            <div className={styles.trendMeta}>
              <SegmentedControl options={TREND_OPTIONS} value={trendMetric} onChange={setTrendMetric} ariaLabel={t("AiMonitoringPage.trendMetricLabel")} />
            </div>
          </div>
          <TrendChart series={overview?.series} bucket={overview?.bucket ?? presetToBucket(preset)} loading={overviewLoading} metric={trendMetric} t={t} />
        </div>
        {(overviewLoading || runtimeLoading || attentionItems.length > 0) && (
          <CompactHealthPanel items={attentionItems} loading={overviewLoading || runtimeLoading} t={t} onOpen={openAttention} />
        )}
      </section>

      <section ref={detailSectionRef} id="monitoring-details" className={styles.detailSection} aria-labelledby="detail-heading">
        <div className={styles.detailHeader}>
          <div>
            <h2 id="detail-heading" className={styles.detailTitle}>{t("AiMonitoringPage.detailTitle")}</h2>
          </div>
          <div className={styles.detailToolbar}>
            {detailTab === "proxy" ? (
              <SegmentedControl
                options={STATUS_FILTERS}
                value={statusFilter}
                onChange={setStatusFilter}
                ariaLabel={t("AiMonitoringPage.statusFilterLabel")}
              />
            ) : null}
            <div className={styles.search}>
              <MIcon name="search" size={16} />
              <input
                type="text"
                className={styles.searchInput}
                placeholder={detailPlaceholder}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                aria-label={detailPlaceholder}
              />
              {query ? <button type="button" className={styles.clearSearch} onClick={() => setQuery("")} aria-label={t("AiMonitoringPage.clearSearch")}><MIcon name="close" size={14} /></button> : null}
            </div>
          </div>
        </div>
        <DetailSummary summary={summary} t={t} />
        <div className={styles.detailTabs} role="tablist" aria-label={t("AiMonitoringPage.detailTitle")}>
          {DETAIL_TABS.map((item) => (
            <button key={item.key} type="button" role="tab" aria-selected={detailTab === item.key} className={`${styles.detailTab} ${detailTab === item.key ? styles.detailTabActive : ""}`} onClick={() => { setDetailTab(item.key); setQuery(""); }}>
              <MIcon name={item.icon} size={16} />{item.label}<span className={styles.tabCount}>{formatNumber(item.count)}</span>
            </button>
          ))}
        </div>
        <div className={styles.detailContent}>
          {detailLoading ? <LoadingState /> : <DetailTable tab={detailTab} calls={proxyCalls} users={users} models={overview?.model_breakdown} runtimeModels={runtime?.models} query={detailQuery} statusFilter={statusFilter} onModelSelect={selectModel} t={t} />}
        </div>
      </section>
    </div>
  );
}
