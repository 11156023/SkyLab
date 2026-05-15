import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./AiMonitoringPage.module.scss";
import MIcon from "../../../components/MIcon";
import { AiMonitoringService } from "../../../services/aiMonitoring";
import { useToast } from "../../../hooks/useToast";

function presetToRange(preset) {
  const end = new Date();
  const start = new Date();
  const days = preset === "7d" ? 7 : preset === "30d" ? 30 : 90;
  start.setDate(start.getDate() - days);
  return { startDate: start.toISOString(), endDate: end.toISOString() };
}

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString("zh-TW") : "—";
}

const PRESETS = [
  { value: "7d",  label: "近 7 天" },
  { value: "30d", label: "近 30 天" },
  { value: "90d", label: "近 90 天" },
];

const TABS = [
  { key: "proxy",    label: "Proxy 呼叫", icon: "swap_horiz" },
  { key: "template", label: "Template",   icon: "auto_awesome" },
  { key: "users",    label: "使用者用量", icon: "groups" },
];

const PROXY_COLS = ["時間", "使用者", "模型", "Tokens", "延遲", "狀態"];
const TPL_COLS   = ["時間", "使用者", "模板", "Tokens", "延遲", "狀態"];
const USER_COLS  = ["使用者", "呼叫次數", "Tokens 總計", "平均延遲", "失敗率"];

