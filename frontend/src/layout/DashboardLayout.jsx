import { useCallback, useEffect, useMemo, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Suspense } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../components/MIcon";
import LoadingState from "../components/LoadingState/LoadingState";
import Sidebar from "../components/Sidebar/Sidebar";
import AiFloatingChat from "../components/AiFloatingChat/AiFloatingChat";
import ClassroomStudentLayer from "../components/Classroom/ClassroomStudentLayer";
import JobsProvider from "../components/Jobs/JobsProvider";
import SubnetBanner from "../components/SubnetBanner/SubnetBanner";
import SessionWarningDialog from "../components/SessionWarning/SessionWarningDialog";
import useSessionWarning from "../hooks/useSessionWarning";
import useDialogPresence from "../hooks/useDialogPresence";
import useBodyScrollLock from "../hooks/useBodyScrollLock";
import ErrorBoundary from "../components/ErrorBoundary/ErrorBoundary";
import UserGuide from "../components/UserGuide/UserGuide";
import { isAiJudgePath } from "./layoutRouteVisibility";
import { LayoutContext } from "./layoutContext";
import styles from "./DashboardLayout.module.scss";

export { LayoutContext };


const COLLAPSE_MIN_WIDTH = 1024;

export default function DashboardLayout() {
  const location = useLocation();
  const { t } = useTranslation("common");
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  /* 手機側欄抽屜開啟時鎖住底下頁面捲動 */
  useBodyScrollLock(mobileOpen);
  const [compactFooter, setCompactFooter] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [requestForm, setRequestForm] = useState(null);
  const [requestSubmission, reportRequestSubmission] = useState(null);
  const registerRequestForm = useCallback((api) => setRequestForm(api ?? null), []);
  /* 一次只有一個畫面被問：使用者問的一定是眼前這個。取消註冊時比對 id，
     免得後掛載的頁面先卸載時把還在畫面上的那個清掉。 */
  const [surface, setSurface] = useState(null);
  const registerSurface = useCallback((surfaceId, api) => {
    setSurface((current) => {
      if (!api) return current?.id === surfaceId ? null : current;
      return { id: surfaceId, ...api };
    });
  }, []);
  const layoutValue = useMemo(
    () => ({
      setCompactFooter,
      registerRequestForm,
      requestForm,
      requestSubmission,
      reportRequestSubmission,
      registerSurface,
      surface,
    }),
    [registerRequestForm, requestForm, registerSurface, surface, requestSubmission],
  );
  const { active: sessionWarning, dismiss, dismissPermanent } = useSessionWarning();
  const mobileOverlay = useDialogPresence(mobileOpen);
  const hideSidebar = isAiJudgePath(location.pathname);

  useEffect(() => {
    if (hideSidebar) setMobileOpen(false);
  }, [hideSidebar]);

  useEffect(() => {
    function handleResize() {
      if (window.innerWidth < COLLAPSE_MIN_WIDTH) {
        setCollapsed(false);
        setMobileOpen(false);
      }
    }
    window.addEventListener("resize", handleResize);
    handleResize();
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  return (
    <LayoutContext.Provider value={layoutValue}>
    {/* 任務狀態全站常駐（WS + toast + 詳情 dialog）；顯示按鈕在 Sidebar 底部 */}
    <JobsProvider>
    <div className={styles.layout}>
      {!hideSidebar && mobileOverlay.open && (
        <div
          className={`${styles.overlay} ${mobileOverlay.closing ? styles.overlayOut : ""}`}
          onClick={() => setMobileOpen(false)}
        />
      )}

      {!hideSidebar && (
        <Sidebar
          collapsed={collapsed}
          mobileOpen={mobileOpen}
          onToggle={() => setCollapsed((c) => !c)}
          onClose={() => setMobileOpen(false)}
        />
      )}

      <main className={styles.main}>
        {/* 教室學生層：直播橫幅 / 觀看視窗 / 接管狀態（模組 E） */}
        <ClassroomStudentLayer>
          <div className={styles.workspace}>
            <div className={styles.pageColumn}>
              {!hideSidebar && (
                <div className={styles.mobileTopBar}>
                  <button
                    className={styles.mobileMenuBtn}
                    onClick={() => setMobileOpen(true)}
                    aria-label={t("DashboardLayout.openMenuAriaLabel")}
                    type="button"
                  >
                    <MIcon name="segment" size={22} />
                  </button>
                </div>
              )}
              <SubnetBanner />
              <ErrorBoundary>
                <Suspense
                  fallback={
                    <div className={styles.routeLoading}>
                      <LoadingState text={t("DashboardLayout.pageLoading")} />
                    </div>
                  }
                >
                  <Outlet />
                </Suspense>
              </ErrorBoundary>
              <UserGuide />
              <div className={`${styles.footer} ${compactFooter ? styles.footerCompact : ""}`}>SkyLab · 2026</div>
            </div>
            <AiFloatingChat
              open={assistantOpen}
              onOpenChange={setAssistantOpen}
            />
          </div>
          <SessionWarningDialog
            status={sessionWarning}
            onClose={dismiss}
            onDismissPermanent={dismissPermanent}
          />
        </ClassroomStudentLayer>
      </main>
    </div>
    </JobsProvider>
    </LayoutContext.Provider>
  );
}
