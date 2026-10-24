import { useTranslation } from "react-i18next";
import { createColumnHelper, useTable } from "@tanstack/react-table";
import styles from "./MonitoringPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import DataTable, { DataTableCells } from "../../../components/DataTable/DataTable";
import { baseFeatures } from "../../../components/DataTable/features";
import { useSystemHealthQuery } from "../../../queries/monitoring";
import { useLoadError } from "../../../hooks/useQueryLoadState";
import { formatDateTime } from "../../../utils/formatDate";
import usePersistedToggle from "./usePersistedToggle";

/* 任務明細收合偏好記在本機（與節點用量卡共用 usePersistedToggle） */
const OPEN_STORAGE_KEY = "skylab.monitoringHealthOpen";

/* 狀態 → 徽章色。橘色只留給「待審核」語意，異常一律紅色（見前端 style guide） */
const STATUS_BADGE = {
  ok: "badge_success",
  down: "badge_danger",
  degraded: "badge_danger",
  failing: "badge_danger",
  stale: "badge_danger",
  warning: "badge_danger",
  attention: "badge_danger",
  disabled: "badge_muted",
  unknown: "badge_muted",
  pending: "badge_muted",
};

const COMPONENT_ICONS = {
  database: "storage",
  redis: "memory",
  worker: "engineering",
  gateway: "router",
  ai_gateway: "smart_toy",
};

/* 這些元件的名稱帶有部署資訊（PVE 連線名稱、Gateway 位址、AI 模型名稱），直接用後端給的 label */
function usesBackendLabel(name) {
  return name.includes(":") || name === "gateway";
}

function componentIcon(name) {
  if (name.startsWith("pve")) return "dns";
  if (name.startsWith("ai_model:")) return "model_training";
  return COMPONENT_ICONS[name] ?? "hub";
}

function StatusBadge({ status, t }) {
  return (
    <span className={`${styles.badge} ${styles[STATUS_BADGE[status] ?? "badge_muted"]}`}>
      {t(`SystemHealth.status.${status}`, { defaultValue: status })}
    </span>
  );
}

/** unix 秒 → 「3 分鐘前」；滑過顯示完整時間 */
function Ago({ ts, now, t }) {
  if (!ts) return <span className={styles.mutedText}>—</span>;
  const diff = Math.max(0, Math.round(now - ts));
  let text;
  if (diff < 60) text = t("SystemHealth.agoSeconds", { count: diff });
  else if (diff < 3600) text = t("SystemHealth.agoMinutes", { count: Math.floor(diff / 60) });
  else if (diff < 86400) text = t("SystemHealth.agoHours", { count: Math.floor(diff / 3600) });
  else text = t("SystemHealth.agoDays", { count: Math.floor(diff / 86400) });
  return <span title={formatDateTime(ts * 1000)}>{text}</span>;
}

/* 任務排序：有問題的排前面，其餘照迴圈／名稱 */
const TASK_ORDER = { failing: 0, stale: 1, warning: 2, pending: 3, ok: 4 };

const EMPTY = Object.freeze([]);

/** 表頭文字（header 是元件，可以直接用 hook）；key 是完整的翻譯鍵 */
function ColTitle({ i18nKey }) {
  const { t } = useTranslation("system");
  return t(i18nKey);
}

function TaskNameCell({ task }) {
  const { t } = useTranslation("system");
  return (
    <>
      <span className={styles.healthTaskName}>{task.task}</span>
      <span className={styles.healthTaskLoop}>
        {t(`SystemHealth.loop.${task.loop}`, { defaultValue: task.loop })}
      </span>
    </>
  );
}

function TaskStatusCell({ status }) {
  const { t } = useTranslation("system");
  return <StatusBadge status={status} t={t} />;
}

function TaskAgoCell({ ts, now }) {
  const { t } = useTranslation("system");
  return <Ago ts={ts} now={now} t={t} />;
}

const columnHelper = createColumnHelper();

/* 欄位定義放模組層級：FlexRender 把函式型的 header／cell 當元件掛載，render 裡新建會整格重新掛載。
   「多久以前」的基準時間 now 跟著每次健康資料變，經 rowState 傳進儲存格 */
