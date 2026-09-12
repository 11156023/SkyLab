import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import AiPveChat from "../../../../components/AiPveChat/AiPveChat";
import MIcon from "../../../../components/MIcon";
import usePveOverview from "../../../../hooks/usePveOverview";
import { useAuth } from "../../../../contexts/AuthContext";
import { AiApiService } from "../../../../services/aiApi";
import { BatchProvisionService } from "../../../../services/batchProvision";
import { JobsService } from "../../../../services/jobs";
import { MiningIncidentsService } from "../../../../services/miningIncidents";
import { MonitoringService } from "../../../../services/monitoring";
import { SpecChangeRequestsService } from "../../../../services/specChangeRequests";
import { VmRequestsService } from "../../../../services/vmRequests";
import {
  buildFyiStats,
  buildTodayRows,
  buildUrgentRows,
  formatCheckedAt,
  mergeInfraProblems,
} from "./adminAttention";
import styles from "./AdminDashboardPage.module.scss";
import PageHeader from "../../../../components/PageHeader/PageHeader";

export function countRows(response) {
  if (Array.isArray(response)) return response.length;
  if (Number.isFinite(response?.count)) return response.count;
  if (Number.isFinite(response?.total)) return response.total;
  if (Array.isArray(response?.data)) return response.data.length;
  if (Array.isArray(response?.items)) return response.items.length;
  return 0;
}

export function normalizeAssistantPrompt(value) {
  return String(value ?? "").trim();
}

