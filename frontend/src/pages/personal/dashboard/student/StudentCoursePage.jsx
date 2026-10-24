import { useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import MIcon from "../../../../components/MIcon";
import EmptyState from "../../../../components/EmptyState/EmptyState";
import LoadingState from "../../../../components/LoadingState/LoadingState";
import PageHeader from "../../../../components/PageHeader/PageHeader";
import TerminalDialog from "../../resources/TerminalDialog";
import VncDialog from "../../resources/VncDialog";
import { useQueryView } from "../../../../hooks/useQueryLoadState";
import { CoursesService } from "../../../../services/courses";
import { ResourcesService } from "../../../../services/resources";
import {
  courseKeys,
  useAiAssignmentsQuery,
  useCoursePathQuery,
  useCoursePathsQuery,
  useCourseScheduleQuery,
  usePracticeMachinesQuery,
  useWeeklyTasksQuery,
} from "../../../../queries/courses";
import { resourceKeys, useMyResourcesQuery, useResourceCacheUpdater } from "../../../../queries/resources";
import {
  assignmentsUntilToday,
  buildPracticeMachines,
  formatAssignmentDate,
  normalizeSchedule,
  pickCurrentWeek,
  pickInProgress,
  practiceMachineActionLabel,
  waitForPracticeMachine,
} from "./studentDashboard";
import styles from "./StudentCoursePage.module.scss";
import { feedbackFor } from "../../courses/studentWeekFeedback";

/** AI 任務的每個檢查項目可被自動判定的程度。 */
const AI_DETECTABLE_META = {
  auto: { labelKey: "StudentCoursePage.detectableAuto", icon: "support_agent", tone: "auto" },
  partial: { labelKey: "StudentCoursePage.detectablePartial", icon: "rule", tone: "partial" },
  manual: { labelKey: "StudentCoursePage.detectableManual", icon: "how_to_reg", tone: "manual" },
};

const EMPTY = [];
const asArray = (value) => (Array.isArray(value) ? value : EMPTY);

function createGuideDemoCourse(t) {
  const weekId = "demo-week-1";
  return {
    loading: false,
    hasError: false,
    resources: [],
    activePath: {
      id: "demo",
      title: t("StudentCoursePage.guideDemoCourseTitle"),
      description: t("StudentCoursePage.guideDemoCourseDescription"),
      progress_percent: 50,
    },
    pathDetail: {
      rooms: [{
        id: "demo-room",
        title: t("StudentCoursePage.guideDemoWeekTitle"),
        progress_percent: 50,
      }],
    },
    aiAssignments: [{
      id: "demo-assignment",
      teaching_class_week_id: weekId,
      approved_at: "2026-01-01T00:00:00Z",
      title: t("StudentCoursePage.guideDemoAssignmentTitle"),
      summary: t("StudentCoursePage.guideDemoAssignmentSummary"),
      completion: { completed: false },
      items: [
        { id: "demo-check-1", title: t("StudentCoursePage.guideDemoCheck1"), description: t("StudentCoursePage.guideDemoCheck1Desc"), detectable: "auto" },
        { id: "demo-check-2", title: t("StudentCoursePage.guideDemoCheck2"), description: t("StudentCoursePage.guideDemoCheck2Desc"), detectable: "partial" },
      ],
    }],
    weeklyTasks: [{
      id: weekId,
      week_number: 1,
      session_date: "2026-09-09",
      title: t("StudentCoursePage.guideDemoWeekTitle"),
      teacher_reply: t("StudentCoursePage.guideDemoTeacherReply"),
    }],
    practiceMachines: [{
      machine_node_id: "demo-machine",
      node_key: "main",
      name: t("StudentCoursePage.guideDemoMachineName"),
      role: t("StudentCoursePage.guideDemoMachineRole"),
      resource_type: "lxc",
      vmid: 100,
      status: "running",
    }],
  };
}

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
  const isGuideDemo = pathId === "demo";

  const queryClient = useQueryClient();
  const [expandedAssignmentId, setExpandedAssignmentId] = useState(null);
  const [reportingAssignmentId, setReportingAssignmentId] = useState(null);
  const [activePracticeResource, setActivePracticeResource] = useState(null);
  const [openingMachineId, setOpeningMachineId] = useState(null);

  /* 課程清單、課表與我的機器跟首頁／課程列表／我的資源頁共用快取；選定課程後才抓該課程的
     任務、週次與機器（與週任務頁共用同一份，點進週次不必再等）。導覽示範模式不讀課程 API。 */
  const live = !isGuideDemo;
  const pathsQuery = useCoursePathsQuery({ enabled: live });
  const resourcesQuery = useMyResourcesQuery();
  const scheduleQuery = useCourseScheduleQuery({ enabled: live });

  // 網址指定的課程優先；找不到時退回目前進行中的課程。
  // 退回時畫面顯示的是另一門課，後續導覽與寫入一律用 activePath.id，不要用網址的 pathId。
  const catalogPaths = asArray(pathsQuery.data);
  const catalogPath = live
    ? (pathId && catalogPaths.find((path) => String(path.id) === String(pathId))) || pickInProgress(catalogPaths)
    : null;
  const activePathId = catalogPath?.id ?? null;
  const detailEnabled = live && activePathId != null;
  /* 課程詳情這頁沒有用到，照原本一起抓、放進快取給週任務頁（頁首的課程名稱） */
  useCoursePathQuery(activePathId, { enabled: detailEnabled });
  const aiAssignmentsQuery = useAiAssignmentsQuery(activePathId, { enabled: detailEnabled });
  const weeklyTasksQuery = useWeeklyTasksQuery(activePathId, { enabled: detailEnabled });
  const practiceMachinesQuery = usePracticeMachinesQuery(activePathId, { enabled: detailEnabled });
  const resourceCache = useResourceCacheUpdater();
  /* 首次載入還沒有資料才算載入中／載入失敗；背景刷新失敗沿用上一份。
     首載失敗後背景輪詢重試期間維持失敗提示，不閃回整頁載入中（見 useQueryView） */
  const pathsView = useQueryView(pathsQuery);
  const resourcesView = useQueryView(resourcesQuery);
  const scheduleView = useQueryView(scheduleQuery);
  const aiAssignmentsView = useQueryView(aiAssignmentsQuery);
  const weeklyTasksView = useQueryView(weeklyTasksQuery);
  const practiceMachinesView = useQueryView(practiceMachinesQuery);

  let view;
  if (isGuideDemo) {
    view = createGuideDemoCourse(t);
  } else {
    const scheduleRow = asArray(scheduleQuery.data).find((row) => String(row.id) === String(activePathId));
    view = {
      loading: pathsView.loading || resourcesView.loading || scheduleView.loading
        || (detailEnabled && (
          aiAssignmentsView.loading || weeklyTasksView.loading || practiceMachinesView.loading
        )),
      // 任一來源失敗都要提示；只有課程清單失敗時若不提示，會被誤認成「目前沒有課程」
      hasError: Boolean(pathsView.error || resourcesView.error),
      resources: asArray(resourcesQuery.data?.resources),
      activePath: catalogPath && scheduleRow
        ? { ...catalogPath, schedule: normalizeSchedule(scheduleRow).schedule }
        : catalogPath,
      aiAssignments: asArray(aiAssignmentsQuery.data),
      weeklyTasks: asArray(weeklyTasksQuery.data),
      practiceMachines: asArray(practiceMachinesQuery.data),
    };
  }

  const practiceMachines = buildPracticeMachines(
    view.practiceMachines,
    view.resources,
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
  // 本週進度：這週的打勾狀態、檢查通過幾項、老師回覆
  const currentWeek = pickCurrentWeek(view.weeklyTasks);
  const currentWeekGroup = currentWeek
    ? weeklyAssignmentGroupByKey.get(`week-${currentWeek.id}`) ?? null
    : null;
  const currentWeekDone = Boolean(currentWeekGroup?.assignments.length)
    && currentWeekGroup.assignments.every((assignment) => Boolean(assignment.completion?.completed));
  const currentCheckpoints = currentWeek?.checkpoints ?? [];
  const currentPassed = currentCheckpoints
    .filter((checkpoint) => feedbackFor(checkpoint).status === "passed").length;
  const currentReply = String(currentWeek?.teacher_reply ?? "").trim();
  // 這週沒有要打勾的作業（例如只放教材）就不顯示完成狀態
  const heroStatusMeta = !currentWeekGroup?.assignments.length
    ? null
    : currentWeekDone
      ? { labelKey: "StudentCoursePage.weekCompleted", tone: "success", icon: "check_circle" }
      : { labelKey: "StudentCoursePage.weekPending", tone: "muted", icon: "pending_actions" };

  async function openPracticeMachine(machine) {
    if (isGuideDemo) return;
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
        /* 已在開機中就不重送開機，只等它開完 */
        if (resource.status !== "starting") await ResourcesService.start(resource.vmid);
        resource = await waitForPracticeMachine(resource.vmid);
        if (resource?.status !== "running") {
          toast.info(t("StudentCoursePage.machineStillStarting"), { id: toastId });
          return;
        }
        toast.success(t("StudentCoursePage.machineStarted"), { id: toastId });
      }
      /* 開好的狀態寫回共用的機器清單快取，以快取當下那一列為底合併（開機可能等了快一分半） */
      const current = (queryClient.getQueryData(resourceKeys.mine)?.resources ?? [])
        .find((item) => Number(item.vmid) === Number(resource.vmid));
      if (current) resourceCache.update({ ...current, ...resource, vmid: current.vmid });
      setActivePracticeResource({ ...machine, ...resource });
    } catch (error) {
      toast.error(error?.message ?? t("StudentCoursePage.openMachineFailed"));
    } finally {
      setOpeningMachineId(null);
    }
  }

  function openMachineInformation(machine) {
    if (isGuideDemo) return;
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
    const coursePathId = view.activePath.id;
    try {
      const results = await Promise.allSettled(group.assignments.map((assignment) => (
        CoursesService.updateAssignmentCompletion(
          coursePathId,
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
      /* 就地改任務快取的完成狀態；先取消還在路上的重抓，免得送出前的舊快照蓋回來 */
      const assignmentsKey = courseKeys.aiAssignments(coursePathId);
      await queryClient.cancelQueries({ queryKey: assignmentsKey });
      queryClient.setQueryData(assignmentsKey, (current) => (Array.isArray(current)
        ? current.map((item) => (
          completions.has(item.id) ? { ...item, completion: completions.get(item.id) } : item
        ))
        : current));
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
      <PageHeader title={view.activePath?.title ?? t("StudentCoursePage.defaultCourseTitle")}>
        <button
          type="button"
          className={styles.backBtn}
          onClick={() => navigate(location.state?.from ?? "/courses")}
        >
          <MIcon name="arrow_back" size={18} />
          {t("StudentCoursePage.backToCourses")}
        </button>
      </PageHeader>

      {isGuideDemo && (
        <div className={styles.guideDemoNotice}>
          <MIcon name="visibility" size={17} />
          <span><strong>{t("StudentCoursePage.guideDemoNoticeTitle")}</strong>{t("StudentCoursePage.guideDemoNoticeDescription")}</span>
        </div>
      )}

      {view.hasError && (
        <div className={styles.notice} role="status">
          <MIcon name="cloud_off" size={20} />
          <div>
            <strong>{t("StudentCoursePage.noticeTitle")}</strong>
            <span>{t("StudentCoursePage.noticeDesc")}</span>
          </div>
        </div>
      )}

      {/* 版面同週任務頁：左欄任務，右欄課堂狀態與機器；窄螢幕右欄排到前面 */}
      <div className={styles.layout}>
        <section className={`${styles.panel} ${styles.tasksPanel}`} aria-labelledby="task-title" data-guide="home-tasks">
          <div className={styles.panelHeading}>
            <span><MIcon name="checklist" size={20} /></span>
            <div>
              <h2 id="task-title">{t("StudentCoursePage.tasksTitle")}</h2>
              {weeklyAssignmentGroups.length > 0 && (
                <p>{t("StudentCoursePage.weeksSummary", { count: weeklyAssignmentGroups.length })}</p>
              )}
            </div>
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
                const hasTeacherReply = Boolean(String(group.week?.teacher_reply ?? "").trim());
                const weekTitle = group.week?.title ?? firstAssignment?.title;
                return (
                  <article
                    key={group.id}
                    id={`weekly-task-${group.id}`}
                    className={`${styles.assignmentRow} ${expanded ? styles.assignmentRowOpen : ""}`}
                  >
                    <div className={styles.assignmentHeader}>
                      <button
                        type="button"
                        className={styles.assignmentToggle}
                        onClick={() => group.week
                          ? navigate(`/courses/${view.activePath?.id ?? pathId}/weeks/${group.week.id}`)
                          : toggleAssignment(group.id)}
                        aria-expanded={expanded}
                        aria-controls={`assignment-detail-${group.id}`}
                        data-guide={group.week ? "course-week-open" : undefined}
                      >
                        <span className={styles.taskNumber}>{group.week?.week_number ?? index + 1}</span>
                        <span className={styles.assignmentTitle}>
                          <strong>{weekTitle}</strong>
                          <small>
                            {group.week ? t("StudentCoursePage.weekMeta", { week: group.week.week_number, date: group.week.session_date }) : formatAssignmentDate(firstAssignment.approved_at)}
                            {" · "}{t("StudentCoursePage.itemsCount", { count: itemCount })}
                          </small>
                        </span>
                        {hasTeacherReply ? (
                          <span className={styles.teacherReplyBadge}>
                            <MIcon name="forum" size={15} />
                            {t("StudentCoursePage.teacherReplied")}
                          </span>
                        ) : <span aria-hidden="true" />}
                        {/* 打勾框疊在這一格上（按鈕裡不能放 input），箭頭固定在最右邊 */}
                        <span className={styles.weekCompletionSlot} aria-hidden="true" />
                        <MIcon name={group.week ? "arrow_forward" : (expanded ? "expand_less" : "expand_more")} size={21} />
                      </button>
                      {/* 一個勾＝這週全部做完；只記錄完成狀態，不會啟動檢查 */}
                      {group.assignments.length > 0 && (
                        <label
                          className={styles.weekCompletion}
                          title={t(completionReported ? "StudentCoursePage.weekCompleted" : "StudentCoursePage.markWeekDone")}
                        >
                          <input
                            type="checkbox"
                            checked={completionReported}
                            onChange={() => updateCompletion(group)}
                            disabled={isGuideDemo || reportingAssignmentId !== null}
                            aria-busy={reportingAssignmentId === group.id || undefined}
                            aria-label={t(completionReported ? "StudentCoursePage.uncheckWeekAria" : "StudentCoursePage.checkWeekAria", { title: weekTitle })}
                          />
                        </label>
                      )}
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

        <aside className={styles.side}>
          <section className={styles.panel} aria-labelledby="today-class-title" data-guide="home-current-course">
            <div className={styles.panelHeading}>
              <span><MIcon name="event_note" size={20} /></span>
              <div>
                <h2 id="today-class-title">{t("StudentCoursePage.thisWeekTitle")}</h2>
                <p>
                  {currentWeek
                    ? t("StudentCoursePage.thisWeekDesc", { week: currentWeek.week_number, title: currentWeek.title })
                    : t("StudentCoursePage.thisWeekNone")}
                </p>
              </div>
              {heroStatusMeta && <StatusBadge meta={heroStatusMeta} />}
            </div>

            {currentWeek ? (
              <>
                {currentSchedule && (
                  <div className={styles.courseContext}>
                    <span><MIcon name="schedule" size={18} />{currentSchedule.time}</span>
                    {currentSchedule.teacher && <span><MIcon name="person" size={18} />{currentSchedule.teacher}</span>}
                    {currentSchedule.place && <span><MIcon name="location_on" size={18} />{currentSchedule.place}</span>}
                  </div>
                )}

                <div className={styles.progress} data-guide="home-progress">
                  <span className={styles.progressLabel}>
                    {currentCheckpoints.length
                      ? t("StudentCoursePage.thisWeekChecks", { passed: currentPassed, total: currentCheckpoints.length })
                      : t("StudentCoursePage.thisWeekNoChecks")}
                  </span>
                </div>

                {currentReply && (
                  <p className={styles.weekReply}>
                    <MIcon name="forum" size={16} />
                    <span><strong>{t("StudentCoursePage.teacherReplied")}</strong>{currentReply}</span>
                  </p>
                )}

                <button
                  type="button"
                  className={styles.weekOpenBtn}
                  onClick={() => navigate(`/courses/${view.activePath?.id ?? pathId}/weeks/${currentWeek.id}`)}
                >
                  {t("StudentCoursePage.openThisWeek")}
                  <MIcon name="arrow_forward" size={17} />
                </button>
              </>
            ) : (
              <EmptyState
                icon="event_available"
                title={t("StudentCoursePage.thisWeekNone")}
                description={t("StudentCoursePage.thisWeekNoneDesc")}
              />
            )}
          </section>

          {practiceMachines.length > 0 && (
            <section className={styles.panel} aria-labelledby="class-machines-title" data-guide="home-start">
              <div className={styles.panelHeading}>
                <span><MIcon name="dns" size={20} /></span>
                <div>
                  <h2 id="class-machines-title">{t("StudentCoursePage.yourClassMachines")}</h2>
                  <p>{t("StudentCoursePage.machineHint")}</p>
                </div>
              </div>
              <div className={styles.machineList}>
                {practiceMachines.map((machine) => {
                  const machineName = machine.classMachineName ?? machine.name;
                  const actionLabel = practiceMachineActionLabel(machine, openingMachineId);
                  return (
                    <div
                      key={machine.machine_node_id
                        ?? `${machine.teaching_class_id ?? "course"}-${machine.vmid}`}
                      className={styles.machineRow}
                    >
                      <button
                        type="button"
                        className={styles.machineLaunch}
                        onClick={() => openPracticeMachine(machine)}
                        disabled={isGuideDemo || openingMachineId !== null || machine.vmid == null}
                        aria-label={t("StudentCoursePage.machineLaunchAria", { action: actionLabel, name: machineName })}
                      >
                        <span className={styles.machineIcon}>
                          <MIcon name={machine.type === "lxc" ? "terminal" : "desktop_windows"} size={20} />
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
                          <MIcon name="arrow_forward" size={16} />
                        </span>
                      </button>
                      <button
                        type="button"
                        className={styles.iconBtn}
                        onClick={() => openMachineInformation(machine)}
                        disabled={isGuideDemo || machine.vmid == null}
                        aria-label={t("StudentCoursePage.viewFullInfoAria", { name: machineName })}
                        title={t("StudentCoursePage.viewFullSettingsTitle")}
                      >
                        <MIcon name="info" size={18} />
                      </button>
                    </div>
                  );
                })}
              </div>
            </section>
          )}
        </aside>
      </div>
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
