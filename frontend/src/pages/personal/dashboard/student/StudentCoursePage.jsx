import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import MIcon from "../../../../components/MIcon";
import EmptyState from "../../../../components/EmptyState/EmptyState";
import LoadingState from "../../../../components/LoadingState/LoadingState";
import TerminalDialog from "../../resources/TerminalDialog";
import VncDialog from "../../resources/VncDialog";
import { CoursesService } from "../../../../services/courses";
import { ResourcesService } from "../../../../services/resources";
import {
  assignmentsUntilToday,
  buildPracticeMachines,
  formatAssignmentDate,
  normalizeSchedule,
  pickInProgress,
  practiceMachineActionLabel,
  toPercent,
  waitForPracticeMachine,
} from "./studentDashboard";
import styles from "./StudentCoursePage.module.scss";

/** AI 任務的每個檢查項目可被自動判定的程度。 */
const AI_DETECTABLE_META = {
  auto: { labelKey: "StudentCoursePage.detectableAuto", icon: "smart_toy", tone: "auto" },
  partial: { labelKey: "StudentCoursePage.detectablePartial", icon: "rule", tone: "partial" },
  manual: { labelKey: "StudentCoursePage.detectableManual", icon: "how_to_reg", tone: "manual" },
};

const NO_COURSE_STATUS = { labelKey: "StudentCoursePage.noCourseStatus", tone: "muted", icon: "event_busy" };

function StatusBadge({ meta }) {
  const { t } = useTranslation("personal");
  return (
    <span className={`${styles.statusBadge} ${styles[meta.tone]}`}>
      <MIcon name={meta.icon} size={16} />
      {t(meta.labelKey)}
    </span>
  );
}

/**
 * 學生端單一課程頁（/courses/:pathId）。
 * 內容分三塊：課堂卡片（進度與環境）、課堂機器、截至今天的 AI 任務。
 */
