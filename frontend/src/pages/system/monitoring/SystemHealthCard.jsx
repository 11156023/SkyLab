import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./MonitoringPage.module.scss";
import MIcon from "../../../components/MIcon";
import LoadingState from "../../../components/LoadingState/LoadingState";
import { MonitoringService } from "../../../services/monitoring";
import { formatDateTime } from "../../../utils/formatDate";

/* 任務明細收合偏好記在本機（與節點用量卡同一套作法） */
const OPEN_STORAGE_KEY = "skylab.monitoringHealthOpen";

function loadOpen() {
  try {
    return window.localStorage.getItem(OPEN_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function saveOpen(open) {
  try {
    window.localStorage.setItem(OPEN_STORAGE_KEY, open ? "1" : "0");
  } catch {
    // localStorage 不可用時偏好僅本次瀏覽生效
  }
}

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
};

/* 這些元件的名稱帶有部署資訊（PVE 連線名稱、Gateway 位址），直接用後端給的 label */
function usesBackendLabel(name) {
  return name.includes(":") || name === "gateway";
}

function componentIcon(name) {
  if (name.startsWith("pve")) return "dns";
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

export default function SystemHealthCard() {
  const { t } = useTranslation("system");
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState(loadOpen);

  const load = useCallback(async (signal) => {
    try {
      setHealth(await MonitoringService.getSystemHealth({ signal }));
      setError(false);
    } catch (err) {
      if (!err?.cancelled) setError(true);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    const timer = setInterval(() => load(), 30_000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [load]);

  function toggleOpen() {
    setOpen((value) => {
      saveOpen(!value);
      return !value;
    });
  }

  if (health === null) {
    return (
      <div className={styles.card}>
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
  const tasks = [...health.tasks].sort(
    (a, b) =>
      (TASK_ORDER[a.status] ?? 9) - (TASK_ORDER[b.status] ?? 9) ||
      a.loop.localeCompare(b.loop) ||
      a.task.localeCompare(b.task),
  );

  return (
    <div className={styles.card}>
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
              <span className={styles.healthComponentName}>
                {/* pve:<id>／gateway 直接用後端給的名稱；「:」在 i18next 是命名空間分隔符 */}
                {usesBackendLabel(component.name)
                  ? component.label
                  : t(`SystemHealth.component.${component.name}`, {
                      defaultValue: component.label,
                    })}
              </span>
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
            <table className={`${styles.table} ${styles.healthTable}`}>
              <colgroup>
                <col className={styles.healthColTask} />
                <col className={styles.healthColStatus} />
                <col className={styles.healthColTime} />
                <col className={styles.healthColTime} />
                <col className={styles.healthColNum} />
                <col className={styles.healthColNum} />
                <col />
              </colgroup>
              <thead>
                <tr>
                  <th className={styles.th}>{t("SystemHealth.colTask")}</th>
                  <th className={styles.th}>{t("MonitoringPage.colStatus")}</th>
                  <th className={styles.th}>{t("SystemHealth.colLastRun")}</th>
                  <th className={styles.th}>{t("SystemHealth.colLastSuccess")}</th>
                  <th className={styles.th}>{t("SystemHealth.colFailures")}</th>
                  <th className={styles.th}>{t("SystemHealth.colDuration")}</th>
                  <th className={styles.th}>{t("SystemHealth.colLastError")}</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr key={`${task.loop}/${task.task}`} className={styles.tr}>
                    <td className={styles.td}>
                      <span className={styles.healthTaskName}>{task.task}</span>
                      <span className={styles.mutedText}>
                        {t(`SystemHealth.loop.${task.loop}`, { defaultValue: task.loop })}
                      </span>
                    </td>
                    <td className={styles.td}>
                      <StatusBadge status={task.status} t={t} />
                    </td>
                    <td className={styles.td}>
                      <Ago ts={task.last_run_at} now={now} t={t} />
                    </td>
                    <td className={styles.td}>
                      <Ago ts={task.last_success_at} now={now} t={t} />
                    </td>
                    <td className={`${styles.td} ${styles.numericCell}`}>
                      {task.consecutive_failures}
                    </td>
                    <td className={`${styles.td} ${styles.numericCell}`}>
                      {task.last_duration_ms != null ? `${Math.round(task.last_duration_ms)} ms` : "—"}
                    </td>
                    <td className={styles.td}>
                      {task.last_error ? (
                        <span className={styles.healthError} title={task.last_error}>
                          {task.last_error}
                        </span>
                      ) : (
                        <span className={styles.mutedText}>—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
