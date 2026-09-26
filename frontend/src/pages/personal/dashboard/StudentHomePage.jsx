import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import TerminalDialog from "../resources/TerminalDialog";
import VncDialog from "../resources/VncDialog";
import { CoursesService } from "../../../services/courses";
import { ResourcesService } from "../../../services/resources";
import { QuickPracticeService } from "../../../services/quickPractice";
import styles from "./StudentHomePage.module.scss";
import HomeOverview from "./HomeOverview";
import LoadingState from "../../../components/LoadingState/LoadingState";
import i18n from "../../../i18n";

// 與 react-i18next 的 t 同型（key, options），normalizeSchedule 會帶插值參數
const defaultT = (key, options = {}) => i18n.t(key, { ns: "personal", ...options });

const STATUS_META = {
  running: { labelKey: "StudentHomePage.statusRunning", tone: "success", icon: "check_circle" },
  provisioning: { labelKey: "StudentHomePage.statusProvisioning", tone: "info", icon: "hourglass_top" },
  failed: { labelKey: "StudentHomePage.statusFailed", tone: "danger", icon: "error" },
  expired: { labelKey: "StudentHomePage.statusExpired", tone: "muted", icon: "schedule" },
  stopped: { labelKey: "StudentHomePage.statusStopped", tone: "muted", icon: "power_settings_new" },
  no_lab: { labelKey: "StudentHomePage.statusNoLab", tone: "success", icon: "menu_book" },
  not_started: { labelKey: "StudentHomePage.statusNotStarted", tone: "warning", icon: "play_circle" },
  empty: { labelKey: "StudentHomePage.statusEmpty", tone: "muted", icon: "event_busy" },
};

const AI_DETECTABLE_META = {
  auto: { labelKey: "StudentHomePage.detectableAuto", icon: "support_agent", tone: "auto" },
  partial: { labelKey: "StudentHomePage.detectablePartial", icon: "rule", tone: "partial" },
  manual: { labelKey: "StudentHomePage.detectableManual", icon: "how_to_reg", tone: "manual" },
};

const AI_CHECK_STATUS_META = {
  pending: { labelKey: "StudentHomePage.checkPending", icon: "hourglass_top", tone: "pending" },
  running: { labelKey: "StudentHomePage.checkRunning", icon: "sync", tone: "running" },
  completed: { labelKey: "StudentHomePage.checkCompleted", icon: "task_alt", tone: "completed" },
  failed: { labelKey: "StudentHomePage.checkFailed", icon: "error_outline", tone: "failed" },
  cancelled: { labelKey: "StudentHomePage.checkCancelled", icon: "block", tone: "cancelled" },
};

export function assignmentsUntilToday(assignments, now = new Date()) {
  const dateKey = (value) => {
    const parts = new Intl.DateTimeFormat("en", {
      timeZone: "Asia/Taipei",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(value);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
  };
  const todayKey = dateKey(now);
  return [...(assignments ?? [])]
    .filter((assignment) => {
      if (!assignment?.approved_at) return true;
      const approvedAt = new Date(assignment.approved_at);
      return !Number.isNaN(approvedAt.getTime()) && dateKey(approvedAt) <= todayKey;
    })
    .sort((left, right) => {
      const leftTime = left.approved_at ? new Date(left.approved_at).getTime() : 0;
      const rightTime = right.approved_at ? new Date(right.approved_at).getTime() : 0;
      return leftTime - rightTime;
    });
}

function formatAssignmentDate(value, t = defaultT) {
  if (!value) return t("StudentHomePage.published");
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return t("StudentHomePage.published");
  return new Intl.DateTimeFormat(i18n.language, {
    month: "numeric",
    day: "numeric",
  }).format(date);
}

function toPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, Math.round(number)));
}

function chooseCurrentPath(paths) {
  return (
    paths.find((path) => toPercent(path.progress_percent) > 0 && toPercent(path.progress_percent) < 100)
    ?? paths.find((path) => toPercent(path.progress_percent) < 100)
    ?? paths[0]
    ?? null
  );
}

function chooseNextRoom(rooms) {
  return (
    rooms.find((room) => toPercent(room.progress_percent) > 0 && toPercent(room.progress_percent) < 100)
    ?? rooms.find((room) => toPercent(room.progress_percent) < 100)
    ?? rooms[0]
    ?? null
  );
}

export function buildPracticeMachines(classMachines, resources) {
  const machines = (classMachines ?? []).map((machine) => {
    const resource = (resources ?? []).find(
      (item) => machine.vmid != null && Number(item.vmid) === Number(machine.vmid),
    );
    return {
      ...machine,
      ...resource,
      classMachineName: machine.name,
      classMachineRole: machine.role,
      type: resource?.type ?? machine.resource_type,
      name: resource?.name ?? machine.name,
    };
  });

  return machines;
}

