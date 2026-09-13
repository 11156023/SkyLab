import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import EmptyState from "../../../components/EmptyState/EmptyState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import { CoursesService } from "../../../services/courses";
import styles from "./StudentWeekPage.module.scss";

const STATUS_ICONS = {
  completed: "check_circle",
  passed: "check_circle",
  partial: "rule",
  needs_review: "person_search",
  failed: "error",
  cancelled: "cancel",
  running: "sync",
  pending: "schedule",
};

function feedbackFor(checkpoint) {
  const check = checkpoint.latest_check;
  if (!check) return { score: null, maxScore: null, text: "", status: "none" };
  const item = (check.items ?? []).find((entry) => entry.item_id === checkpoint.id);
  const itemStatus = ["passed", "failed", "partial", "needs_review"].includes(item?.status)
    ? item.status : null;
  return {
    score: item?.score ?? check.score,
    maxScore: item?.max_score ?? check.max_score,
    text: item?.comment || check.summary || check.error || "",
    status: itemStatus || check.status,
  };
}

export default function StudentWeekPage() {
  const { t, i18n } = useTranslation("personal");
  const navigate = useNavigate();
  const { pathId, weekId } = useParams();
  const [view, setView] = useState({ loading: true, failed: false, path: null, week: null, machines: [] });
  const [openingFileId, setOpeningFileId] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const [pathResult, weeksResult, machinesResult] = await Promise.allSettled([
        CoursesService.getPath(pathId),
        CoursesService.getWeeklyTasks(pathId),
        CoursesService.getPracticeMachines(pathId),
      ]);
      if (cancelled) return;
      const weeks = weeksResult.status === "fulfilled" && Array.isArray(weeksResult.value)
        ? weeksResult.value : [];
      setView({
        loading: false,
        failed: weeksResult.status === "rejected",
        path: pathResult.status === "fulfilled" ? pathResult.value : null,
        week: weeks.find((item) => String(item.id) === String(weekId)) ?? null,
        machines: machinesResult.status === "fulfilled" && Array.isArray(machinesResult.value)
          ? machinesResult.value : [],
      });
    }
    load();
    return () => { cancelled = true; };
  }, [pathId, weekId]);

  const machines = useMemo(() => {
    if (!view.week?.target_node_key) return view.machines;
    return view.machines.filter((machine) => machine.node_key === view.week.target_node_key);
  }, [view.machines, view.week]);

  async function openDocument(file) {
    setOpeningFileId(file.id);
    const preview = window.open("", "_blank");
    try {
      const blob = await CoursesService.getWeeklyTaskDocument(pathId, weekId, file.id);
      const url = URL.createObjectURL(blob);
      if (preview) preview.location = url;
      else window.open(url, "_blank", "noopener,noreferrer");
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (error) {
      preview?.close();
      toast.error(error?.message ?? t("StudentWeekPage.fileOpenFailed"));
    } finally {
      setOpeningFileId(null);
    }
  }

  if (view.loading) return <div className={styles.page}><LoadingState fullPage text={t("StudentWeekPage.loading")} /></div>;

  if (!view.week) {
    return <div className={styles.page}>
      <button type="button" className={styles.backButton} onClick={() => navigate(`/courses/${pathId}`)}><MIcon name="arrow_back" size={18} />{t("StudentWeekPage.back")}</button>
      <EmptyState icon="event_busy" title={t("StudentWeekPage.notFoundTitle")} description={view.failed ? t("StudentWeekPage.loadFailed") : t("StudentWeekPage.notFoundDesc")} />
    </div>;
  }

  return <div className={styles.page}>
    <header className={styles.header}>
      <button type="button" className={styles.backButton} onClick={() => navigate(`/courses/${pathId}`)}><MIcon name="arrow_back" size={18} />{t("StudentWeekPage.back")}</button>
      <div>
        <p>{view.path?.title ?? view.week.teaching_class_name}</p>
        <h1>{view.week.title}</h1>
        <span>{t("StudentWeekPage.weekMeta", { week: view.week.week_number, date: new Intl.DateTimeFormat(i18n.language, { dateStyle: "long" }).format(new Date(`${view.week.session_date}T00:00:00`)) })}</span>
      </div>
    </header>

    <main className={styles.grid}>
      <section className={styles.panel}>
        <div className={styles.panelHeading}><span><MIcon name="rate_review" size={20} /></span><div><h2>{t("StudentWeekPage.feedbackTitle")}</h2><p>{t("StudentWeekPage.feedbackHint")}</p></div></div>
        {view.week.checkpoints.length ? <div className={styles.feedbackList}>
          {view.week.checkpoints.map((checkpoint, index) => {
            const feedback = feedbackFor(checkpoint);
            return <article key={checkpoint.id} className={`${styles.feedbackCard} ${styles[`feedback_${feedback.status}`] ?? ""}`}>
              <div className={styles.feedbackTop}>
                <span className={styles.number}>{index + 1}</span>
                <div><strong>{checkpoint.title}</strong>{checkpoint.description && <p>{checkpoint.description}</p>}</div>
                <span className={styles.feedbackStatus}><MIcon name={STATUS_ICONS[feedback.status] ?? "hourglass_empty"} size={17} />{feedback.status === "none" ? t("StudentWeekPage.noFeedback") : t(`StudentWeekPage.status_${feedback.status}`)}</span>
              </div>
              {feedback.status !== "none" && <div className={styles.feedbackBody}>
                {feedback.score != null && <strong>{t("StudentWeekPage.score", { score: feedback.score, max: feedback.maxScore ?? "—" })}</strong>}
                <p>{feedback.text || t("StudentWeekPage.noComment")}</p>
              </div>}
            </article>;
          })}
        </div> : <EmptyState icon="rate_review" title={t("StudentWeekPage.noCheckpointsTitle")} description={t("StudentWeekPage.noCheckpointsDesc")} />}
      </section>

      <aside className={styles.side}>
        <section className={styles.panel}>
          <div className={styles.panelHeading}><span><MIcon name="dns" size={20} /></span><div><h2>{t("StudentWeekPage.machineTitle")}</h2><p>{view.week.target_node_key ? t("StudentWeekPage.machineSelectedHint") : t("StudentWeekPage.machineAllHint")}</p></div></div>
          {machines.length ? <div className={styles.machineList}>{machines.map((machine) => <article className={styles.machineCard} key={machine.machine_node_id}>
            <div className={styles.machineCopy}><span><MIcon name={machine.resource_type === "lxc" ? "terminal" : "desktop_windows"} size={20} /></span><div><strong>{machine.name}</strong><small>{machine.role}{machine.vmid ? ` · VMID ${machine.vmid}` : ""}</small></div></div>
            <div className={styles.machineActions}>
              {machine.public_url ? <a href={machine.public_url} target="_blank" rel="noreferrer"><MIcon name="language" size={17} />{t("StudentWeekPage.openWebsite")}</a> : <span className={styles.urlPending}><MIcon name="link_off" size={16} />{t("StudentWeekPage.urlPending")}</span>}
              {machine.vmid && <button type="button" onClick={() => navigate(`/my-resources/${machine.vmid}`)}><MIcon name="tune" size={17} />{t("StudentWeekPage.openMachine")}</button>}
            </div>
          </article>)}</div> : <EmptyState icon="dns" title={t("StudentWeekPage.noMachineTitle")} description={t("StudentWeekPage.noMachineDesc")} />}
        </section>

        {view.week.files.length > 0 && <section className={styles.panel}>
          <div className={styles.panelHeading}><span><MIcon name="description" size={20} /></span><div><h2>{t("StudentWeekPage.filesTitle")}</h2></div></div>
          <div className={styles.fileList}>{view.week.files.map((file) => <button type="button" key={file.id} disabled={openingFileId !== null} onClick={() => openDocument(file)}><MIcon name="picture_as_pdf" size={19} /><span>{file.filename}</span><MIcon name={openingFileId === file.id ? "sync" : "open_in_new"} size={17} /></button>)}</div>
        </section>}
      </aside>
    </main>
  </div>;
}