export default function AdminDashboardPage() {
  const { t, i18n } = useTranslation("personal");
  const navigate = useNavigate();
  const { user } = useAuth();
  const { overview, loading: overviewLoading, refreshing, error: overviewError, reload } = usePveOverview();
  const [assistantPrompt, setAssistantPrompt] = useState("");
  const assistantInputRef = useRef(null);
  const [conversationPrompt, setConversationPrompt] = useState("");
  /* 放大模式：對話佔滿版面，上面的待辦暫時收起來 */
  const [focusMode, setFocusMode] = useState(false);
  const [checks, setChecks] = useState({ alerts: [], failedJobs: 0, requests: 0, batches: 0, aiRequests: 0, miningIncidents: 0, unavailable: 0 });
  const [loading, setLoading] = useState(true);
  const [checkVersion, setCheckVersion] = useState(0);

  useEffect(() => {
    let active = true;
    async function loadChecks() {
      setLoading(true);
      const settled = await Promise.allSettled([
        VmRequestsService.listAll("pending"),
        SpecChangeRequestsService.listAll({ status: "pending" }),
        BatchProvisionService.listPending(),
        AiApiService.listAllRequests(),
        JobsService.list({ statuses: ["failed", "blocked"], historyDays: 7, limit: 50 }),
        MonitoringService.listAlerts({ active: true, limit: 100 }),
        MiningIncidentsService.list({ limit: 200 }),
      ]);
      if (!active) return;
      const value = (index) => settled[index].status === "fulfilled" ? settled[index].value : null;
      const aiPending = value(3)?.data?.filter((request) => request.status === "pending").length ?? 0;
      const alertRows = value(5);
      /* 只算還沒被管理員定奪的事件（detected 待判斷、suspended 已凍結待處置） */
      const miningRows = value(6);
      const miningActive = (Array.isArray(miningRows) ? miningRows : miningRows?.data ?? [])
        .filter((incident) => incident.status === "detected" || incident.status === "suspended").length;
      setChecks({
        requests: countRows(value(0)) + countRows(value(1)),
        batches: countRows(value(2)),
        aiRequests: aiPending,
        failedJobs: countRows(value(4)),
        alerts: Array.isArray(alertRows) ? alertRows : alertRows?.data ?? [],
        miningIncidents: miningActive,
        unavailable: settled.filter((result) => result.status === "rejected").length,
      });
      setLoading(false);
    }
    loadChecks();
    return () => { active = false; };
  }, [checkVersion]);

  /* 即時異常與未解除告警的門檻判斷共用同一組設定，兩邊都列會讓同一台機器
     出現兩次；mergeInfraProblems 負責去重，細節見 adminAttention.js。 */
  const infraProblems = useMemo(
    () => mergeInfraProblems(overview?.issues ?? [], checks.alerts ?? []),
    [overview?.issues, checks.alerts],
  );
  const urgent = useMemo(
    () => buildUrgentRows({ infraProblems, failedJobs: checks.failedJobs, miningIncidents: checks.miningIncidents }, t),
    [infraProblems, checks.failedJobs, checks.miningIncidents, t],
  );
  const today = useMemo(() => buildTodayRows(checks, t), [checks, t]);
  const stats = useMemo(() => buildFyiStats(overview, t), [overview, t]);

  const busy = loading || (overviewLoading && !overview);
  const urgentCount = urgent.reduce((total, row) => total + (row.count ?? 1), 0);
  const todayCount = today.reduce((total, row) => total + row.count, 0);
  const incomplete = overviewError || !overview || overview.data_status === "stale"
    || overview.data_status === "partial" || checks.unavailable > 0;
  const name = user?.full_name?.trim() || user?.email?.split("@")[0] || t("AdminDashboardPage.defaultName");

  function refreshDashboard() {
    setLoading(true);
    setCheckVersion((value) => value + 1);
    reload();
  }

  function resetAssistant() {
    setConversationPrompt("");
    setAssistantPrompt("");
    setFocusMode(false);
  }

  function openAssistant(event) {
    event.preventDefault();
    const prompt = normalizeAssistantPrompt(assistantPrompt);
    if (!prompt) return;
    setConversationPrompt(prompt);
  }

  const suggestions = [
    t("AdminDashboardPage.suggestion1"),
    t("AdminDashboardPage.suggestion2"),
    t("AdminDashboardPage.suggestion3"),
  ];

  return <div className={`${styles.page} ${focusMode ? styles.pageFocused : ""}`}>
    <PageHeader title={t("AdminDashboardPage.greeting", { name })} subtitle={t("AdminDashboardPage.subtitle")}>
      {!focusMode && <div className={styles.refreshControls}>
        <span className={styles.checkedAt}>
          {overview
            ? t("AdminDashboardPage.pveUpdatedAt", { time: formatCheckedAt(overview.collected_at, i18n.language) })
            : t("AdminDashboardPage.pveNotChecked")}
        </span>
        <button type="button" onClick={refreshDashboard} disabled={busy || refreshing}>
          <MIcon name="refresh" size={16} className={refreshing || loading ? styles.spin : ""} />
          {t("AdminDashboardPage.pveRefresh")}
        </button>
      </div>}
    </PageHeader>

    {!focusMode && <>
    {stats.length > 0 && <section className={styles.statsGrid} aria-label={t("AdminDashboardPage.resourceOverview")}>
      {stats.map((stat) => <button type="button" key={stat.key} className={styles.statCard} onClick={() => navigate(stat.path)}>
        <span className={styles.statIcon}><MIcon name={stat.icon} size={21} /></span>
        <span className={styles.statContent}>
          <span className={styles.statLabel}>{stat.label}</span>
          <strong>{stat.value}</strong>
          <small>{t(stat.key === "nodes" ? "AdminDashboardPage.onlineOfTotal" : "AdminDashboardPage.runningOfTotal")}</small>
        </span>
        <MIcon name="chevron_right" size={17} className={styles.statArrow} />
      </button>)}
    </section>}

    <section className={styles.attention} aria-label={t("AdminDashboardPage.attentionTitle")} aria-busy={busy}>
      {busy ? <div className={styles.checking} role="status"><MIcon name="sync" size={18} className={styles.spin} />{t("AdminDashboardPage.checking")}</div> : <>
        <div className={styles.tiers}>
        <section className={`${styles.tier} ${urgent.length ? styles.tierNow : ""}`} aria-labelledby="admin-urgent-title">
          <div className={styles.tierHead}>
            <h3 id="admin-urgent-title"><MIcon name="error_outline" size={18} />{t("AdminDashboardPage.tierNowTitle")}</h3>
            <span className={styles.countBadge}>{incomplete && !urgentCount ? "—" : urgentCount}</span>
          </div>
          <div className={styles.rowList}>
          {urgent.map((row) => <button type="button" key={row.key} className={`${styles.row} ${styles[`row_${row.tone}`]}`} onClick={() => navigate(row.path)}>
            <MIcon name={row.icon} size={17} />
            <span className={styles.rowContent}><strong>{row.title}</strong><small>{row.detail}</small></span>
            <b>{row.count ?? ""}</b>
            <MIcon name="chevron_right" size={16} />
          </button>)}
          {urgent.length === 0 && <div className={styles.emptyState}>
            <MIcon name={incomplete ? "sync_problem" : "check_circle"} size={22} />
            <span>{t(incomplete ? "AdminDashboardPage.emptyUnavailable" : "AdminDashboardPage.noUrgent")}</span>
          </div>}
          </div>
          <button type="button" className={styles.tierLink} onClick={() => navigate("/monitoring")}>
            {t("AdminDashboardPage.viewMonitoring")}<MIcon name="arrow_forward" size={15} />
          </button>
        </section>

        <section className={styles.tier} aria-labelledby="admin-today-title">
          <div className={styles.tierHead}>
            <h3 id="admin-today-title"><MIcon name="event_note" size={18} />{t("AdminDashboardPage.tierTodayTitle")}</h3>
            <span className={`${styles.countBadge} ${todayCount ? styles.countPending : ""}`}>{checks.unavailable && !todayCount ? "—" : todayCount}</span>
          </div>
          <div className={styles.rowList}>
          {today.map((row) => <button type="button" key={row.key} className={styles.row} onClick={() => navigate(row.path)}>
            <MIcon name={row.icon} size={17} />
            <span className={styles.rowContent}><strong>{row.title}</strong></span>
            <b>{row.count}</b>
            <MIcon name="chevron_right" size={16} />
          </button>)}
          {today.length === 0 && <div className={styles.emptyState}>
            <MIcon name={checks.unavailable ? "sync_problem" : "task_alt"} size={22} />
            <span>{t(checks.unavailable ? "AdminDashboardPage.emptyUnavailable" : "AdminDashboardPage.noPending")}</span>
          </div>}
          </div>
          {today.length > 0 && <p className={styles.tierFootnote}>{t("AdminDashboardPage.todayHint")}</p>}
        </section>
        </div>
      </>}

      {!busy && incomplete && <div className={styles.staleNote} role="status">
        <MIcon name="sync_problem" size={16} />
        <span>{t(!overview ? "AdminDashboardPage.emptyUnavailable"
          : overview?.data_status === "partial" ? "AdminDashboardPage.pvePartialMessage"
            : overviewError || overview?.data_status === "stale" ? "AdminDashboardPage.pveStaleMessage"
              : "AdminDashboardPage.issueUnavailableTitle")}</span>
      </div>}
    </section>
    </>}

    {/* AI 助手：沒開始對話前只是一條輸入列，不要先佔掉整片高度 */}
    <section className={`${styles.assistant} ${focusMode ? styles.assistantFocused : ""}`} aria-labelledby="admin-assistant-title">
      <div className={styles.assistantHead}>
        <div className={styles.assistantIdentity}>
          <span className={styles.assistantIcon}><MIcon name="support_agent" size={24} /></span>
          <h2 id="admin-assistant-title">{t("AdminDashboardPage.assistantLabel")}</h2>
        </div>
        {conversationPrompt && <div className={styles.assistantActions}>
          <button type="button" onClick={() => setFocusMode((value) => !value)}>
            <MIcon name={focusMode ? "close_fullscreen" : "open_in_full"} size={15} />
            {focusMode ? t("AdminDashboardPage.backToOverview") : t("AdminDashboardPage.expandChat")}
          </button>
          <button type="button" onClick={resetAssistant}>
            <MIcon name="refresh" size={15} />
            {t("AdminDashboardPage.askAgain")}
          </button>
        </div>}
      </div>

      {conversationPrompt ? <AiPveChat initialPrompt={conversationPrompt} compact={!focusMode} fill={focusMode} />
        : <form className={styles.assistantForm} onSubmit={openAssistant}>
          <div className={styles.assistantInput}>
            <MIcon name="terminal" size={19} />
            <input ref={assistantInputRef} aria-label={t("AdminDashboardPage.assistantLabel")} value={assistantPrompt} onChange={(event) => setAssistantPrompt(event.target.value)} placeholder={t("AdminDashboardPage.promptPlaceholder")} autoComplete="off" />
            <button type="submit" disabled={!assistantPrompt.trim()}>{t("AdminDashboardPage.startAsking")}<MIcon name="arrow_forward" size={16} /></button>
          </div>
          <div className={styles.suggestionButtons}>
            {suggestions.map((suggestion) => <button type="button" key={suggestion} onClick={() => {
              setAssistantPrompt(suggestion);
              assistantInputRef.current?.focus();
            }}>{suggestion}</button>)}
          </div>
        </form>}
    </section>
  </div>;
}
