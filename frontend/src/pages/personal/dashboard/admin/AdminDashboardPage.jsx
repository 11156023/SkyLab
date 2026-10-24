import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import AiPveChat from "../../../../components/AiPveChat/AiPveChat";
import MIcon from "../../../../components/MIcon";
import usePveOverview from "../../../../hooks/usePveOverview";
import { useAuth } from "../../../../contexts/AuthContext";
import { useAllAiRequestsQuery } from "../../../../queries/aiApi";
import { usePendingBatchProvisionQuery } from "../../../../queries/batchProvision";
import { useJobsQuery } from "../../../../queries/jobs";
import { useMiningIncidentsQuery } from "../../../../queries/miningIncidents";
import { useAlertsQuery } from "../../../../queries/monitoring";
import { useAllSpecChangeRequestsQuery } from "../../../../queries/specChangeRequests";
import { useAllVmRequestsQuery } from "../../../../queries/vmRequests";
import {
  buildFyiStats,
  buildTodayRows,
  buildUrgentRows,
  mergeInfraProblems,
} from "./adminAttention";
import { formatTime } from "../../../../utils/formatDate";
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

/* 待辦來源的查詢參數（模組常數：同一組參數產生同一個 query key） */
const PENDING_SPEC_PARAMS = Object.freeze({ status: "pending" });
/* 待審數量交給後端依狀態過濾後回 count；不帶條件只會拿到最新一頁（上限 100），
   較舊的待審件會被擠出去而漏算 */
const PENDING_AI_PARAMS = Object.freeze({ status: "pending", limit: 1 });
const FAILED_JOBS_PARAMS = Object.freeze({ statuses: ["failed", "blocked"], historyDays: 7, limit: 50 });
const ACTIVE_ALERTS_PARAMS = Object.freeze({ active: true, limit: 100 });
/* 只算還沒被管理員定奪的事件（detected 待判斷、suspended 已凍結待處置）；
   由後端依狀態過濾，不在全部事件的前幾筆裡找，才不會漏掉較舊的未結事件 */
const DETECTED_MINING_PARAMS = Object.freeze({ status: "detected", limit: 1000 });
const SUSPENDED_MINING_PARAMS = Object.freeze({ status: "suspended", limit: 1000 });

const NO_ALERTS = Object.freeze([]);

function alertRowsOf(response) {
  if (Array.isArray(response)) return response;
  return response?.data ?? NO_ALERTS;
}

/* 待辦來源的狀態不看 isPending／isError：沒資料時 v5 每次重抓都把 status 改回 pending、清掉 error，
   一直失敗的來源會讓待辦區每 30 秒閃回「檢查中」、「資料不完整」也跟著消失。errorUpdateCount 重抓時不會歸零 */
function neverSettled(query) {
  return query.data === undefined && query.errorUpdateCount === 0;
}

function failing(query) {
  return query.data === undefined ? query.errorUpdateCount > 0 : query.isError;
}

export default function AdminDashboardPage() {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();
  const { user } = useAuth();
  const { overview, loading: overviewLoading, error: overviewError } = usePveOverview();
  const [assistantPrompt, setAssistantPrompt] = useState("");
  const assistantInputRef = useRef(null);
  const [conversationPrompt, setConversationPrompt] = useState("");
  /* 放大模式：對話佔滿版面，統計卡與待辦暫時收起來 */
  const [focusMode, setFocusMode] = useState(false);
  /* 待辦來源各自一支查詢（TanStack Query），每 30 秒背景刷新、分頁隱藏時暫停，互不牽連：
     某個來源失敗只影響它自己那個數字，背景刷新失敗時沿用上一次的資料，
     免得待辦列在 0 與實際值之間跳動；缺漏照樣記進 unavailable。
     與審核頁、監控頁同參數時共用快取，在那邊處理完回到首頁數字就是新的。 */
  const vmRequestsQuery = useAllVmRequestsQuery("pending");
  const specRequestsQuery = useAllSpecChangeRequestsQuery(PENDING_SPEC_PARAMS);
  const batchesQuery = usePendingBatchProvisionQuery();
  const aiRequestsQuery = useAllAiRequestsQuery(PENDING_AI_PARAMS);
  const failedJobsQuery = useJobsQuery(FAILED_JOBS_PARAMS);
  const alertsQuery = useAlertsQuery(ACTIVE_ALERTS_PARAMS);
  const detectedMiningQuery = useMiningIncidentsQuery(DETECTED_MINING_PARAMS);
  const suspendedMiningQuery = useMiningIncidentsQuery(SUSPENDED_MINING_PARAMS);
  const checkQueries = [
    vmRequestsQuery,
    specRequestsQuery,
    batchesQuery,
    aiRequestsQuery,
    failedJobsQuery,
    alertsQuery,
    detectedMiningQuery,
    suspendedMiningQuery,
  ];
  /* 首次載入要等每個來源都有結果（成功或失敗）才顯示待辦，同原本 allSettled；
     之後背景重抓（包含持續失敗的來源在重試）都不回到「檢查中」。
     用 filter 不用 some：結果物件是存取追蹤的，每支查詢用到的欄位都要在 render 時讀到 */
  const loading = checkQueries.filter(neverSettled).length > 0;
  const checks = {
    requests: countRows(vmRequestsQuery.data) + countRows(specRequestsQuery.data),
    batches: countRows(batchesQuery.data),
    aiRequests: countRows(aiRequestsQuery.data),
    failedJobs: countRows(failedJobsQuery.data),
    alerts: alertRowsOf(alertsQuery.data),
    miningIncidents: countRows(detectedMiningQuery.data) + countRows(suspendedMiningQuery.data),
    unavailable: checkQueries.filter(failing).length,
  };

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
    ? t("AdminDashboardPage.pveUpdatedAt", { time: formatTime(overview.collected_at, "—", { seconds: true }) })
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

    {!focusMode && stats.length > 0 && <section className={styles.statsGrid} aria-label={t("AdminDashboardPage.resourceOverview")} data-guide="admin-home-stats">
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
    <section className={`${styles.assistant} ${focusMode ? styles.assistantFocused : ""}`} aria-labelledby="admin-assistant-title" data-guide="admin-home-assistant">
      {/* 閒置時不出現整條標頭，助手身份直接放在輸入列上 */}
      {conversationPrompt && <div className={styles.assistantHead}>
        <div className={styles.assistantIdentity}>
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
        <section className={`${styles.tier} ${urgent.length ? styles.tierNow : ""}`} aria-labelledby="admin-urgent-title" data-guide="admin-home-urgent">
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

        <section className={styles.tier} aria-labelledby="admin-today-title" data-guide="admin-home-today">
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
