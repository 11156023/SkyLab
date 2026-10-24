import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
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
import EmptyState from "../../../components/EmptyState/EmptyState";
import DataTable from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import { MonitoringService } from "../../../services/monitoring";
import {
  useAiMonitoringOverviewQuery,
  useAiMonitoringRuntimeQuery,
  useAiProxyCallsQuery,
  useAiTemplateCallsQuery,
  useAiUsersUsageQuery,
} from "../../../queries/aiMonitoring";
import { useToast } from "../../../hooks/useToast";
import { useQueryView } from "../../../hooks/useQueryLoadState";
import PageHeader from "../../../components/PageHeader/PageHeader";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import {
  formatDuration,
  formatModelDisplay,
  formatTokenRate,
  formatTokens,
  isOkStatus,
  presetToBucket,
} from "../aiFormat";

const EMPTY = Object.freeze([]);

/* 明細查詢的清單與筆數（count 缺席時用清單長度） */
function listRows(listQuery) {
  return listQuery.data?.data ?? EMPTY;
}

function listCount(listQuery) {
  return listQuery.data?.count ?? listQuery.data?.data?.length ?? 0;
}

function modelKey(modelName) {
  return formatModelDisplay(modelName).toLocaleLowerCase();
}

export function mergeModelRows(usageModels = [], runtimeModels = []) {
  const rows = new Map();

  usageModels.forEach((model) => {
    const key = modelKey(model.model_name);
    /* 後端依原始名稱分組：路徑與 org/name、大小寫不同的名稱會對到同一個顯示 key，
       後面那筆不能蓋掉前面的用量（清單已依呼叫數排序，執行狀態併到第一筆） */
    rows.set(rows.has(key) ? `raw:${model.model_name}` : key, {
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
    <section className={`${styles.panel} ${styles.healthPanel}`} aria-labelledby="runtime-heading" data-guide="ai-monitoring-health">
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

/* ── 明細的三張表（TanStack Table）──
   欄位定義放模組層級：v9 的 FlexRender 把函式型的 header／cell 當元件掛載，render 裡新建會讓整格重新掛載。
   表頭文字取 meta.headerKey 的翻譯；點模型看呼叫紀錄經 table meta 交回頁面。
   資料沒變時表格的列不會重繪，切換語言只會重繪有用 useTranslation 的元件，
   所以依語系格式化的格子（formatNumber／formatDateTime 讀 i18n.language）自己訂閱語系 */
const columnHelper = createColumnHelper();
const NUMERIC_TD = `${styles.td} ${styles.numericCell}`;
const MONO_TD = `${styles.td} ${styles.monoCell}`;

function HeaderLabel({ column }) {
  const { t } = useTranslation("ai");
  return t(column.columnDef.meta.headerKey);
}

function NumberCell({ getValue }) {
  useTranslation("ai");
  return formatNumber(getValue());
}

function columnMeta(headerKey, tdClassName = styles.td, extra) {
  return { headerKey, thClassName: styles.th, tdClassName, ...extra };
}

function ModelNameCell({ row, table }) {
  const { t } = useTranslation("ai");
  const model = row.original;
  if (!(model.total_calls > 0)) return <span>{formatModelDisplay(model.model_name)}</span>;
  return (
    <button type="button" className={styles.modelDrilldown} onClick={() => table.options.meta.onModelSelect(model.model_name)} title={t("AiMonitoringPage.viewModelCalls", { model: formatModelDisplay(model.model_name) })}>
      <span>{formatModelDisplay(model.model_name)}</span>
      <MIcon name="arrow_forward" size={15} />
    </button>
  );
}

function ModelRuntimeCell({ row }) {
  const { t } = useTranslation("ai");
  return <ModelRuntimeBadge status={row.original.runtime_status} t={t} />;
}

function ModelDeploymentsCell({ row }) {
  const { t } = useTranslation("ai");
  const model = row.original;
  return model.runtime_status ? t("AiMonitoringPage.deploymentCount", { healthy: model.healthy_deployments, unhealthy: model.unhealthy_deployments }) : "—";
}

const modelColumns = columnHelper.columns([
  columnHelper.display({ id: "model", header: HeaderLabel, cell: ModelNameCell, meta: columnMeta("AiMonitoringPage.colModel", MONO_TD) }),
  columnHelper.display({ id: "runtime", header: HeaderLabel, cell: ModelRuntimeCell, meta: columnMeta("AiMonitoringPage.colRuntimeStatus") }),
  columnHelper.display({ id: "deployments", header: HeaderLabel, cell: ModelDeploymentsCell, meta: columnMeta("AiMonitoringPage.colDeployments") }),
  columnHelper.accessor("total_calls", { header: HeaderLabel, cell: NumberCell, meta: columnMeta("AiMonitoringPage.colCallCount", NUMERIC_TD) }),
  columnHelper.accessor("total_tokens", { header: HeaderLabel, cell: ({ getValue }) => formatTokens(getValue()), meta: columnMeta("AiMonitoringPage.colTokensTotal", NUMERIC_TD) }),
  columnHelper.accessor("error_rate", { header: HeaderLabel, cell: ({ getValue }) => formatPercent(getValue()), meta: columnMeta("AiMonitoringPage.colFailRate", NUMERIC_TD) }),
  columnHelper.accessor("avg_latency_ms", { header: HeaderLabel, cell: ({ getValue }) => formatDuration(getValue()), meta: columnMeta("AiMonitoringPage.colAvgLatency", NUMERIC_TD) }),
]);

function userTotalCalls(user) {
  return (user.proxy_calls ?? 0) + (user.template_calls ?? 0);
}

function userTotalTokens(user) {
  return (user.proxy_input_tokens ?? 0) + (user.proxy_output_tokens ?? 0)
    + (user.template_input_tokens ?? 0) + (user.template_output_tokens ?? 0);
}

const userColumns = columnHelper.columns([
  columnHelper.display({
    id: "user",
    header: HeaderLabel,
    cell: ({ row }) => <UserCell email={row.original.user_email} fullName={row.original.user_full_name} fallback={row.original.user_id} />,
    meta: columnMeta("AiMonitoringPage.colUser"),
  }),
  columnHelper.accessor(userTotalCalls, { id: "calls", header: HeaderLabel, cell: NumberCell, meta: columnMeta("AiMonitoringPage.colCallCount", NUMERIC_TD) }),
  columnHelper.accessor(userTotalTokens, { id: "tokens", header: HeaderLabel, cell: ({ getValue }) => formatTokens(getValue()), meta: columnMeta("AiMonitoringPage.colTokensTotal", NUMERIC_TD) }),
  columnHelper.accessor("avg_latency_ms", { header: HeaderLabel, cell: ({ getValue }) => formatDuration(getValue()), meta: columnMeta("AiMonitoringPage.colAvgLatency", NUMERIC_TD) }),
  columnHelper.accessor("error_rate", { header: HeaderLabel, cell: ({ getValue }) => formatPercent(getValue()), meta: columnMeta("AiMonitoringPage.colFailRate", NUMERIC_TD) }),
]);

function CallTimeCell({ row }) {
  useTranslation("ai");
  const call = row.original;
  return (
    <>
      {formatDateTime(call.created_at)}
      {call.request_id ? <small className={`${styles.monoCell} ${styles.callRequestId}`} title={[call.request_id, call.upstream_request_id].filter(Boolean).join(" / ")}>{call.request_id.slice(0, 8)}</small> : null}
    </>
  );
}

function CallModelCell({ row }) {
  const call = row.original;
  return call.response_model && call.response_model !== call.model_name
    ? `${formatModelDisplay(call.model_name)} → ${formatModelDisplay(call.response_model)}`
    : formatModelDisplay(call.model_name);
}

function CallUsageCell({ row }) {
  const { t } = useTranslation("ai");
  return t(row.original.usage_reported ? "AiMonitoringPage.usageReported" : "AiMonitoringPage.usageMissing");
}

const callColumns = columnHelper.columns([
  columnHelper.display({ id: "time", header: HeaderLabel, cell: CallTimeCell, meta: columnMeta("AiMonitoringPage.colTime") }),
  columnHelper.display({
    id: "user",
    header: HeaderLabel,
    cell: ({ row }) => <UserCell email={row.original.user_email} fullName={row.original.user_full_name} fallback={row.original.user_id} />,
    meta: columnMeta("AiMonitoringPage.colUser"),
  }),
  columnHelper.display({
    id: "model",
    header: HeaderLabel,
    cell: CallModelCell,
    /* td 的 title 依列決定（模型 → 實際回應的模型） */
    meta: columnMeta("AiMonitoringPage.colModel", MONO_TD, {
      tdProps: (row) => ({ title: [row.original.model_name, row.original.response_model].filter(Boolean).join(" → ") }),
    }),
  }),
  columnHelper.display({ id: "type", header: HeaderLabel, cell: ({ row }) => row.original.request_type ?? row.original.call_type ?? "—", meta: columnMeta("AiMonitoringPage.colType") }),
  columnHelper.display({ id: "input", header: HeaderLabel, cell: ({ row }) => formatTokens(row.original.input_tokens ?? 0), meta: columnMeta("AiMonitoringPage.colInput", NUMERIC_TD) }),
  columnHelper.display({ id: "output", header: HeaderLabel, cell: ({ row }) => formatTokens(row.original.output_tokens ?? 0), meta: columnMeta("AiMonitoringPage.colOutput", NUMERIC_TD) }),
  columnHelper.accessor("request_duration_ms", { header: HeaderLabel, cell: ({ getValue }) => formatDuration(getValue()), meta: columnMeta("AiMonitoringPage.colDuration", NUMERIC_TD) }),
  columnHelper.accessor("first_token_ms", { header: HeaderLabel, cell: ({ getValue }) => formatDuration(getValue()), meta: columnMeta("AiMonitoringPage.colFirstToken", NUMERIC_TD) }),
  columnHelper.accessor("e2e_output_tokens_per_second", { header: HeaderLabel, cell: ({ getValue }) => formatTokenRate(getValue()), meta: columnMeta("AiMonitoringPage.colOutputRate", NUMERIC_TD) }),
  columnHelper.display({ id: "usage", header: HeaderLabel, cell: CallUsageCell, meta: columnMeta("AiMonitoringPage.colUsageEvidence") }),
  columnHelper.display({ id: "status", header: HeaderLabel, cell: ({ row }) => <StatusBadge status={row.original.status} />, meta: columnMeta("AiMonitoringPage.colStatus") }),
]);

const detailRowClassName = () => styles.tr;

function ModelsTable({ models, onModelSelect }) {
  const table = useTable({
    features: baseFeatures,
    columns: modelColumns,
    data: models,
    getRowId: (model) => `${model.model_name}:${model.runtime_name ?? "usage"}`,
    meta: { onModelSelect },
  });
  return <div className={styles.tableWrap}><DataTable table={table} className={styles.table} rowClassName={detailRowClassName} /></div>;
}

function UsersTable({ users }) {
  const table = useTable({ features: baseFeatures, columns: userColumns, data: users, getRowId: (user) => String(user.user_id) });
  return (
    <div className={styles.tableWrap}>
      <DataTable table={table} className={styles.table} rowClassName={detailRowClassName} />
    </div>
  );
}

function CallsTable({ calls }) {
  const table = useTable({ features: baseFeatures, columns: callColumns, data: calls, getRowId: (call) => String(call.id) });
  return (
    <div className={styles.tableWrap}>
      <DataTable table={table} className={styles.table} rowClassName={detailRowClassName} />
    </div>
  );
}

function DetailTable({ tab, calls, users, modelRows, query, statusFilter, onModelSelect, onClearFilters, t }) {
  const q = query.trim().toLowerCase();
  /* 搜尋或狀態篩選把資料濾光時，空狀態給一顆清除鈕；本來就沒資料則只說明 */
  const statusFiltered = tab !== "models" && tab !== "users" && statusFilter !== "all";
  const clearAction = q || statusFiltered ? (
    <button type="button" className={styles.btnSecondary} onClick={onClearFilters}>
      <MIcon name={statusFiltered ? "filter_alt_off" : "search_off"} size={16} />
      {t(statusFiltered ? "EmptyState.clearFilters" : "EmptyState.clearSearch", { ns: "common" })}
    </button>
  ) : undefined;

  if (tab === "models") {
    const visibleModels = modelRows.filter((model) => (
      !q
      || model.model_name.toLowerCase().includes(q)
      || (model.runtime_name ?? "").toLowerCase().includes(q)
      || (model.runtime_status ?? "").toLowerCase().includes(q)
    ));
    if (!visibleModels.length) return <EmptyState icon="model_training" title={t("AiMonitoringPage.emptyModelBreakdown")} action={clearAction} />;
    return <ModelsTable models={visibleModels} onModelSelect={onModelSelect} />;
  }

  if (tab === "users") {
    const visibleUsers = (users ?? []).filter((user) => {
      if (!q) return true;
      return (user.user_email ?? "").toLowerCase().includes(q)
        || (user.user_full_name ?? "").toLowerCase().includes(q);
    });
    if (!visibleUsers.length) return <EmptyState icon="groups" title={t("AiMonitoringPage.emptyUsersTitle")} action={clearAction} />;
    return <UsersTable users={visibleUsers} />;
  }

  const visibleCalls = (calls ?? []).filter((call) => {
    if (statusFilter === "success" && !isOkStatus(call.status)) return false;
    if (statusFilter === "error" && isOkStatus(call.status)) return false;
    if (!q) return true;
    return (call.user_email ?? "").toLowerCase().includes(q)
      || (call.user_full_name ?? "").toLowerCase().includes(q)
      || (call.model_name ?? "").toLowerCase().includes(q)
      || (call.request_type ?? call.call_type ?? "").toLowerCase().includes(q)
      || (call.request_id ?? "").toLowerCase().includes(q);
  });

  if (!visibleCalls.length) return <EmptyState icon="analytics" title={t("AiMonitoringPage.emptyCallsTitle")} action={clearAction} />;
  return <CallsTable calls={visibleCalls} />;
}

export default function AiMonitoringPage() {
  const { t } = useTranslation("ai");
  const toast = useToast();
  const [preset, setPreset] = useState("7d");
  const [trendMetric, setTrendMetric] = useState("calls");
  const [detailTab, setDetailTab] = useState("models");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [detailFocusRequest, setDetailFocusRequest] = useState(0);
  /* 監控 stack 有啟用且目前是管理員才顯示「SkyLab AI」儀表板連結（查詢失敗或 403 就不顯示）；
     同一支 API 會設定 Grafana 免密碼登入的 cookie，頁面開著時定期續期 */
  const [grafanaUrl, setGrafanaUrl] = useState(null);
  const detailSectionRef = useRef(null);

  /* 五份資料各走一個 TanStack Query，背景 30 秒刷新、分頁隱藏時暫停。
     區間相關的四份以 preset 為 key（起訖在每次抓取時才算，刷新會跟著現在往前滑）：
     快速切換區間時較慢的舊區間回應只會進舊 key，不會蓋掉新區間；換區間時先沿用上一個區間的資料。
     總覽、執行狀態與明細各自獨立：慢或連不上的 gateway 不會讓已到的用量資料卡在載入畫面。 */
  const overviewQuery = useAiMonitoringOverviewQuery({ preset, bucket: presetToBucket(preset), compare: true, source: "all" });
  const runtimeQuery = useAiMonitoringRuntimeQuery();
  const proxyQuery = useAiProxyCallsQuery({ preset, limit: 100 });
  const templateQuery = useAiTemplateCallsQuery({ preset, limit: 100 });
  const usersQuery = useAiUsersUsageQuery({ preset, limit: 100, source: "all" });

  const overview = overviewQuery.data ?? null;
  const runtime = runtimeQuery.data ?? null;
  /* 還沒資料時 v5 每次重抓都把 status 改回 pending、清掉 error，直接看 isPending／isError 會在首載失敗後的
     每次背景重試閃回載入中、告警列一亮一滅；useQueryView 在重試期間維持失敗 */
  const overviewView = useQueryView(overviewQuery);
  const runtimeView = useQueryView(runtimeQuery);
  const proxyView = useQueryView(proxyQuery);
  const templateView = useQueryView(templateQuery);
  const usersView = useQueryView(usersQuery);
  const overviewLoading = overviewView.loading;
  const runtimeLoading = runtimeView.loading;
  /* 失敗旗標與原本相同：最近一次抓取失敗就亮（沿用畫面上的舊資料），下一次成功才熄 */
  const overviewError = overview !== null ? overviewQuery.isError : Boolean(overviewView.error);
  const runtimeError = runtime !== null ? runtimeQuery.isError : Boolean(runtimeView.error);
  const proxyCalls = listRows(proxyQuery);
  const templateCalls = listRows(templateQuery);
  const users = listRows(usersQuery);
  const counts = { proxy: listCount(proxyQuery), template: listCount(templateQuery), users: listCount(usersQuery) };
  /* 三份明細都到齊（或失敗）才離開載入畫面（與原本相同） */
  const detailLoading = proxyView.loading || templateView.loading || usersView.loading;
  /* 最後一次有回應（成功或失敗）的時間 */
  const lastUpdatedAt = Math.max(
    ...[overviewQuery, runtimeQuery, proxyQuery, templateQuery, usersQuery]
      .map((q) => Math.max(q.dataUpdatedAt, q.errorUpdatedAt)),
  );
  const lastUpdated = lastUpdatedAt > 0 ? new Date(lastUpdatedAt) : null;

  /* 這個區間的總覽一筆都還沒拿到就失敗才跳 toast（與原本的前景載入相同）；背景刷新失敗只亮告警列。
     useQueryView 的錯誤在背景重試期間不會先清掉再出現，同一個區間只提示一次，換區間又失敗才再提示 */
  const overviewFailedWithoutData = overview === null && Boolean(overviewView.error);
  useEffect(() => {
    if (overviewFailedWithoutData) toast.error(t("AiMonitoringPage.loadError"));
  }, [overviewFailedWithoutData, toast, t]);

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
    { key: "models", label: t("AiMonitoringPage.tabModels"), icon: "model_training", count: modelRows.length, buttonProps: { "data-guide-tab": "ai-monitoring-models" } },
    { key: "proxy", label: t("AiMonitoringPage.tabProxy"), icon: "swap_horiz", count: counts.proxy, buttonProps: { "data-guide-tab": "ai-monitoring-proxy" } },
    { key: "template", label: t("AiMonitoringPage.tabTemplate"), icon: "smart_toy", count: counts.template, buttonProps: { "data-guide-tab": "ai-monitoring-template" } },
    { key: "users", label: t("AiMonitoringPage.tabUsers"), icon: "groups", count: counts.users, buttonProps: { "data-guide-tab": "ai-monitoring-users" } },
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

  useEffect(() => {
    const controller = new AbortController();
    const refresh = () =>
      MonitoringService.createGrafanaSession({ signal: controller.signal })
        .then((link) =>
          setGrafanaUrl(link?.enabled && link.url ? `${link.url.replace(/\/+$/, "")}/d/skylab-ai` : null),
        )
        .catch(() => {});
    refresh();
    const timer = setInterval(refresh, 30 * 60_000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);

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
  const detailPlaceholder = detailTab === "users"
    ? t("AiMonitoringPage.searchPlaceholderUsers")
    : detailTab === "models" ? t("AiMonitoringPage.searchPlaceholderModels") : t("AiMonitoringPage.searchPlaceholderCalls");

  const selectModel = (modelName) => {
    setDetailTab("proxy");
    setStatusFilter("all");
    setQuery(modelName);
  };

  const openAttention = (target) => {
    /* AI Gateway（LiteLLM／vLLM）就緒狀態看的是本頁模型分頁的執行狀態；
       /gateway 是網路 Gateway VM（nginx），跟這個告警無關 */
    if (target === "models" || target === "runtime") {
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
          {grafanaUrl && (
            <a className={styles.linkBtn} href={grafanaUrl} target="_blank" rel="noopener noreferrer">
              <MIcon name="open_in_new" size={16} />
              {t("AiMonitoringPage.openGrafana")}
            </a>
          )}
        </div>
      </PageHeader>

      <section className={styles.metricRow} data-guide="ai-monitoring-metrics" aria-label={t("AiMonitoringPage.summaryTitle")}>
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
        <div className={`${styles.panel} ${styles.trendPanel}`} data-guide="ai-monitoring-trend">
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

      <section ref={detailSectionRef} id="monitoring-details" className={styles.detailSection} data-guide="ai-monitoring-detail" aria-labelledby="detail-heading">
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
            <button key={item.key} type="button" {...item.buttonProps} role="tab" aria-selected={detailTab === item.key} className={`${styles.detailTab} ${detailTab === item.key ? styles.detailTabActive : ""}`} onClick={() => { setDetailTab(item.key); setQuery(""); }}>
              <MIcon name={item.icon} size={16} />{item.label}<span className={styles.tabCount}>{formatNumber(item.count)}</span>
            </button>
          ))}
        </div>
        <div className={styles.detailContent}>
          {detailLoading ? <LoadingState /> : <DetailTable tab={detailTab} calls={detailTab === "template" ? templateCalls : proxyCalls} users={users} modelRows={modelRows} query={query} statusFilter={statusFilter} onModelSelect={selectModel} onClearFilters={() => { setQuery(""); setStatusFilter("all"); }} t={t} />}
        </div>
      </section>
    </div>
  );
}
