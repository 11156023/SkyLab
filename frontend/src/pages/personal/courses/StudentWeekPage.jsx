import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import EmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { useQueryView } from "../../../hooks/useQueryLoadState";
import { CoursesService } from "../../../services/courses";
import { useCoursePathQuery, usePracticeMachinesQuery, useWeeklyTasksQuery } from "../../../queries/courses";
import styles from "./StudentWeekPage.module.scss";
import { feedbackFor } from "./studentWeekFeedback";

const EMPTY = [];

const STATUS_ICONS = {
  completed: "check_circle",
  passed: "check_circle",
  partial: "rule",
  needs_review: "person_search",
  failed: "error",
  cancelled: "cancel",
  running: "sync",
  pending: "schedule",
  env_error: "report",
};

function createGuideDemoWeek(t) {
  const checkpointId = "demo-checkpoint";
  return {
    loading: false,
    failed: false,
    path: { id: "demo", title: t("StudentWeekPage.guideDemoCourseTitle") },
    week: {
      id: "demo-week-1",
      teaching_class_name: t("StudentWeekPage.guideDemoCourseTitle"),
      title: t("StudentWeekPage.guideDemoWeekTitle"),
      week_number: 1,
      session_date: "2026-09-09",
      checkpoints: [{
        id: checkpointId,
        title: t("StudentWeekPage.guideDemoCheckpointTitle"),
        description: t("StudentWeekPage.guideDemoCheckpointDescription"),
        latest_check: {
          status: "passed",
          score: 3,
          max_score: 3,
          summary: t("StudentWeekPage.guideDemoFeedback"),
          items: [{ item_id: checkpointId, status: "passed", score: 3, max_score: 3, comment: t("StudentWeekPage.guideDemoFeedback") }],
        },
      }],
      files: [{ id: "demo-file", filename: "linux-week-01.pdf" }],
      teacher_reply: t("StudentWeekPage.guideDemoTeacherReply"),
    },
    machines: [{
      machine_node_id: "demo-machine",
      node_key: "main",
      name: t("StudentWeekPage.guideDemoMachineName"),
      role: t("StudentWeekPage.guideDemoMachineRole"),
      resource_type: "lxc",
      vmid: null,
      public_url: null,
    }],
  };
}
export default function StudentWeekPage() {
  const { t, i18n } = useTranslation("personal");
  const navigate = useNavigate();
  const { pathId, weekId } = useParams();
  const isGuideDemo = pathId === "demo" || weekId === "demo-week-1";
  const [openingFileId, setOpeningFileId] = useState(null);

  /* 與課程頁共用快取（從課程頁點進來直接有資料）；導覽示範模式不讀 API */
  const live = !isGuideDemo && pathId != null;
  const pathQuery = useCoursePathQuery(pathId, { enabled: live });
  const weeksQuery = useWeeklyTasksQuery(pathId, { enabled: live });
  const machinesQuery = usePracticeMachinesQuery(pathId, { enabled: live });
  /* 首次載入還沒有資料才算載入中／載入失敗；背景刷新失敗沿用上一份。
     首載失敗後背景重試期間維持失敗畫面，不閃回載入中（見 useQueryView） */
  const pathView = useQueryView(pathQuery);
  const weeksView = useQueryView(weeksQuery);
  const machinesView = useQueryView(machinesQuery);

  let view;
  if (isGuideDemo) {
    view = createGuideDemoWeek(t);
  } else {
    const weeks = Array.isArray(weeksQuery.data) ? weeksQuery.data : EMPTY;
    view = {
      loading: live && (pathView.loading || weeksView.loading || machinesView.loading),
      failed: Boolean(weeksView.error),
      path: pathQuery.data ?? null,
      week: weeks.find((item) => String(item.id) === String(weekId)) ?? null,
      machines: Array.isArray(machinesQuery.data) ? machinesQuery.data : EMPTY,
    };
  }

  async function openDocument(file) {
    if (isGuideDemo) return;
    setOpeningFileId(file.key);
    const preview = window.open("", "_blank");
    try {
      const blob = file.assignment_id
        ? await CoursesService.getAiAssignmentSourceDocument(pathId, file.assignment_id)
        : await CoursesService.getWeeklyTaskDocument(pathId, weekId, file.id);
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
    const back = <button type="button" className={`${styles.backBtn} ${styles.backBtnAlone}`} onClick={() => navigate(`/courses/${pathId}`)}><MIcon name="arrow_back" size={18} />{t("StudentWeekPage.back")}</button>;
    return <div className={styles.page}>
      {view.failed
        ? <>{back}<ErrorState /></>
        : <EmptyState icon="event_busy" title={t("StudentWeekPage.notFoundTitle")} description={t("StudentWeekPage.notFoundDesc")} action={back} />}
    </div>;
  }

  const teacherReply = String(view.week.teacher_reply ?? "").trim();
  const documents = [
    ...(view.week.files ?? []).map((file) => ({ ...file, key: `weekly-${file.id}` })),
    ...(view.week.source_documents ?? []).map((file) => ({ ...file, key: `assignment-${file.assignment_id}` })),
  ];

  return <div className={styles.page}>
    <div data-guide="course-week-header">
      <PageHeader
        eyebrow={view.path?.title ?? view.week.teaching_class_name}
        title={view.week.title}
        subtitle={t("StudentWeekPage.weekMeta", { week: view.week.week_number, date: new Intl.DateTimeFormat(i18n.language, { dateStyle: "long" }).format(new Date(`${view.week.session_date}T00:00:00`)) })}
      >
        <button type="button" className={styles.backBtn} onClick={() => navigate(`/courses/${pathId}`)}><MIcon name="arrow_back" size={18} />{t("StudentWeekPage.back")}</button>
      </PageHeader>
    </div>

    {isGuideDemo && (
      <div className={styles.guideDemoNotice}>
        <MIcon name="visibility" size={17} />
        <span><strong>{t("StudentWeekPage.guideDemoNoticeTitle")}</strong>{t("StudentWeekPage.guideDemoNoticeDescription")}</span>
      </div>
    )}

    <main className={styles.grid}>
      <section className={styles.panel} data-guide="course-week-feedback">
        <div className={styles.panelHeading}><span><MIcon name="rate_review" size={20} /></span><div><h2>{t("StudentWeekPage.feedbackTitle")}</h2><p>{t("StudentWeekPage.feedbackHint")}</p></div></div>
        {/* 老師這週的回覆：一週一則，老師再回覆就換成新的 */}
        {teacherReply && <article className={styles.teacherReply} data-guide="course-week-teacher-reply">
          <span><MIcon name="forum" size={18} /></span>
          <div>
            <strong>{t("StudentWeekPage.teacherReplyTitle")}</strong>
            <p>{teacherReply}</p>
          </div>
        </article>}
        {view.week.checkpoints.length ? <div className={styles.feedbackList}>
          {view.week.checkpoints.map((checkpoint, index) => {
            const feedback = feedbackFor(checkpoint);
            // 多台機器的作業：標出這一項在哪台機器上檢查
            const machine = checkpoint.machine_node_key
              ? view.machines.find((entry) => entry.node_key === checkpoint.machine_node_key)
              : null;
            return <article key={checkpoint.id} className={`${styles.feedbackCard} ${styles[`feedback_${feedback.status}`] ?? ""}`}>
              <div className={styles.feedbackTop}>
                <span className={styles.number}>{index + 1}</span>
                <div>
                  <strong>{checkpoint.title}</strong>
                  {checkpoint.description && <p>{checkpoint.description}</p>}
                  {machine && <small className={styles.checkpointMachine}><MIcon name="dns" size={14} />{t("StudentWeekPage.checkpointMachine", { machine: machine.name })}</small>}
                </div>
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
        <section className={styles.panel} data-guide="course-week-machine">
          <div className={styles.panelHeading}><span><MIcon name="dns" size={20} /></span><div><h2>{t("StudentWeekPage.machineTitle")}</h2><p>{t("StudentWeekPage.machineAllHint")}</p></div></div>
          {view.machines.length ? <div className={styles.machineList}>{view.machines.map((machine) => <article className={styles.machineCard} key={machine.machine_node_id}>
            <div className={styles.machineCopy}><span><MIcon name={machine.resource_type === "lxc" ? "terminal" : "desktop_windows"} size={20} /></span><div><strong>{machine.name}</strong><small>{machine.role}{machine.vmid ? ` · VMID ${machine.vmid}` : ""}</small></div></div>
            <div className={styles.machineActions}>
              {machine.public_url ? <a href={machine.public_url} target="_blank" rel="noreferrer"><MIcon name="language" size={17} />{t("StudentWeekPage.openWebsite")}</a> : (machine.forward_endpoints ?? []).length === 0 && <span className={styles.urlPending}><MIcon name="link_off" size={16} />{t("StudentWeekPage.urlPending")}</span>}
              {/* 對外 port：SSH / 資料庫這類不是網頁的服務，學生用 host:port 連 */}
              {(machine.forward_endpoints ?? []).map((endpoint) => <code key={`${endpoint.protocol}-${endpoint.external_port}`} title={t("StudentWeekPage.forwardEndpointTitle", { port: endpoint.internal_port, protocol: endpoint.protocol })}><MIcon name="swap_horiz" size={15} />{endpoint.host ? `${endpoint.host}:${endpoint.external_port}` : t("StudentWeekPage.forwardPortOnly", { port: endpoint.external_port })}</code>)}
              {machine.vmid && <button type="button" onClick={() => navigate(`/my-resources/${machine.vmid}`)}><MIcon name="tune" size={17} />{t("StudentWeekPage.openMachine")}</button>}
            </div>
          </article>)}</div> : <EmptyState icon="dns" title={t("StudentWeekPage.noMachineTitle")} description={t("StudentWeekPage.noMachineDesc")} />}
        </section>

        {documents.length > 0 && <section className={styles.panel} data-guide="course-week-materials">
          <div className={styles.panelHeading}><span><MIcon name="description" size={20} /></span><div><h2>{t("StudentWeekPage.filesTitle")}</h2></div></div>
          <div className={styles.fileList}>{documents.map((file) => <button type="button" key={file.key} disabled={isGuideDemo || openingFileId !== null} onClick={() => openDocument(file)}><MIcon name="picture_as_pdf" size={19} /><span>{file.filename}</span><MIcon name={openingFileId === file.key ? "sync" : "open_in_new"} size={17} /></button>)}</div>
        </section>}
      </aside>
    </main>
  </div>;
}
