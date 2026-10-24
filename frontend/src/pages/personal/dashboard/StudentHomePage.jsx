import { Suspense, lazy, useState } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../../contexts/AuthContext";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { ResourcesService } from "../../../services/resources";
import { resourceKeys, useMyResourcesQuery, useResourceCacheUpdater } from "../../../queries/resources";
import { useCourseScheduleQuery } from "../../../queries/courses";
import { useQuickPracticeTemplatesQuery } from "../../../queries/quickPractice";
import { useQueryView } from "../../../hooks/useQueryLoadState";
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

const EMPTY = [];

/* 學生首頁：課表、我的機器與快速練習範本；課程詳情在 StudentCoursePage（/courses/:pathId）。 */
export default function StudentHomePage() {
  const { t } = useTranslation("personal");
  const { user } = useAuth();
  const confirm = useConfirm();
  const queryClient = useQueryClient();
  const [shuttingDownId, setShuttingDownId] = useState(null);
  const [activePracticeResource, setActivePracticeResource] = useState(null);
  const [openingMachineId, setOpeningMachineId] = useState(null);

  /* 我的機器與「我的資源」頁共用同一份快取（背景 30 秒刷新、開機中 5 秒）；
     課表與課程頁共用；快速練習範本與快速練習入口共用，不輪詢 */
  const resourcesQuery = useMyResourcesQuery();
  const scheduleQuery = useCourseScheduleQuery();
  const templatesQuery = useQuickPracticeTemplatesQuery();
  const resourceCache = useResourceCacheUpdater();

  const resources = Array.isArray(resourcesQuery.data?.resources) ? resourcesQuery.data.resources : EMPTY;
  /* 課表在 render 時才格式化：語系切換不必重抓資料，畫面也會換成新語系 */
  const paths = Array.isArray(scheduleQuery.data)
    ? scheduleQuery.data.map((row) => normalizeSchedule(row, t))
    : EMPTY;
  const quickTemplates = Array.isArray(templatesQuery.data) ? templatesQuery.data : EMPTY;
  /* 首次載入還沒有資料才算載入中／載入失敗；背景刷新失敗沿用上一份。
     首載失敗後背景輪詢重試期間維持失敗畫面，不閃回載入中（見 useQueryView） */
  const resourcesView = useQueryView(resourcesQuery);
  const scheduleView = useQueryView(scheduleQuery);
  const templatesView = useQueryView(templatesQuery);
  const loading = resourcesView.loading || scheduleView.loading;
  const resourcesError = Boolean(resourcesView.error);
  const coursesError = Boolean(scheduleView.error);
  const templatesLoading = templatesView.loading;
  const templatesError = Boolean(templatesView.error);

  // 每次 render 重新格式化：語系切換經 useTranslation 觸發 re-render，才會換成新語系
  const todayLabel = new Intl.DateTimeFormat(i18n.language, {
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());

  /* 操作後就地改清單快取的那一台；以快取當下的那一列為底合併（開機流程可能等了快一分半，
     不能拿點擊當時的舊清單蓋回去） */
  const patchResource = (vmid, patch) => {
    const current = (queryClient.getQueryData(resourceKeys.mine)?.resources ?? [])
      .find((item) => Number(item.vmid) === Number(vmid));
    if (current) resourceCache.update({ ...current, ...patch, vmid: current.vmid });
  };

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
      patchResource(resource.vmid, resource);
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
      patchResource(machine.vmid, { status: "stopped" });
    } catch (error) {
      toast.error(error?.message ?? t("EnvironmentMachineRow.controlFailed"));
    } finally {
      setShuttingDownId(null);
    }
  };

  if (loading) {
    return (
      <div className={styles.page}>
        <LoadingState fullPage text={t("StudentHomePage.loadingLabel")} />
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <HomeOverview paths={paths} resources={resources}
        resourcesError={resourcesError} coursesError={coursesError}
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