function formatTokens(n) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatDuration(ms) {
  if (ms == null) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

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

function StatusBadge({ status }) {
  const ok = status === "success" || status === 200 || status === "ok";
  return (
    <span className={`${styles.badge} ${ok ? styles.badge_ok : styles.badge_err}`}>
      <span className={styles.dot} />
      {ok ? "成功" : "失敗"}
    </span>
  );
}

export default function AiMonitoringPage() {
  const toast = useToast();
  const [preset, setPreset] = useState("7d");
  const [tab, setTab] = useState("proxy");
  const [query, setQuery] = useState("");
  const [statsData, setStatsData] = useState(null);
  const [proxyCalls, setProxyCalls] = useState([]);
  const [templateCalls, setTemplateCalls] = useState([]);
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const range = presetToRange(preset);
      const [s, p, t, u] = await Promise.all([
        AiMonitoringService.stats(range),
        AiMonitoringService.listProxyCalls({ ...range, limit: 100 }),
        AiMonitoringService.listTemplateCalls({ ...range, limit: 100 }),
        AiMonitoringService.listUsersUsage({ ...range, limit: 100 }),
      ]);
      setStatsData(s);
      setProxyCalls(p?.data ?? []);
      setTemplateCalls(t?.data ?? []);
      setUsers(u?.data ?? []);
    } catch (e) {
      toast.error(e?.message ?? "載入 AI 監控資料失敗");
    } finally {
      setLoading(false);
    }
  }, [preset, toast]);

  useEffect(() => { load(); }, [load]);

  const stats = useMemo(() => {
    if (!statsData) {
      return { totalCalls: 0, totalTokens: 0, successRate: 100, avgLatency: 0 };
    }
    const totalCalls = (statsData.proxy_total_calls ?? 0) + (statsData.template_total_calls ?? 0);
    const totalTokens =
      (statsData.proxy_total_input_tokens ?? 0) +
      (statsData.proxy_total_output_tokens ?? 0) +
      (statsData.template_total_input_tokens ?? 0) +
      (statsData.template_total_output_tokens ?? 0);
    const allCalls = [...proxyCalls, ...templateCalls];
    const failed = allCalls.filter((c) => c.status && c.status !== "success").length;
    const successRate = allCalls.length === 0 ? 100 : Math.round(((allCalls.length - failed) / allCalls.length) * 100);
    const withDur = allCalls.filter((c) => c.request_duration_ms != null);
    const avgLatency =
      withDur.length === 0
        ? 0
        : Math.round(withDur.reduce((s, c) => s + c.request_duration_ms, 0) / withDur.length);
    return { totalCalls, totalTokens, successRate, avgLatency };
  }, [statsData, proxyCalls, templateCalls]);

  const visibleCalls = useMemo(() => {
    const source = tab === "proxy" ? proxyCalls : templateCalls;
    const q = query.trim().toLowerCase();
    if (!q) return source;
    return source.filter(
      (c) =>
        (c.user_email ?? "").toLowerCase().includes(q) ||
        (c.user_full_name ?? "").toLowerCase().includes(q) ||
        (c.model_name ?? "").toLowerCase().includes(q) ||
        (c.preset ?? "").toLowerCase().includes(q),
    );
  }, [proxyCalls, templateCalls, tab, query]);

  const visibleUsers = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) =>
        (u.user_email ?? "").toLowerCase().includes(q) ||
        (u.user_full_name ?? "").toLowerCase().includes(q),
    );
  }, [users, query]);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>AI 監控</h1>
          <p className={styles.pageSubtitle}>檢視 AI Proxy 與 Template 服務的呼叫紀錄與用量統計</p>
        </div>
        <div className={styles.pageActions}>
          <div className={styles.segment}>
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
          </div>
          <button type="button" className={styles.btnSecondary} onClick={load} disabled={loading}>
            <MIcon name="sync" size={16} />
            {loading ? "載入中…" : "重新整理"}
          </button>
        </div>
      </div>

      <div className={styles.statRow}>
        <div className={styles.statCard}>
          <div className={styles.statIcon}>
            <MIcon name="swap_calls" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>呼叫次數</span>
            <span className={styles.statValue}>{stats.totalCalls}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconAccent}`}>
            <MIcon name="bolt" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>Tokens 總計</span>
            <span className={styles.statValue}>{formatTokens(stats.totalTokens)}</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconOk}`}>
            <MIcon name="task_alt" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>成功率</span>
            <span className={styles.statValue}>{stats.successRate}%</span>
          </div>
        </div>
        <div className={styles.statCard}>
          <div className={`${styles.statIcon} ${styles.statIconBusy}`}>
            <MIcon name="timer" size={20} />
          </div>
          <div className={styles.statInfo}>
            <span className={styles.statLabel}>平均延遲</span>
            <span className={styles.statValue}>{formatDuration(stats.avgLatency)}</span>
          </div>
        </div>
      </div>

      <div className={styles.tabs}>
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            className={`${styles.tab} ${tab === t.key ? styles.tabActive : ""}`}
            onClick={() => setTab(t.key)}
          >
            <MIcon name={t.icon} size={16} />
            {t.label}
          </button>
        ))}
      </div>

      <div className={styles.toolbar}>
        <div className={styles.search}>
          <MIcon name="search" size={16} />
          <input
            type="text"
            className={styles.searchInput}
            placeholder={tab === "users" ? "搜尋使用者" : "搜尋使用者、模型或模板"}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>

      <div className={styles.content}>
        {tab === "users" ? (
          visibleUsers.length === 0 ? (
            <EmptyState
              icon="groups"
              title="尚無使用者用量資料"
              desc="使用者呼叫 AI 服務後,統計資料會出現在這裡"
            />
          ) : (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    {USER_COLS.map((c) => (
                      <th key={c} className={styles.th}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {visibleUsers.map((u) => {
                    const totalCalls = (u.proxy_calls ?? 0) + (u.template_calls ?? 0);
                    const totalTokens =
                      (u.proxy_input_tokens ?? 0) +
                      (u.proxy_output_tokens ?? 0) +
                      (u.template_input_tokens ?? 0) +
                      (u.template_output_tokens ?? 0);
                    return (
                      <tr key={u.user_id} className={styles.tr}>
                        <td className={styles.td}>{u.user_email ?? u.user_full_name ?? u.user_id}</td>
                        <td className={styles.td}>{totalCalls}</td>
                        <td className={styles.td}>{formatTokens(totalTokens)}</td>
                        <td className={styles.td}>—</td>
                        <td className={styles.td}>—</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        ) : visibleCalls.length === 0 ? (
          <EmptyState
            icon="analytics"
            title="尚無呼叫紀錄"
            desc={`此時段內沒有 ${tab === "proxy" ? "Proxy" : "Template"} 呼叫紀錄`}
          />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {(tab === "proxy" ? PROXY_COLS : TPL_COLS).map((c) => (
                    <th key={c} className={styles.th}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleCalls.map((c) => (
                  <tr key={c.id} className={styles.tr}>
                    <td className={styles.td}>{fmtTime(c.created_at)}</td>
                    <td className={styles.td}>{c.user_email ?? c.user_full_name ?? "—"}</td>
                    <td className={styles.td}>
                      {tab === "proxy" ? c.model_name : (c.preset ?? c.call_type ?? c.model_name)}
                    </td>
                    <td className={styles.td}>
                      {formatTokens((c.input_tokens ?? 0) + (c.output_tokens ?? 0))}
                    </td>
                    <td className={styles.td}>{formatDuration(c.request_duration_ms)}</td>
                    <td className={styles.td}>
                      <StatusBadge status={c.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
