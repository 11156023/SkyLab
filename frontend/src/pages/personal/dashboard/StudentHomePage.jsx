import { Suspense, lazy, useEffect, useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../../contexts/AuthContext";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { CoursesService } from "../../../services/courses";
import { ResourcesService } from "../../../services/resources";
import { QuickPracticeService } from "../../../services/quickPractice";
import { formatTime } from "../../../utils/formatDate";
import styles from "./StudentHomePage.module.scss";
import HomeOverview from "./HomeOverview";
import { waitForPracticeMachine } from "./student/studentDashboard";
import LoadingState from "../../../components/LoadingState/LoadingState";
import i18n from "../../../i18n";

// 終端機（xterm）與 VNC（noVNC）對話框都很大，學生首頁載入時多半用不到
const TerminalDialog = lazy(() => import("../resources/TerminalDialog"));
const VncDialog = lazy(() => import("../resources/VncDialog"));

/* 課表 API 的扁平欄位收進 schedule 物件；尚未開課（available）的課程在時間前加上「下次上課」日期。 */
function normalizeSchedule(row, t) {
  const sessionDate = row.session_date ? new Date(`${row.session_date}T00:00:00`) : null;
  const sessionLabel = sessionDate && !Number.isNaN(sessionDate.getTime())
    ? new Intl.DateTimeFormat(i18n.language, { month: "numeric", day: "numeric", weekday: "short" }).format(sessionDate)
    : "";
  return {
    ...row,
    schedule: {
      state: row.state,
      label: row.label,
      time: `${row.state === "available" && sessionLabel ? t("StudentHomePage.nextSession", { session: sessionLabel }) : ""}${formatTime(row.start_at, "")}–${formatTime(row.end_at, "")}`,
      teacher: row.teacher,
      place: row.location,
    },
  };
}

/* 學生首頁：課表、我的機器與快速練習範本；課程詳情在 StudentCoursePage（/courses/:pathId）。 */
export default function StudentHomePage() {
  const { t } = useTranslation("personal");
  const { user } = useAuth();
  const confirm = useConfirm();
  const [shuttingDownId, setShuttingDownId] = useState(null);
  const [view, setView] = useState({
    loading: true,
    hasError: false,
    resourcesError: false,
    paths: [],
    resources: [],
  });
  const [quickTemplates, setQuickTemplates] = useState([]);
  const [templatesError, setTemplatesError] = useState(false);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [activePracticeResource, setActivePracticeResource] = useState(null);
  const [openingMachineId, setOpeningMachineId] = useState(null);

  // 每次 render 重新格式化：語系切換經 useTranslation 觸發 re-render，才會換成新語系
  const todayLabel = new Intl.DateTimeFormat(i18n.language, {
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());

  useEffect(() => {
    let cancelled = false;

    async function loadStudentHome() {
      const [resourcesResult, scheduleResult] = await Promise.allSettled([
        ResourcesService.list(),
        CoursesService.listSchedule(),
      ]);

      if (cancelled) return;

      const paths = scheduleResult.status === "fulfilled" && Array.isArray(scheduleResult.value)
        ? scheduleResult.value.map((row) => normalizeSchedule(row, t))
        : [];
      const resources = resourcesResult.status === "fulfilled" && Array.isArray(resourcesResult.value)
        ? resourcesResult.value
        : [];

      setView({
        loading: false,
        hasError: scheduleResult.status === "rejected",
        resourcesError: resourcesResult.status === "rejected",
        paths,
        resources,
      });
    }

    loadStudentHome();
    return () => {
      cancelled = true;
    };
    // 只在進入頁面時載入一次（與原本的行為相同），語系切換不重新抓資料
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
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
  }, []);

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

  /* 首頁機器卡的關機鈕：先確認，送出正常關機後先把卡片標成已關機（同我的資源頁的樂觀更新） */
  const shutdownPracticeMachine = async (machine) => {
    const ok = await confirm({
      title: t("HomeOverview.confirmShutdownTitle", { name: machine.name }),
      message: t("HomeOverview.confirmShutdownMessage"),
      confirmText: t("HomeOverview.shutdown"),
    });
    if (!ok) return;
    setShuttingDownId(machine.vmid);
    try {
      await ResourcesService.shutdown(machine.vmid);
      toast.success(t("EnvironmentMachineRow.shutdownCommandSent"));
      setView((current) => ({ ...current, resources: current.resources.map((item) =>
        Number(item.vmid) === Number(machine.vmid) ? { ...item, status: "stopped" } : item) }));
    } catch (error) {
      toast.error(error?.message ?? t("EnvironmentMachineRow.controlFailed"));
    } finally {
      setShuttingDownId(null);
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
      <HomeOverview paths={view.paths} resources={view.resources}
        resourcesError={view.resourcesError} coursesError={view.hasError}
        templates={quickTemplates} templatesLoading={templatesLoading} templatesError={templatesError}
        openingMachineId={openingMachineId} onOpenMachine={openPracticeMachine} todayLabel={todayLabel}
        shuttingDownId={shuttingDownId} onShutdownMachine={shutdownPracticeMachine}
        userId={user?.id} />

      <Suspense fallback={null}>
        {activePracticeResource?.type === "lxc" && (
          <TerminalDialog resource={activePracticeResource} onClose={() => setActivePracticeResource(null)} />
        )}
        {activePracticeResource && activePracticeResource.type !== "lxc" && (
          <VncDialog resource={activePracticeResource} onClose={() => setActivePracticeResource(null)} />
        )}
      </Suspense>
    </div>
  );
}
