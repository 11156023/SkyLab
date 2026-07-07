import { createContext, useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import MIcon from "../components/MIcon";
import Sidebar from "../components/Sidebar/Sidebar";
import AiFloatingChat from "../components/AiFloatingChat/AiFloatingChat";
import ClassroomStudentLayer from "../components/Classroom/ClassroomStudentLayer";
import ErrorBoundary from "../components/ErrorBoundary/ErrorBoundary";
import styles from "./DashboardLayout.module.scss";

export const LayoutContext = createContext({ setCompactFooter: () => {} });

const COLLAPSE_MIN_WIDTH = 1280;

export default function DashboardLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [compactFooter, setCompactFooter] = useState(false);

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
    <LayoutContext.Provider value={{ setCompactFooter }}>
    <div className={`${styles.layout} ${collapsed ? styles.collapsed : ""}`}>
      {mobileOpen && (
        <div
          className={styles.overlay}
          onClick={() => setMobileOpen(false)}
        />
      )}

      <Sidebar
        collapsed={collapsed}
        mobileOpen={mobileOpen}
        onToggle={() => setCollapsed((c) => !c)}
        onClose={() => setMobileOpen(false)}
      />

      <main className={styles.main}>
        {/* 教室學生層：直播橫幅 / 觀看視窗 / 接管狀態（模組 E） */}
        <ClassroomStudentLayer>
          <div className={styles.mobileTopBar}>
            <button
              className={styles.mobileMenuBtn}
              onClick={() => setMobileOpen(true)}
              aria-label="開啟選單"
              type="button"
            >
              <MIcon name="segment" size={22} />
            </button>
          </div>
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
          <div className={`${styles.footer} ${compactFooter ? styles.footerCompact : ""}`}>SkyLab · 2026</div>
          <AiFloatingChat />
        </ClassroomStudentLayer>
      </main>
    </div>
    </LayoutContext.Provider>
  );
}