export default function StudentCoursePage() {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();
  const location = useLocation();
  const { pathId } = useParams();

  const [view, setView] = useState({
    loading: true,
    hasError: false,
    resources: [],
    activePath: null,
    pathDetail: null,
    roomDetail: null,
    aiAssignments: [],
    weeklyTasks: [],
    practiceMachines: [],
  });
  const [expandedAssignmentId, setExpandedAssignmentId] = useState(null);
  const [reportingAssignmentId, setReportingAssignmentId] = useState(null);
  const [activePracticeResource, setActivePracticeResource] = useState(null);
  const [openingMachineId, setOpeningMachineId] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function loadCourse() {
      const [pathsResult, resourcesResult, scheduleResult] = await Promise.allSettled([
        CoursesService.listPaths(),
        ResourcesService.list(),
        CoursesService.listSchedule(),
      ]);
      if (cancelled) return;

      const catalogPaths = pathsResult.status === "fulfilled" && Array.isArray(pathsResult.value)
        ? pathsResult.value
        : [];
      const resources = resourcesResult.status === "fulfilled" && Array.isArray(resourcesResult.value)
        ? resourcesResult.value
        : [];
      const schedulePaths = scheduleResult.status === "fulfilled" && Array.isArray(scheduleResult.value)
        ? scheduleResult.value.map(normalizeSchedule)
        : [];

      // 網址指定的課程優先；找不到時退回目前進行中的課程。
      let activePath = (pathId
        && catalogPaths.find((path) => String(path.id) === String(pathId)))
        || pickInProgress(catalogPaths);
      const scheduledVersion = schedulePaths.find(
        (path) => String(path.id) === String(activePath?.id),
      );
      if (activePath && scheduledVersion) {
        activePath = { ...activePath, schedule: scheduledVersion.schedule };
      }

      let pathDetail = null;
      let roomDetail = null;
      let aiAssignments = [];
      let weeklyTasks = [];
      let practiceMachines = [];

      if (activePath) {
        const [pathDetailResult, aiAssignmentsResult, weeklyTasksResult, practiceMachinesResult] = await Promise.allSettled([
          CoursesService.getPath(activePath.id),
          CoursesService.getAiAssignments(activePath.id),
          CoursesService.getWeeklyTasks(activePath.id),
          CoursesService.getPracticeMachines(activePath.id),
        ]);
        if (pathDetailResult.status === "fulfilled") {
          pathDetail = pathDetailResult.value;
          const nextRoom = pickInProgress(pathDetail?.rooms);
          if (nextRoom) {
            try {
              roomDetail = await CoursesService.getRoom(nextRoom.id);
            } catch {
              roomDetail = null;
            }
          }
        }
        aiAssignments = aiAssignmentsResult.status === "fulfilled"
          && Array.isArray(aiAssignmentsResult.value)
          ? aiAssignmentsResult.value
          : [];
        weeklyTasks = weeklyTasksResult.status === "fulfilled"
          && Array.isArray(weeklyTasksResult.value)
          ? weeklyTasksResult.value
          : [];
        practiceMachines = practiceMachinesResult.status === "fulfilled"
          && Array.isArray(practiceMachinesResult.value)
          ? practiceMachinesResult.value
          : [];
      }

      if (cancelled) return;
      setView({
        loading: false,
        hasError: pathsResult.status === "rejected" && resourcesResult.status === "rejected",
        resources,
        activePath,
        pathDetail,
        roomDetail,
        aiAssignments,
        weeklyTasks,
        practiceMachines,
      });
    }

    loadCourse();
    return () => {
      cancelled = true;
    };
  }, [pathId]);

  const nextRoom = pickInProgress(view.pathDetail?.rooms);
  const roomProgress = toPercent(nextRoom?.progress_percent);
  const deployment = view.roomDetail?.my_deployment;
  const practiceMachines = buildPracticeMachines(
    view.practiceMachines,
    view.resources,
    deployment,
    view.roomDetail?.title,
  );
  const aiAssignments = assignmentsUntilToday(view.aiAssignments);
  const weekById = new Map(
    (view.weeklyTasks ?? []).map((week) => [String(week.id), week]),
  );
  const weeklyAssignmentGroups = [];
  const weeklyAssignmentGroupByKey = new Map();
  (view.weeklyTasks ?? []).forEach((week) => {
    const key = `week-${week.id}`;
    const group = { id: key, week, assignments: [] };
    weeklyAssignmentGroupByKey.set(key, group);
    weeklyAssignmentGroups.push(group);
  });
  aiAssignments.forEach((assignment) => {
    const weekId = assignment.teaching_class_week_id
      ? String(assignment.teaching_class_week_id)
      : null;
    const key = weekId ? `week-${weekId}` : `assignment-${assignment.id}`;
    let group = weeklyAssignmentGroupByKey.get(key);
    if (!group) {
      group = {
        id: key,
        week: weekId ? weekById.get(weekId) : null,
        assignments: [],
      };
      weeklyAssignmentGroupByKey.set(key, group);
      weeklyAssignmentGroups.push(group);
    }
    group.assignments.push(assignment);
  });
  const currentSchedule = view.activePath?.schedule;
  const heroStatusMeta = view.activePath
    ? currentSchedule?.state === "now"
      ? { labelKey: "StudentCoursePage.statusInClass", tone: "success", icon: "sensors" }
      : { labelKey: "StudentCoursePage.statusReadyToStart", tone: "success", icon: "play_circle" }
    : NO_COURSE_STATUS;

  async function openPracticeMachine(machine) {
    if (!machine?.vmid) {
      toast.error(t("StudentCoursePage.machineNotReady"));
      return;
    }
    const toastId = `start-class-machine-${machine.vmid}`;
    setOpeningMachineId(machine.vmid);
    try {
      let resource = await ResourcesService.get(machine.vmid);
      if (resource.status !== "running") {
        toast.info(t("StudentCoursePage.startingMachine"), { id: toastId });
        await ResourcesService.start(resource.vmid);
        resource = await waitForPracticeMachine(resource.vmid);
        if (resource?.status !== "running") {
          toast.info(t("StudentCoursePage.machineStillStarting"), { id: toastId });
          return;
        }
        toast.success(t("StudentCoursePage.machineStarted"), { id: toastId });
      }
      setActivePracticeResource({ ...machine, ...resource });
    } catch (error) {
      toast.error(error?.message ?? t("StudentCoursePage.openMachineFailed"));
    } finally {
      setOpeningMachineId(null);
    }
  }

  function openMachineInformation(machine) {
    if (!machine?.vmid) {
      toast.info(t("StudentCoursePage.machineNotReadyInfo"));
      return;
    }
    navigate(`/my-resources/${machine.vmid}`);
  }

  function toggleAssignment(groupId) {
    setExpandedAssignmentId((current) => (current === groupId ? null : groupId));
  }

  async function updateCompletion(group) {
    if (reportingAssignmentId) return;
    const completed = !group.assignments.every((assignment) => (
      Boolean(assignment.completion?.completed)
    ));
    setReportingAssignmentId(group.id);
    setExpandedAssignmentId(group.id);
    try {
      const results = await Promise.allSettled(group.assignments.map((assignment) => (
        CoursesService.updateAssignmentCompletion(
          view.activePath.id,
          assignment.id,
          completed,
        )
      )));
      const completions = new Map();
      results.forEach((result, index) => {
        if (result.status === "fulfilled") {
          completions.set(group.assignments[index].id, result.value);
        }
      });
      setView((current) => ({
        ...current,
        aiAssignments: current.aiAssignments.map((item) => (
          completions.has(item.id) ? { ...item, completion: completions.get(item.id) } : item
        )),
      }));
      const failedResult = results.find((result) => result.status === "rejected");
      if (failedResult) {
        throw failedResult.reason;
      }
      toast.success(completed
        ? t("StudentCoursePage.weekChecked")
        : t("StudentCoursePage.weekUnchecked"));
    } catch (error) {
      toast.error(error?.message ?? t("StudentCoursePage.completionUpdateFailed"));
    } finally {
      setReportingAssignmentId(null);
    }
  }

  if (view.loading) {
    return (
      <div className={styles.page}>
        <LoadingState text={t("StudentCoursePage.loadingText")} fullPage />
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.coursePageHeader}>
        <button
          type="button"
          className={styles.courseBackButton}
          onClick={() => navigate(location.state?.from ?? "/courses")}
        >
          <MIcon name="arrow_back" size={18} />
          {t("StudentCoursePage.backToCourses")}
        </button>
        <div className={styles.coursePageTitle}>
          <p className={styles.eyebrow}>{t("StudentCoursePage.eyebrowCourseOverview")}</p>
          <h1>{view.activePath?.title ?? t("StudentCoursePage.defaultCourseTitle")}</h1>
          <p>
            {/* UserGuide 導覽入口的 portal slot，比照 PageHeader 副標前的位置 */}
            <span data-user-guide-slot="" />
            {view.activePath?.description ?? t("StudentCoursePage.defaultCourseDesc")}
          </p>
        </div>
      </header>

      {view.hasError && (
        <div className={styles.notice} role="status">
          <MIcon name="cloud_off" size={20} />
          <div>
            <strong>{t("StudentCoursePage.noticeTitle")}</strong>
            <span>{t("StudentCoursePage.noticeDesc")}</span>
          </div>
        </div>
      )}

      <main className={styles.mainGrid}>
        <section
          className={styles.classCard}
          aria-labelledby="today-class-title"
          data-guide="home-current-course"
        >
          <div className={styles.classCardTop}>
            <div>
              <p className={styles.eyebrow}>
                {currentSchedule?.state === "now" ? t("StudentCoursePage.eyebrowNow") : t("StudentCoursePage.eyebrowUpcoming")}
              </p>
              <h2 id="today-class-title">
                {view.activePath?.title ?? t("StudentCoursePage.noCourseAvailable")}
              </h2>
              <p className={styles.classDescription}>
                {nextRoom
                  ? t("StudentCoursePage.classTaskDesc", { title: nextRoom.title })
                  : view.activePath?.description
                    ?? t("StudentCoursePage.classNoContentYet")}
              </p>
            </div>
            <StatusBadge meta={heroStatusMeta} />
          </div>

          {view.activePath ? (
            <>
              <div className={styles.courseContext}>
                {currentSchedule ? (
                  <>
                    <span><MIcon name="schedule" size={18} />{currentSchedule.time}</span>
                    <span><MIcon name="person" size={18} />{currentSchedule.teacher}</span>
                    <span><MIcon name="location_on" size={18} />{currentSchedule.place}</span>
                  </>
                ) : (
                  <span><MIcon name="task_alt" size={18} />{t("StudentCoursePage.taskProgress", { percent: roomProgress })}</span>
                )}
              </div>

              <div
                className={styles.progressTrack}
                aria-label={t("StudentCoursePage.chapterProgressAria", { percent: roomProgress })}
                data-guide="home-progress"
              >
                <span style={{ width: `${roomProgress}%` }} />
              </div>

              <div className={styles.simpleCourseHint}>
                <MIcon name="check_circle" size={18} />
                <span>
                  {deployment?.status === "running" || !nextRoom?.has_lab
                    ? t("StudentCoursePage.hintReady")
                    : t("StudentCoursePage.hintWillPrepare")}
                </span>
              </div>
            </>
          ) : (
            <EmptyState
              icon="event_available"
              title={t("StudentCoursePage.emptyNoCourseTitle")}
              description={t("StudentCoursePage.emptyNoCourseDesc")}
            />
          )}

          {practiceMachines.length > 0 && (
            <section className={styles.machinePicker} aria-label={t("StudentCoursePage.classMachinesAriaLabel")} data-guide="home-start">
              <header>
                <div>
                  <strong>{t("StudentCoursePage.yourClassMachines")}</strong>
                  <span>{t("StudentCoursePage.machineHint")}</span>
                </div>
              </header>
              <div className={styles.machineGrid}>
                {practiceMachines.map((machine) => {
                  const machineName = machine.classMachineName ?? machine.name;
                  const actionLabel = practiceMachineActionLabel(machine, openingMachineId);
                  return (
                    <div
                      key={machine.machine_node_id
                        ?? `${machine.teaching_class_id ?? "course"}-${machine.vmid}`}
                      className={styles.machineOption}
                    >
                      <button
                        type="button"
                        className={styles.machineLaunchButton}
                        onClick={() => openPracticeMachine(machine)}
                        disabled={openingMachineId !== null || machine.vmid == null}
                        aria-label={t("StudentCoursePage.machineLaunchAria", { action: actionLabel, name: machineName })}
                      >
                        <span className={styles.machineIcon}>
                          <MIcon name={machine.type === "lxc" ? "terminal" : "desktop_windows"} size={22} />
                        </span>
                        <span className={styles.machineCopy}>
                          <strong>{machineName}</strong>
                          <small>
                            {machine.classMachineRole ?? t("StudentCoursePage.defaultMachineRole")}
                            {machine.vmid != null ? t("StudentCoursePage.vmidSuffix", { vmid: machine.vmid }) : t("StudentCoursePage.notConfiguredSuffix")}
                          </small>
                        </span>
                        <span className={`${styles.machineState} ${machine.status === "running" ? styles.machineStateReady : ""}`}>
                          {actionLabel}
                        </span>
                        <span className={styles.machineArrow}><MIcon name="arrow_forward" size={20} /></span>
                      </button>
                      <button
                        type="button"
                        className={styles.machineInfoButton}
                        onClick={() => openMachineInformation(machine)}
                        disabled={machine.vmid == null}
                        aria-label={t("StudentCoursePage.viewFullInfoAria", { name: machineName })}
                        title={t("StudentCoursePage.viewFullSettingsTitle")}
                      >
                        <MIcon name="info" size={20} />
                      </button>
                    </div>
                  );
                })}
              </div>
            </section>
          )}
        </section>
      </main>

      <section className={styles.taskSection} aria-labelledby="task-title" data-guide="home-tasks">
        <div className={styles.sectionHeading}>
          <div>
            <h2 id="task-title">{t("StudentCoursePage.tasksTitle")}</h2>
          </div>
          {weeklyAssignmentGroups.length > 0 && (
            <span>{t("StudentCoursePage.weeksSummary", { count: weeklyAssignmentGroups.length })}</span>
          )}
        </div>

        {weeklyAssignmentGroups.length > 0 ? (
          <div className={styles.assignmentList}>
            {weeklyAssignmentGroups.map((group, index) => {
              const firstAssignment = group.assignments[0];
              const expanded = expandedAssignmentId === group.id;
              const completionReported = group.assignments.length > 0 && group.assignments.every((assignment) => (
                Boolean(assignment.completion?.completed)
              ));
              const itemCount = group.assignments.reduce(
                (count, assignment) => count + (assignment.items?.length ?? 0),
                0,
              );
              return (
                <article
                  key={group.id}
                  id={`weekly-task-${group.id}`}
                  className={`${styles.assignmentRow} ${expanded ? styles.assignmentRowOpen : ""}`}
                >
                  <div className={styles.assignmentHeader}>
                    {group.assignments.length > 0 ? <label className={styles.weekCompletion}>
                      <input
                        type="checkbox"
                        checked={completionReported}
                        onChange={() => updateCompletion(group)}
                        disabled={reportingAssignmentId !== null}
                        aria-label={t(completionReported ? "StudentCoursePage.uncheckWeekAria" : "StudentCoursePage.checkWeekAria", { title: group.week?.title ?? firstAssignment?.title })}
                      />
                      {reportingAssignmentId === group.id && <MIcon name="sync" size={15} />}
                    </label> : <span className={styles.weekCompletion} aria-hidden="true" />}
                    <button
                      type="button"
                      className={styles.assignmentToggle}
                      onClick={() => group.week
                        ? navigate(`/courses/${pathId}/weeks/${group.week.id}`)
                        : toggleAssignment(group.id)}
                      aria-expanded={expanded}
                      aria-controls={`assignment-detail-${group.id}`}
                    >
                      <span className={styles.taskNumber}>{group.week?.week_number ?? index + 1}</span>
                      <span className={styles.assignmentTitle}>
                        <strong>{group.week?.title ?? firstAssignment?.title}</strong>
                        <small>
                          {group.week ? t("StudentCoursePage.weekMeta", { week: group.week.week_number, date: group.week.session_date }) : formatAssignmentDate(firstAssignment.approved_at)}
                          {" · "}{t("StudentCoursePage.itemsCount", { count: itemCount })}
                        </small>
                      </span>
                      <span className={`${styles.assignmentStatus} ${completionReported ? styles.assignmentStatus_completed : styles.assignmentStatus_ready}`}>
                        <MIcon name={completionReported ? "check_circle" : "pending_actions"} size={16} />
                        {t(completionReported ? "StudentCoursePage.weekCompleted" : "StudentCoursePage.weekPending")}
                      </span>
                      <MIcon name={group.week ? "arrow_forward" : (expanded ? "expand_less" : "expand_more")} size={21} />
                    </button>
                  </div>

                  {expanded && (
                    <div className={styles.assignmentDetail} id={`assignment-detail-${group.id}`}>
                      {group.assignments.map((assignment) => (
                        <div className={styles.assignmentDetailBlock} key={assignment.id}>
                          <div className={styles.aiBrief}>
                            <span><MIcon name="auto_awesome" size={19} /></span>
                            <div>
                              <strong>{group.assignments.length > 1 ? assignment.title : t("StudentCoursePage.aiSummaryTitle")}</strong>
                              <p>{assignment.summary || t("StudentCoursePage.aiSummaryFallback")}</p>
                            </div>
                          </div>

                          <ol className={styles.aiRequirementList}>
                            {(assignment.items ?? []).map((item, itemIndex) => {
                              const detectableMeta = AI_DETECTABLE_META[item.detectable]
                                ?? AI_DETECTABLE_META.manual;
                              return (
                                <li
                                  className={styles.aiRequirementItem}
                                  key={`${assignment.id}:${item.id}`}
                                >
                                  <span className={styles.aiRequirementNumber}>{itemIndex + 1}</span>
                                  <div className={styles.aiRequirementContent}>
                                    <strong>{item.title}</strong>
                                    {item.description && <p>{item.description}</p>}
                                  </div>
                                  <span className={`${styles.aiCheckBadge} ${styles[detectableMeta.tone]}`}>
                                    <MIcon name={detectableMeta.icon} size={15} />
                                    {t(detectableMeta.labelKey)}
                                  </span>
                                </li>
                              );
                            })}
                          </ol>
                        </div>
                      ))}

                      <p className={styles.assignmentNote}>
                        <MIcon name="info" size={16} />
                        {t("StudentCoursePage.completionNote")}
                      </p>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        ) : (
          <EmptyState
            icon="checklist"
            title={t("StudentCoursePage.emptyNoTasksTitle")}
            description={t("StudentCoursePage.emptyNoTasksDesc")}
          />
        )}
      </section>
      {activePracticeResource?.type === "lxc" && (
        <TerminalDialog
          resource={activePracticeResource}
          onClose={() => setActivePracticeResource(null)}
        />
      )}
      {activePracticeResource && activePracticeResource.type !== "lxc" && (
        <VncDialog
          resource={activePracticeResource}
          onClose={() => setActivePracticeResource(null)}
        />
      )}
    </div>
  );
}
