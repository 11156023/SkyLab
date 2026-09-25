import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import AiPveChat from "../../../../components/AiPveChat/AiPveChat";
import MIcon from "../../../../components/MIcon";
import useAutoRefresh from "../../../../hooks/useAutoRefresh";
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
  const { overview, loading: overviewLoading, error: overviewError } = usePveOverview();
  const [assistantPrompt, setAssistantPrompt] = useState("");
  const assistantInputRef = useRef(null);
  const [conversationPrompt, setConversationPrompt] = useState("");
  /* 放大模式：對話佔滿版面，統計卡與待辦暫時收起來 */
  const [focusMode, setFocusMode] = useState(false);
  const [checks, setChecks] = useState({ alerts: [], failedJobs: 0, requests: 0, batches: 0, aiRequests: 0, miningIncidents: 0, unavailable: 0 });
  const [loading, setLoading] = useState(true);
  const checksInFlightRef = useRef(false);

  /* 待辦來源每 30 秒靜默重抓一次（同 PVE 概況，分頁隱藏時暫停）。背景更新不顯示載入中；
     個別來源失敗時沿用上一次的數字，免得待辦列在 0 與實際值之間跳動，缺漏照樣記進 unavailable。 */
  const loadChecks = useCallback(async (silent = false) => {
    if (checksInFlightRef.current) return;
    checksInFlightRef.current = true;
    if (!silent) setLoading(true);
    try {
      const settled = await Promise.allSettled([
        VmRequestsService.listAll("pending"),
        SpecChangeRequestsService.listAll({ status: "pending" }),
        BatchProvisionService.listPending(),
        AiApiService.listAllRequests(),
        JobsService.list({ statuses: ["failed", "blocked"], historyDays: 7, limit: 50 }),
        MonitoringService.listAlerts({ active: true, limit: 100 }),
        MiningIncidentsService.list({ limit: 200 }),
      ]);
      const ok = (index) => settled[index].status === "fulfilled";
      const value = (index) => ok(index) ? settled[index].value : null;
      const aiPending = value(3)?.data?.filter((request) => request.status === "pending").length ?? 0;
      const alertRows = value(5);
      /* 只算還沒被管理員定奪的事件（detected 待判斷、suspended 已凍結待處置） */
      const miningRows = value(6);
      const miningActive = (Array.isArray(miningRows) ? miningRows : miningRows?.data ?? [])
        .filter((incident) => incident.status === "detected" || incident.status === "suspended").length;
      setChecks((prev) => {
        const pick = (fresh, next, previous) => (silent && !fresh ? previous : next);
        return {
          requests: pick(ok(0) && ok(1), countRows(value(0)) + countRows(value(1)), prev.requests),
          batches: pick(ok(2), countRows(value(2)), prev.batches),
          aiRequests: pick(ok(3), aiPending, prev.aiRequests),
          failedJobs: pick(ok(4), countRows(value(4)), prev.failedJobs),
          alerts: pick(ok(5), Array.isArray(alertRows) ? alertRows : alertRows?.data ?? [], prev.alerts),
          miningIncidents: pick(ok(6), miningActive, prev.miningIncidents),
          unavailable: settled.filter((result) => result.status === "rejected").length,
        };
      });
    } finally {
      checksInFlightRef.current = false;
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => { loadChecks(); }, [loadChecks]);
  useAutoRefresh(() => loadChecks(true));

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
  /* 資料不完整的原因：頁首狀態籤顯示短句，滑過看完整說明 */
  const incompleteReason = !overview ? "noData"
    : overview.data_status === "partial" ? "partial"
      : overviewError || overview.data_status === "stale" ? "stale"
        : "checks";
  const INCOMPLETE_TEXT = {
    noData: { chip: "AdminDashboardPage.chipNoData", full: "AdminDashboardPage.emptyUnavailable" },
    partial: { chip: "AdminDashboardPage.chipPartial", full: "AdminDashboardPage.pvePartialMessage" },
    stale: { chip: "AdminDashboardPage.chipStale", full: "AdminDashboardPage.pveStaleMessage" },
    checks: { chip: "AdminDashboardPage.chipChecksUnavailable", full: "AdminDashboardPage.issueUnavailableTitle" },
  }[incompleteReason];
  const updatedAtText = overview
    ? t("AdminDashboardPage.pveUpdatedAt", { time: formatCheckedAt(overview.collected_at, i18n.language) })
    : t("AdminDashboardPage.pveNotChecked");

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
    <PageHeader title={t("AdminDashboardPage.greeting", { name })}>
      {/* 資料新舊與完整度集中在這裡：正常時是灰字更新時間，不完整時前面加上橘色的連線狀態、更新時間轉藍 */}
      {!focusMode && (!busy && incomplete
        ? <span className={styles.checkedAtWarn} role="status" title={t(INCOMPLETE_TEXT.full)}>
          <MIcon name="sync_problem" size={15} />
          <span>{t(INCOMPLETE_TEXT.chip)}</span>
          {overview && <span className={styles.checkedAtTime}>{updatedAtText}</span>}
          <span className={styles.srOnly}>{t(INCOMPLETE_TEXT.full)}</span>
        </span>
        : <span className={styles.checkedAt}>{updatedAtText}</span>)}
    </PageHeader>

    {!focusMode && stats.length > 0 && <section className={styles.statsGrid} aria-label={t("AdminDashboardPage.resourceOverview")}>
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

    {/* AI 助手：沒開始對話前只是一條輸入列，不要先佔掉整片高度 */}
    <section className={`${styles.assistant} ${focusMode ? styles.assistantFocused : ""}`} aria-labelledby="admin-assistant-title">
      {/* 閒置時不出現整條標頭，助手身份直接放在輸入列上 */}
      {conversationPrompt && <div className={styles.assistantHead}>
        <div className={styles.assistantIdentity}>
          <span className={styles.assistantIcon}><MIcon name="support_agent" size={24} /></span>
          <h2 id="admin-assistant-title">{t("AdminDashboardPage.assistantLabel")}</h2>
        </div>
        <div className={styles.assistantActions}>
          <button type="button" onClick={() => setFocusMode((value) => !value)}>
            <MIcon name={focusMode ? "close_fullscreen" : "open_in_full"} size={15} />
            {focusMode ? t("AdminDashboardPage.backToOverview") : t("AdminDashboardPage.expandChat")}
          </button>
          <button type="button" onClick={resetAssistant}>
            <MIcon name="refresh" size={15} />
            {t("AdminDashboardPage.askAgain")}
          </button>
        </div>
      </div>}

      {conversationPrompt ? <AiPveChat initialPrompt={conversationPrompt} compact={!focusMode} fill={focusMode} />
        : <form className={styles.assistantForm} onSubmit={openAssistant}>
          <div className={styles.assistantIdentity}>
            <span className={styles.assistantIcon}><MIcon name="support_agent" size={20} /></span>
            <span id="admin-assistant-title" className={styles.assistantName}>{t("AdminDashboardPage.assistantLabel")}</span>
          </div>
          <div className={styles.assistantInput}>
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

    {!focusMode && <section className={styles.attention} aria-label={t("AdminDashboardPage.attentionTitle")} aria-busy={busy}>
      {busy ? <div className={styles.checking} role="status"><MIcon name="sync" size={18} spin />{t("AdminDashboardPage.checking")}</div> : <>
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

    </section>}
  </div>;
}
