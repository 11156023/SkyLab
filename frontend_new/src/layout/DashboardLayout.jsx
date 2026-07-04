import { createContext, useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import Sidebar from "../components/Sidebar/Sidebar";
import AiFloatingChat from "../components/AiFloatingChat/AiFloatingChat";
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
        <div className={styles.mobileTopBar}>
          <button
            className={styles.mobileMenuBtn}
            onClick={() => setMobileOpen(true)}
            aria-label="開啟選單"
            type="button"
          >
            <span className="material-icons-outlined" style={{ fontSize: 22 }}>
              segment
            </span>
          </button>
        </div>
        <Outlet />
        <div className={`${styles.footer} ${compactFooter ? styles.footerCompact : ""}`}>SkyLab · 2026</div>
        <AiFloatingChat />
      </main>
    </div>
    </LayoutContext.Provider>
  );
}