const taskColumns = columnHelper.columns([
  columnHelper.accessor("task", {
    header: () => <ColTitle i18nKey="SystemHealth.colTask" />,
    cell: ({ row }) => <TaskNameCell task={row.original} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("status", {
    header: () => <ColTitle i18nKey="MonitoringPage.colStatus" />,
    cell: ({ row }) => <TaskStatusCell status={row.original.status} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("last_run_at", {
    header: () => <ColTitle i18nKey="SystemHealth.colLastRun" />,
    cell: ({ row, rowState }) => <TaskAgoCell ts={row.original.last_run_at} now={rowState.now} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("last_success_at", {
    header: () => <ColTitle i18nKey="SystemHealth.colLastSuccess" />,
    cell: ({ row, rowState }) => <TaskAgoCell ts={row.original.last_success_at} now={rowState.now} />,
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
  columnHelper.accessor("consecutive_failures", {
    header: () => <ColTitle i18nKey="SystemHealth.colFailures" />,
    cell: ({ row }) => row.original.consecutive_failures,
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.numericCell}` },
  }),
  columnHelper.accessor("last_duration_ms", {
    header: () => <ColTitle i18nKey="SystemHealth.colDuration" />,
    cell: ({ row }) => (row.original.last_duration_ms != null ? `${Math.round(row.original.last_duration_ms)} ms` : "—"),
    meta: { thClassName: styles.th, tdClassName: `${styles.td} ${styles.numericCell}` },
  }),
  columnHelper.accessor("last_error", {
    header: () => <ColTitle i18nKey="SystemHealth.colLastError" />,
    cell: ({ row }) => (row.original.last_error ? (
      <span className={styles.healthError} title={row.original.last_error}>
        {row.original.last_error}
      </span>
    ) : (
      <span className={styles.mutedText}>—</span>
    )),
    meta: { thClassName: styles.th, tdClassName: styles.td },
  }),
]);

export default function SystemHealthCard() {
  const { t } = useTranslation("system");
  const [open, toggleOpen] = usePersistedToggle(OPEN_STORAGE_KEY);
  /* 30 秒背景刷新、分頁隱藏時不輪詢；背景失敗時沿用上一筆，只有還沒拿到資料才顯示錯誤 */
  const healthQuery = useSystemHealthQuery();
  const health = healthQuery.data ?? null;
  /* 還沒拿到資料時的失敗看 useLoadError（沒資料時 v5 每次重抓都把 status 改回 pending、清掉 error，看 isError 會在背景重試時閃回載入中） */
  const error = Boolean(useLoadError(healthQuery).error);

  /* useTable 是 hook，要在下面「還沒拿到資料」的提早 return 之前呼叫，排序也跟著提前 */
  const tasks = health === null ? EMPTY : [...health.tasks].sort(
    (a, b) =>
      (TASK_ORDER[a.status] ?? 9) - (TASK_ORDER[b.status] ?? 9) ||
      a.loop.localeCompare(b.loop) ||
      a.task.localeCompare(b.task),
  );
  const table = useTable({
    features: baseFeatures,
    columns: taskColumns,
    data: tasks,
    getRowId: (task) => `${task.loop}/${task.task}`,
  });

  if (health === null) {
    return (
      <div className={styles.card} data-guide="monitoring-health">
        {error ? (
          <div className={styles.cardEmpty}>
            <MIcon name="warning" size={24} />
            <p>{t("SystemHealth.errorFetch")}</p>
          </div>
        ) : (
          <LoadingState text={t("SystemHealth.loading")} />
        )}
      </div>
    );
  }

  const now = new Date(health.generated_at).getTime() / 1000;
  const problemTasks = health.tasks.filter((task) =>
    ["failing", "stale", "warning"].includes(task.status),
  );

  return (
    <div className={styles.card} data-guide="monitoring-health">
      <button
        type="button"
        className={styles.cardHeaderToggle}
        onClick={toggleOpen}
        aria-expanded={open}
      >
        <h2 className={styles.cardTitle}>
          <MIcon name="monitor_heart" size={18} />
          {t("SystemHealth.title")}
          <StatusBadge status={health.status} t={t} />
        </h2>
        <span className={styles.cardHeaderMeta}>
          {problemTasks.length > 0
            ? t("SystemHealth.problemTasks", { count: problemTasks.length })
            : t("SystemHealth.tasksAllOk", { count: health.tasks.length })}
          <MIcon name={open ? "expand_less" : "expand_more"} size={18} />
        </span>
      </button>

      {/* 依賴元件：永遠顯示，一眼看出 DB／Redis／worker／PVE／Gateway 誰掛了 */}
      <div className={styles.healthComponents}>
        {health.components.map((component) => (
          <div key={component.name} className={styles.healthComponent}>
            <MIcon name={componentIcon(component.name)} size={18} />
            <div className={styles.healthComponentText}>
              {/* pve:<id>／gateway 直接用後端給的名稱；「:」在 i18next 是命名空間分隔符。
                  名稱太長會被截斷（例如 LLM 模型名），滑過顯示全名 */}
              {(() => {
                const name = usesBackendLabel(component.name)
                  ? component.label
                  : t(`SystemHealth.component.${component.name}`, { defaultValue: component.label });
                return <span className={styles.healthComponentName} title={name}>{name}</span>;
              })()}
              <span className={styles.healthComponentMeta} title={component.detail ?? ""}>
                {component.status === "ok" && component.latency_ms != null
                  ? `${component.latency_ms} ms`
                  : (component.detail ?? "")}
              </span>
            </div>
            <StatusBadge status={component.status} t={t} />
          </div>
        ))}
      </div>

      {open && (
        <>
          {health.heartbeat_source === "memory" && (
            <p className={styles.healthNotice}>
              <MIcon name="info" size={16} />
              {t("SystemHealth.memorySourceNotice")}
            </p>
          )}

          {health.loops.length > 0 && (
            <div className={styles.healthLoops}>
              {health.loops.map((loop) => (
                <div key={loop.loop} className={styles.healthLoop}>
                  <span className={styles.healthLoopName}>
                    {t(`SystemHealth.loop.${loop.loop}`, { defaultValue: loop.loop })}
                  </span>
                  <StatusBadge status={loop.status} t={t} />
                  <span className={styles.mutedText}>
                    {t("SystemHealth.lastTick")}{" "}
                    <Ago ts={loop.leader_last_tick_at} now={now} t={t} />
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className={styles.tableScroll}>
            <DataTable
              table={table}
              className={`${styles.table} ${styles.healthTable}`}
              colgroup={(
                <colgroup>
                  <col className={styles.healthColTask} />
                  <col className={styles.healthColStatus} />
                  <col className={styles.healthColTime} />
                  <col className={styles.healthColTime} />
                  <col className={styles.healthColNum} />
                  <col className={styles.healthColNum} />
                  <col />
                </colgroup>
              )}
              renderRow={(row) => (
                <tr key={row.id} className={styles.tr}>
                  <DataTableCells row={row} rowState={{ now }} />
                </tr>
              )}
            />
          </div>
        </>
      )}
    </div>
  );
}
