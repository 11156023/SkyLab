import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import styles from "./TemplatesPage.module.scss";
import { TEMPLATE_TASK_LABEL, TemplatesService } from "../../../services/templates";
import { TaskProgressBar, TaskStatusBadge } from "./TemplateBadges";

/** 最近背景任務卡：有進行中任務時 3 秒輪詢，否則 15 秒 */
export default function TemplateTasksCard() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState([]);
  const timerRef = useRef(null);

  useEffect(() => {
    let cancelled = false;

    const schedule = (list) => {
      const active = list.some((t) => t.status === "queued" || t.status === "running");
      timerRef.current = setTimeout(load, active ? 3_000 : 15_000);
    };

    const load = async () => {
      try {
        const res = await TemplatesService.listTasks(20);
        if (cancelled) return;
        setTasks(res?.data ?? []);
        schedule(res?.data ?? []);
      } catch {
        if (!cancelled) schedule([]);
      }
    };

    load();
    return () => {
      cancelled = true;
      clearTimeout(timerRef.current);
    };
  }, []);

  if (tasks.length === 0) return null;

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <h2 className={styles.cardTitle}>最近任務</h2>
      </div>
      <div className={styles.taskList}>
        {tasks.slice(0, 8).map((task) => (
          <div key={task.id} className={styles.taskRow}>
            <div className={styles.taskHead}>
              <span className={styles.taskLabel}>
                {TEMPLATE_TASK_LABEL[task.task_type] ?? task.task_type}
                {task.status === "succeeded" &&
                  task.resource_vmid &&
                  task.task_type === "template.clone" && (
                    <button
                      type="button"
                      className={styles.taskLink}
                      onClick={() => navigate("/my-resources")}
                    >
                      VMID {task.resource_vmid} → 前往資源頁
                    </button>
                  )}
              </span>
              <TaskStatusBadge status={task.status} />
            </div>
            <TaskProgressBar progress={task.progress} status={task.status} />
            {task.error && <p className={styles.taskError}>{task.error}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}