export function practiceMachineActionLabel(machine, openingMachineId = null, t = defaultT) {
  if (machine?.vmid == null) return t("StudentHomePage.actionConfiguring");
  if (openingMachineId === machine.vmid || machine.status === "starting") return t("StudentHomePage.actionStarting");
  if (machine.status === "running") return t("StudentHomePage.actionEnter");
  return t("StudentHomePage.actionStartAndEnter");
}

/* 後端在開機 task（qmstart）跑完前回報 starting；GPU 直通機要鎖整段記憶體，
   開機可達 40 秒以上，所以最多等約 90 秒，確定 running 才開主控台。 */
async function waitForPracticeMachine(vmid, attempts = 45) {
  let resource = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    resource = await ResourcesService.get(vmid);
    if (resource.status === "running") return resource;
    if (attempt < attempts - 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
    }
  }
  return resource;
}

function StatusBadge({ meta }) {
  const { t } = useTranslation("personal");
  return (
    <span className={`${styles.statusBadge} ${styles[meta.tone]}`}>
      <MIcon name={meta.icon} size={16} />
      {meta.label ?? t(meta.labelKey)}
    </span>
  );
}

function formatScheduleTime(value) {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return new Intl.DateTimeFormat(i18n.language, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function normalizeSchedule(row, t = defaultT) {
  const sessionDate = row.session_date ? new Date(`${row.session_date}T00:00:00`) : null;
  const sessionLabel = sessionDate && !Number.isNaN(sessionDate.getTime())
    ? new Intl.DateTimeFormat(i18n.language, { month: "numeric", day: "numeric", weekday: "short" }).format(sessionDate)
    : "";
  return {
    ...row,
    schedule: {
      state: row.state,
      label: row.label,
      time: `${row.state === "available" && sessionLabel ? t("StudentHomePage.nextSession", { session: sessionLabel }) : ""}${formatScheduleTime(row.start_at)}–${formatScheduleTime(row.end_at)}`,
      teacher: row.teacher,
      place: row.location,
    },
  };
}

export default function StudentHomePage({ courseView = false }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { pathId } = useParams();
  const { t } = useTranslation("personal");
  const [view, setView] = useState({
    loading: true,
    hasError: false,
    paths: [],
    resources: [],
    activePath: null,
    pathDetail: null,
    roomDetail: null,
    aiAssignments: [],
    weeklyTasks: [],
    practiceMachines: [],
  });
  const [quickTemplates, setQuickTemplates] = useState([]);
  const [templatesError, setTemplatesError] = useState(false);
  const [templatesLoading, setTemplatesLoading] = useState(!courseView);
  const [expandedAssignmentId, setExpandedAssignmentId] = useState(null);
  const [expandedWeeklyTaskId, setExpandedWeeklyTaskId] = useState(null);
  const [assignmentChecks, setAssignmentChecks] = useState({});
  const [checkpointChecks, setCheckpointChecks] = useState({});
  const [checkingAssignmentId, setCheckingAssignmentId] = useState(null);
  const [checkingCheckpointKey, setCheckingCheckpointKey] = useState(null);
  const [activePracticeResource, setActivePracticeResource] = useState(null);
  const [openingMachineId, setOpeningMachineId] = useState(null);
  const [documentPreview, setDocumentPreview] = useState(null);
  const [openingDocumentId, setOpeningDocumentId] = useState(null);

  const todayLabel = useMemo(
    () => new Intl.DateTimeFormat(i18n.language, {
      month: "long",
      day: "numeric",
      weekday: "long",
    }).format(new Date()),
    [],
  );

  useEffect(() => {
    let cancelled = false;

    async function loadStudentHome() {
      const [pathsResult, resourcesResult, scheduleResult] = await Promise.allSettled([
        CoursesService.listPaths(),
        ResourcesService.list(),
        CoursesService.listSchedule(),
      ]);

      if (cancelled) return;

      const catalogPaths = pathsResult.status === "fulfilled" && Array.isArray(pathsResult.value)
        ? pathsResult.value
        : [];
      const schedulePaths = scheduleResult.status === "fulfilled" && Array.isArray(scheduleResult.value)
        ? scheduleResult.value.map((row) => normalizeSchedule(row))
        : [];
      const paths = courseView ? catalogPaths : schedulePaths;
      const resources = resourcesResult.status === "fulfilled" && Array.isArray(resourcesResult.value)
        ? resourcesResult.value
        : [];
      let activePath = courseView && pathId
        ? catalogPaths.find((path) => String(path.id) === String(pathId)) ?? chooseCurrentPath(catalogPaths)
        : chooseCurrentPath(paths);
      const scheduledVersion = schedulePaths.find((path) => String(path.id) === String(activePath?.id));
      if (activePath && scheduledVersion) activePath = { ...activePath, schedule: scheduledVersion.schedule };
      let pathDetail = null;
      let roomDetail = null;
      let aiAssignments = [];
      let weeklyTasks = [];
      let practiceMachines = [];

      if (activePath && courseView) {
        const [pathDetailResult, aiAssignmentsResult, weeklyTasksResult, practiceMachinesResult] = await Promise.allSettled([
          CoursesService.getPath(activePath.id),
          CoursesService.getAiAssignments(activePath.id),
          CoursesService.getWeeklyTasks(activePath.id),
          CoursesService.getPracticeMachines(activePath.id),
        ]);
        if (pathDetailResult.status === "fulfilled") {
          pathDetail = pathDetailResult.value;
          const nextRoom = chooseNextRoom(pathDetail?.rooms ?? []);
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

      if (!cancelled) {
        setView({
          loading: false,
          hasError: courseView
            ? pathsResult.status === "rejected" && resourcesResult.status === "rejected"
            : scheduleResult.status === "rejected",
          resourcesError: resourcesResult.status === "rejected",
          paths,
          resources,
          activePath,
          pathDetail,
          roomDetail,
          aiAssignments,
          weeklyTasks,
          practiceMachines,
        });
      }
    }

    loadStudentHome();
    return () => {
      cancelled = true;
    };
  }, [courseView, pathId]);

  useEffect(() => {
    if (courseView) return undefined;
    const controller = new AbortController();
    setTemplatesLoading(true);
    setTemplatesError(false);
    QuickPracticeService.listTemplates({ signal: controller.signal })
      .then((available) => setQuickTemplates(available))
      .catch((error) => {
        if (!error?.cancelled) { setQuickTemplates([]); setTemplatesError(true); }
      })
      .finally(() => {
        if (!controller.signal.aborted) setTemplatesLoading(false);
      });
    return () => controller.abort();
  }, [courseView]);

  useEffect(() => {
    if (!courseView || !view.activePath?.id) return undefined;
    const activeChecks = assignmentsUntilToday(view.aiAssignments)
      .map((assignment) => [
        String(assignment.id),
        assignmentChecks[assignment.id] ?? assignment.latest_check,
      ])
      .filter(([, check]) => check?.status === "pending" || check?.status === "running");
    if (activeChecks.length === 0) return undefined;

    let cancelled = false;
    const timer = window.setTimeout(async () => {
      const updates = await Promise.all(activeChecks.map(async ([assignmentId, check]) => {
        try {
          const nextCheck = await CoursesService.getAiCheck(
            view.activePath.id,
            assignmentId,
            check.run_id,
          );
          return [assignmentId, nextCheck];
        } catch {
          return null;
        }
      }));
      if (cancelled) return;
      setAssignmentChecks((current) => {
        const next = { ...current };
        updates.filter(Boolean).forEach(([assignmentId, check]) => {
          next[assignmentId] = check;
        });
        return next;
      });
    }, 2500);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [assignmentChecks, courseView, view.activePath?.id, view.aiAssignments]);

  useEffect(() => {
    if (!courseView || !view.activePath?.id) return undefined;
    const activeChecks = Object.entries(checkpointChecks)
      .filter(([, entry]) => entry.check?.status === "pending" || entry.check?.status === "running");
    if (activeChecks.length === 0) return undefined;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      const updates = await Promise.all(activeChecks.map(async ([key, entry]) => {
        try {
          const check = await CoursesService.getAiCheck(
            view.activePath.id,
            entry.assignmentId,
            entry.check.run_id,
          );
          return [key, { ...entry, check }];
        } catch {
          return null;
        }
      }));
      if (cancelled) return;
      setCheckpointChecks((current) => {
        const next = { ...current };
        updates.filter(Boolean).forEach(([key, entry]) => { next[key] = entry; });
        return next;
      });
    }, 2500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [checkpointChecks, courseView, view.activePath?.id]);

  useEffect(() => () => {
    if (documentPreview?.url) window.URL.revokeObjectURL(documentPreview.url);
  }, [documentPreview?.url]);

  useEffect(() => {
    if (!documentPreview) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setDocumentPreview(null);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [documentPreview]);

  const nextRoom = chooseNextRoom(view.pathDetail?.rooms ?? []);
  const roomProgress = toPercent(nextRoom?.progress_percent);
  const practiceMachines = buildPracticeMachines(
    view.practiceMachines,
    view.resources,
  );
  const aiAssignments = assignmentsUntilToday(view.aiAssignments);
  const weeklyAssignmentIds = new Set(
    view.weeklyTasks.flatMap((task) => (task.checkpoints ?? [])
      .map((checkpoint) => checkpoint.assignment_id ? String(checkpoint.assignment_id) : null)
      .filter(Boolean)),
  );
  const standaloneAiAssignments = aiAssignments.filter(
    (assignment) => !weeklyAssignmentIds.has(String(assignment.id)),
  );
  const weeklyCheckpointCount = view.weeklyTasks.reduce(
    (count, task) => count + (task.checkpoints?.length ?? 0),
    0,
  );
  const aiRequirementCount = aiAssignments.reduce(
    (count, assignment) => count + (assignment.items?.length ?? 0),
    0,
  );
  const primaryLabel = nextRoom ? t("StudentHomePage.startPractice") : t("StudentHomePage.viewAvailableCourses");
  const currentSchedule = view.activePath?.schedule;
  const heroStatusMeta = view.activePath
    ? currentSchedule?.state === "now"
      ? { label: t("StudentHomePage.statusLive"), tone: "success", icon: "sensors" }
      : { label: t("StudentHomePage.statusReady"), tone: "success", icon: "play_circle" }
    : STATUS_META.empty;

  const openPracticeMachine = async (machine) => {
    if (!machine?.vmid) {
      toast.error(t("StudentHomePage.machineNotReady"));
      return;
    }
    setOpeningMachineId(machine.vmid);
    let resource = machine;
    try {
      resource = await ResourcesService.get(resource.vmid);
      if (resource.status !== "running") {
        toast.info(t("StudentHomePage.machineStarting"), {
          id: `start-class-machine-${machine.vmid}`,
        });
        /* 已在開機中就不重送開機，只等它開完 */
        if (resource.status !== "starting") await ResourcesService.start(resource.vmid);
        resource = await waitForPracticeMachine(resource.vmid);
        if (resource?.status !== "running") {
          toast.info(t("StudentHomePage.machineStillStarting"), {
            id: `start-class-machine-${machine.vmid}`,
          });
          return;
        }
        toast.success(t("StudentHomePage.machineStarted"), {
          id: `start-class-machine-${machine.vmid}`,
        });
      }
      setView((current) => ({ ...current, resources: current.resources.map((item) =>
        Number(item.vmid) === Number(resource.vmid) ? { ...item, ...resource } : item) }));
      setActivePracticeResource({ ...machine, ...resource });
    } catch (error) {
      toast.error(error?.message ?? t("StudentHomePage.machineOpenFailed"));
    } finally {
      setOpeningMachineId(null);
    }
  };

  const openMachineInformation = (machine) => {
    if (!machine?.vmid) {
      toast.info(t("StudentHomePage.machineNotReadyPeriod"));
      return;
    }
    navigate(`/my-resources/${machine.vmid}`);
  };

  const openCourseOverview = (path = view.activePath) => {
    if (!path) {
      navigate("/courses");
      return;
    }
    navigate(`/courses/${path.id}`, { state: { from: "/dashboard" } });
  };

  const toggleAssignment = (assignmentId) => {
    setExpandedAssignmentId((current) => current === assignmentId ? null : assignmentId);
  };

  const openAssignmentDocument = async (assignment) => {
    if (!view.activePath?.id || !assignment?.source_document) return;
    setOpeningDocumentId(assignment.id);
    try {
      const blob = await CoursesService.getAiAssignmentDocument(
        view.activePath.id,
        assignment.id,
      );
      const url = window.URL.createObjectURL(blob);
      setDocumentPreview({
        url,
        filename: assignment.source_document.filename,
        displayName: assignment.source_document.display_name,
      });
    } catch (error) {
      toast.error(error?.message ?? t("StudentHomePage.pdfOpenFailed"));
    } finally {
      setOpeningDocumentId(null);
    }
  };

  const openWeeklyTaskDocument = async (task, file) => {
    if (!view.activePath?.id || !task?.id || !file?.id) return;
    setOpeningDocumentId(file.id);
    try {
      const blob = await CoursesService.getWeeklyTaskDocument(
        view.activePath.id,
        task.id,
        file.id,
      );
      const url = window.URL.createObjectURL(blob);
      setDocumentPreview({ url, filename: file.filename, displayName: file.filename });
    } catch (error) {
      toast.error(error?.message ?? t("StudentHomePage.pdfOpenFailed"));
    } finally {
      setOpeningDocumentId(null);
    }
  };

  const submitAiCheck = async (assignment) => {
    if (checkingAssignmentId) return;
    setCheckingAssignmentId(assignment.id);
    setExpandedAssignmentId(assignment.id);
    try {
      const check = await CoursesService.startAiCheck(
        view.activePath.id,
        assignment.id,
      );
      setAssignmentChecks((current) => ({ ...current, [assignment.id]: check }));
      toast.success(check.status === "completed" ? t("StudentHomePage.aiCheckCompleted") : t("StudentHomePage.aiCheckSubmitted"));
    } catch (error) {
      toast.error(error?.message ?? t("StudentHomePage.aiCheckSubmitFailed"));
    } finally {
      setCheckingAssignmentId(null);
    }
  };

  const submitCheckpointCheck = async (checkpoint) => {
    if (!view.activePath?.id || checkingCheckpointKey || !checkpoint.assignment_id) return;
    const key = `${checkpoint.task_id}:${checkpoint.id}`;
    setCheckingCheckpointKey(key);
    try {
      const check = await CoursesService.startAiCheck(
        view.activePath.id,
        checkpoint.assignment_id,
        checkpoint.id,
      );
      setCheckpointChecks((current) => ({
        ...current,
        [key]: { assignmentId: checkpoint.assignment_id, itemId: checkpoint.id, check },
      }));
      toast.success(check.status === "completed" ? t("StudentHomePage.checkpointCompleted") : t("StudentHomePage.checkpointSubmitted"));
    } catch (error) {
      toast.error(error?.message ?? t("StudentHomePage.checkpointCheckFailed"));
    } finally {
      setCheckingCheckpointKey(null);
    }
  };

  if (view.loading) {
    return (
      <div className={styles.page}>
        <LoadingState fullPage text={t("StudentHomePage.loadingLabel")} />
      </div>
    );
  }

  return (
    <div className={styles.page}>
      {courseView && (
        <header className={styles.coursePageHeader}>
          <button
            type="button"
            className={styles.courseBackButton}
            onClick={() => navigate(location.state?.from ?? "/dashboard")}
          >
            <MIcon name="arrow_back" size={18} />
            {t("StudentHomePage.back")}
          </button>
          <div className={styles.coursePageTitle}>
            <p className={styles.eyebrow}>{t("StudentHomePage.courseOverview")}</p>
            <h1>{view.activePath?.title ?? t("StudentHomePage.courseFallback")}</h1>
            <p>{view.activePath?.description ?? t("StudentHomePage.courseDescriptionFallback")}</p>
          </div>
        </header>
      )}

      {courseView && view.hasError && (
        <div className={styles.notice} role="status">
          <MIcon name="cloud_off" size={20} />
          <div>
            <strong>{t("StudentHomePage.errorTitle")}</strong>
            <span>{t("StudentHomePage.errorDesc")}</span>
          </div>
        </div>
      )}

      {!courseView && <HomeOverview paths={view.paths} resources={view.resources}
        resourcesError={view.resourcesError} coursesError={view.hasError}
        templates={quickTemplates} templatesLoading={templatesLoading} templatesError={templatesError}
        openingMachineId={openingMachineId} onOpenMachine={openPracticeMachine} todayLabel={todayLabel} />}

      {courseView && (
        <>
      <main className={styles.mainGrid}>
        <section className={styles.classCard} aria-labelledby="today-class-title" data-student-tour="class" data-guide="home-current-course">
          <div className={styles.classCardTop}>
            <div>
              <p className={styles.eyebrow}>{currentSchedule?.state === "now" ? t("StudentHomePage.inProgressNow") : t("StudentHomePage.upNextForPractice")}</p>
              <h2 id="today-class-title">
                {view.activePath?.title ?? t("StudentHomePage.noCourseToStart")}
              </h2>
              <p className={styles.classDescription}>
                {nextRoom
                  ? t("StudentHomePage.thisLessonTask", { title: nextRoom.title })
                  : view.activePath?.description
                    ?? t("StudentHomePage.waitingForTeacherContent")}
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
                  <span><MIcon name="task_alt" size={18} />{t("StudentHomePage.taskProgress", { percent: roomProgress })}</span>
                )}
              </div>

              <div className={styles.progressTrack} aria-label={t("StudentHomePage.chapterProgressAria", { percent: roomProgress })} data-guide="home-progress">
                <span style={{ width: `${roomProgress}%` }} />
              </div>

              <div className={styles.simpleCourseHint}>
                <MIcon name="check_circle" size={18} />
                <span>
                  {t("StudentHomePage.practiceReady")}
                </span>
              </div>
            </>
          ) : (
            <div className={styles.emptyClass}>
              <MIcon name="event_available" size={28} />
              <div>
                <strong>{t("StudentHomePage.noPendingCourseTitle")}</strong>
                <p>{t("StudentHomePage.noPendingCourseDesc")}</p>
              </div>
            </div>
          )}

          {practiceMachines.length === 0 && (
            <div className={styles.primaryActions}>
              <button type="button" className={styles.primaryButton} onClick={() => openCourseOverview()}>
                {primaryLabel}
                <MIcon name="arrow_forward" size={18} />
              </button>
            </div>
          )}

          {practiceMachines.length > 0 && (
            <section className={styles.machinePicker} aria-label={t("StudentHomePage.classMachinesAria")} data-guide="home-start">
              <header>
                <div><strong>{t("StudentHomePage.yourClassMachines")}</strong></div>
              </header>
              <div className={styles.machineGrid}>
                {practiceMachines.map((machine) => (
                  <div
                    key={machine.machine_node_id ?? `${machine.teaching_class_id ?? "course"}-${machine.vmid}`}
                    className={styles.machineOption}
                  >
                    <button
                      type="button"
                      className={styles.machineLaunchButton}
                      onClick={() => openPracticeMachine(machine)}
                      disabled={openingMachineId !== null || machine.vmid == null}
                      aria-label={t("StudentHomePage.machineLaunchAria", { action: practiceMachineActionLabel(machine, openingMachineId, t), name: machine.classMachineName ?? machine.name })}
                    >
                      <span className={styles.machineIcon}><MIcon name={machine.type === "lxc" ? "terminal" : "desktop_windows"} size={22} /></span>
                      <span className={styles.machineCopy}>
                        <strong>{machine.classMachineName ?? machine.name}</strong>
                        <small>
                          {machine.classMachineRole ?? t("StudentHomePage.defaultPracticeMachineName")}
                          {machine.vmid != null ? t("StudentHomePage.machineVmidSuffix", { vmid: machine.vmid }) : t("StudentHomePage.machineNotConfigured")}
                        </small>
                      </span>
                      <span className={`${styles.machineState} ${machine.status === "running" ? styles.machineStateReady : ""}`}>
                        {practiceMachineActionLabel(machine, openingMachineId, t)}
                      </span>
                      <span className={styles.machineArrow}><MIcon name="arrow_forward" size={20} /></span>
                    </button>
                    <button
                      type="button"
                      className={styles.machineInfoButton}
                      onClick={() => openMachineInformation(machine)}
                      disabled={machine.vmid == null}
                      aria-label={t("StudentHomePage.machineInfoAria", { name: machine.classMachineName ?? machine.name })}
                      title={t("StudentHomePage.machineInfoTitle")}
                    >
                      <MIcon name="info" size={20} />
                    </button>
                  </div>
                ))}
              </div>
            </section>
          )}
        </section>

      </main>

      <section className={styles.taskSection} aria-labelledby="task-title" data-student-tour="tasks" data-guide="home-tasks">
        <div className={styles.sectionHeading}>
          <h2 id="task-title">{t("StudentHomePage.courseTasks")}</h2>
          {(view.weeklyTasks.length > 0 || aiRequirementCount > 0) && <span>{t("StudentHomePage.taskSummary", { taskCount: view.weeklyTasks.length, checkpointCount: weeklyCheckpointCount + standaloneAiAssignments.reduce((count, assignment) => count + (assignment.items?.length ?? 0), 0) })}</span>}
        </div>

        {view.weeklyTasks.length > 0 && (
          <div className={styles.weeklyTaskList} aria-label={t("StudentHomePage.weeklyTaskListAria")}>
            {view.weeklyTasks.map((task, index) => {
              const expanded = expandedWeeklyTaskId === task.id;
              const checkpoints = task.checkpoints ?? [];
              return <article className={`${styles.weeklyTaskRow} ${expanded ? styles.weeklyTaskRowOpen : ""}`} key={task.id}>
                <button type="button" className={styles.weeklyTaskToggle} onClick={() => setExpandedWeeklyTaskId(expanded ? null : task.id)} aria-expanded={expanded} aria-controls={`weekly-task-${task.id}`}>
                  <span className={styles.taskNumber}>{index + 1}</span>
                  <span className={styles.assignmentTitle}>
                    <strong>{task.title}</strong>
                    <small>{t("StudentHomePage.weekSummary", { week: task.week_number, date: task.session_date, count: checkpoints.length })}</small>
                  </span>
                  <span className={styles.weeklyTaskHint}>{expanded ? t("StudentHomePage.collapse") : t("StudentHomePage.expandTask")}</span>
                  <MIcon name={expanded ? "expand_less" : "expand_more"} size={22} />
                </button>
                {expanded && <div className={styles.weeklyTaskDetail} id={`weekly-task-${task.id}`}>
                  <div className={styles.weeklyTaskFiles}>
                    {(task.files ?? []).length > 0 ? task.files.map((file) => <button type="button" className={styles.pdfButton} key={file.id} onClick={() => openWeeklyTaskDocument(task, file)} disabled={openingDocumentId !== null} title={file.filename}><MIcon name={openingDocumentId === file.id ? "hourglass_top" : "picture_as_pdf"} size={18} />{openingDocumentId === file.id ? t("StudentHomePage.opening") : t("StudentHomePage.viewPdfNamed", { filename: file.filename })}</button>) : <span className={styles.noTaskFile}>{t("StudentHomePage.noWeeklyPdf")}</span>}
                  </div>
                  {checkpoints.length > 0 ? <ol className={styles.checkpointList}>
                    {checkpoints.map((checkpoint, checkpointIndex) => {
                      const key = `${checkpoint.task_id}:${checkpoint.id}`;
                      const check = checkpointChecks[key]?.check ?? checkpoint.latest_check;
                      const checkMeta = check ? AI_CHECK_STATUS_META[check.status] : null;
                      const running = check?.status === "pending" || check?.status === "running";
                      const resultItem = check?.items?.[0];
                      return <li className={styles.checkpointRow} key={key}>
                        <span className={styles.aiRequirementNumber}>{checkpointIndex + 1}</span>
                        <div className={styles.checkpointContent}><small className={styles.checkpointSource}>{t("StudentHomePage.aiCheckTaskSource", { title: checkpoint.assignment_title })}</small><strong>{checkpoint.title}</strong>{checkpoint.description && <p>{checkpoint.description}</p>}{check && !running && <div className={`${styles.checkpointResult} ${styles[`checkpointResult_${check.status}`]}`}><MIcon name={check.status === "completed" ? "task_alt" : "error_outline"} size={17} /><span><b>{check.teacher_feedback || resultItem?.comment || check.error || check.summary || (checkMeta && t(checkMeta.labelKey))}</b>{typeof resultItem?.score === "number" && <small>{t("StudentHomePage.scoreLine", { score: resultItem.score, max: resultItem.max_score ?? 1 })}</small>}</span></div>}</div>
                        <button type="button" className={styles.checkpointCheckButton} onClick={() => submitCheckpointCheck(checkpoint)} disabled={Boolean(checkingCheckpointKey) || running || !checkpoint.check_available} title={checkpoint.check_available ? "" : t("StudentHomePage.checkpointNotApproved")}><MIcon name={running ? "sync" : checkpoint.check_available && check?.status === "completed" ? "refresh" : checkpoint.check_available ? "fact_check" : "schedule"} size={17} />{running ? t("StudentHomePage.checking") : checkingCheckpointKey === key ? t("StudentHomePage.submitting") : !checkpoint.check_available ? t("StudentHomePage.waitingForTeacherEnable") : check ? t("StudentHomePage.recheck") : t("StudentHomePage.checkThisOne")}</button>
                      </li>;
                    })}
                  </ol> : <div className={styles.checkpointEmpty}><MIcon name="pending_actions" size={20} /><span>{t("StudentHomePage.noCheckpointsPublished")}</span></div>}
                </div>}
              </article>;
            })}
          </div>
        )}

        {standaloneAiAssignments.length > 0 ? (
          <div className={styles.assignmentList}>
            {standaloneAiAssignments.map((assignment, index) => {
              const expanded = expandedAssignmentId === assignment.id;
              const check = assignmentChecks[assignment.id] ?? assignment.latest_check;
              const checkMeta = check ? AI_CHECK_STATUS_META[check.status] : null;
              const checkRunning = check?.status === "pending" || check?.status === "running";
              return (
                <article className={`${styles.assignmentRow} ${expanded ? styles.assignmentRowOpen : ""}`} key={assignment.id}>
                  <button
                    type="button"
                    className={styles.assignmentToggle}
                    onClick={() => toggleAssignment(assignment.id)}
                    aria-expanded={expanded}
                    aria-controls={`assignment-detail-${assignment.id}`}
                  >
                    <span className={styles.taskNumber}>{index + 1}</span>
                    <span className={styles.assignmentTitle}>
                      <strong>{assignment.title}</strong>
                      <small>{t("StudentHomePage.assignmentSummary", { date: formatAssignmentDate(assignment.approved_at, t), className: assignment.teaching_class_name, count: assignment.items?.length ?? 0 })}</small>
                    </span>
                    {checkMeta ? (
                      <span className={`${styles.assignmentStatus} ${styles[`assignmentStatus_${checkMeta.tone}`]}`}>
                        <MIcon name={checkMeta.icon} size={16} />{t(checkMeta.labelKey)}
                      </span>
                    ) : (
                      <span className={`${styles.assignmentStatus} ${styles.assignmentStatus_ready}`}>
                        <MIcon name="radio_button_unchecked" size={16} />{t("StudentHomePage.notYetChecked")}
                      </span>
                    )}
                    <MIcon name={expanded ? "expand_less" : "expand_more"} size={21} />
                  </button>

                  {expanded && (
                    <div className={styles.assignmentDetail} id={`assignment-detail-${assignment.id}`}>
                      <div className={styles.aiBrief}>
                        <span><MIcon name="auto_awesome" size={19} /></span>
                        <div>
                          <strong>{t("StudentHomePage.aiSummaryTitle")}</strong>
                          <p>{assignment.summary || t("StudentHomePage.aiSummaryFallback")}</p>
                        </div>
                      </div>

                      {assignment.source_document && (
                        <div className={styles.assignmentDocumentMeta}>
                          <span><MIcon name="picture_as_pdf" size={21} /></span>
                          <div>
                            <strong>{t("StudentHomePage.teacherUploadedPdf")}</strong>
                            <small>
                              {assignment.source_document.display_name || assignment.source_document.filename}
                              {t("StudentHomePage.correspondingItems", { count: assignment.items?.length ?? 0 })}
                            </small>
                          </div>
                        </div>
                      )}

                      <ol className={styles.aiRequirementList}>
                        {(assignment.items ?? []).map((item, itemIndex) => {
                          const detectableMeta = AI_DETECTABLE_META[item.detectable]
                            ?? AI_DETECTABLE_META.manual;
                          return (
                            <li className={styles.aiRequirementItem} key={item.id}>
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

                      {check && (
                        <section className={`${styles.aiReply} ${styles[`aiReply_${check.status}`]}`} aria-label={t("StudentHomePage.aiReplyAria")}>
                          <header>
                            <span><MIcon name={checkRunning ? "sync" : check.status === "completed" ? "support_agent" : "error_outline"} size={20} /></span>
                            <div>
                              <strong>{checkRunning ? t("StudentHomePage.aiCheckingEnvironment") : t("StudentHomePage.aiCheckReply")}</strong>
                              <small>
                                {typeof check.score === "number" ? t("StudentHomePage.scoreLineWithDefault", { score: check.score, max: check.max_score ?? 5 }) : (checkMeta && t(checkMeta.labelKey))}
                              </small>
                            </div>
                          </header>
                          {(check.teacher_feedback || check.summary || check.error) && <p>{check.teacher_feedback || check.error || check.summary}</p>}
                          {(check.items ?? []).length > 0 && (
                            <div className={styles.aiReplyItems}>
                              {check.items.map((item, itemIndex) => (
                                <div key={`${item.item_id}-${itemIndex}`}>
                                  <MIcon name={["passed", "pass"].includes(item.status) ? "check_circle" : item.status === "fail" || item.status === "failed" ? "error_outline" : "tips_and_updates"} size={17} />
                                  <span><strong>{item.title || t("StudentHomePage.scoringItem")}</strong>{item.comment && <small>{item.comment}</small>}</span>
                                  {typeof item.score === "number" && <em>{item.score}/{item.max_score ?? 1}</em>}
                                </div>
                              ))}
                            </div>
                          )}
                        </section>
                      )}

                      <footer className={styles.assignmentActions}>
                        <span><MIcon name="info" size={16} />{t("StudentHomePage.startMachineBeforeSubmit")}</span>
                        <div className={styles.assignmentActionButtons}>
                          {assignment.source_document && (
                            <button
                              type="button"
                              className={styles.pdfButton}
                              onClick={() => openAssignmentDocument(assignment)}
                              disabled={openingDocumentId !== null}
                              title={assignment.source_document.filename}
                            >
                              <MIcon name={openingDocumentId === assignment.id ? "hourglass_top" : "picture_as_pdf"} size={18} />
                              {openingDocumentId === assignment.id ? t("StudentHomePage.opening") : t("StudentHomePage.viewTaskPdf")}
                            </button>
                          )}
                          <button
                            type="button"
                            className={styles.aiCheckButton}
                            onClick={() => submitAiCheck(assignment)}
                            disabled={checkingAssignmentId !== null || checkRunning}
                          >
                            <MIcon name={checkRunning ? "sync" : "fact_check"} size={18} />
                            {checkRunning
                              ? t("StudentHomePage.aiChecking")
                              : checkingAssignmentId === assignment.id
                                ? t("StudentHomePage.submitting")
                                : check?.status === "completed"
                                  ? t("StudentHomePage.fixedRecheck")
                                  : t("StudentHomePage.doneSubmitCheck")}
                          </button>
                        </div>
                      </footer>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        ) : view.weeklyTasks.length === 0 ? (
          <div className={styles.taskEmpty}>
            <MIcon name="checklist" size={24} />
            <div>
              <strong>{t("StudentHomePage.noAiTasksPublished")}</strong>
              <p>{t("StudentHomePage.noAiTasksDesc")}</p>
            </div>
          </div>
        ) : null}
      </section>
        </>
      )}

      {activePracticeResource?.type === "lxc" && (
        <TerminalDialog resource={activePracticeResource} onClose={() => setActivePracticeResource(null)} />
      )}
      {activePracticeResource && activePracticeResource.type !== "lxc" && (
        <VncDialog resource={activePracticeResource} onClose={() => setActivePracticeResource(null)} />
      )}

      {documentPreview && (
        <div className={styles.pdfBackdrop} role="presentation" onMouseDown={() => setDocumentPreview(null)}>
          <section
            className={styles.pdfDialog}
            role="dialog"
            aria-modal="true"
            aria-label={t("StudentHomePage.taskPdfAria", { name: documentPreview.displayName })}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header>
              <div>
                <span><MIcon name="picture_as_pdf" size={22} /></span>
                <div>
                  <strong>{documentPreview.displayName}</strong>
                  <small>{documentPreview.filename}</small>
                </div>
              </div>
              <div className={styles.pdfDialogActions}>
                <a href={documentPreview.url} target="_blank" rel="noreferrer">
                  <MIcon name="open_in_new" size={18} />{t("StudentHomePage.openInNewTab")}
                </a>
                <button type="button" onClick={() => setDocumentPreview(null)} aria-label={t("StudentHomePage.closeTaskPdf")}>
                  <MIcon name="close" size={20} />
                </button>
              </div>
            </header>
            <iframe src={documentPreview.url} title={documentPreview.displayName} />
          </section>
        </div>
      )}

    </div>
  );
}
