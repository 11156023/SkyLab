import { useEffect, useState } from "react";
import Sidebar from "../components/Sidebar/Sidebar";
import styles from "./DashboardLayout.module.scss";

const COLLAPSE_MIN_WIDTH = 1280;

export default function DashboardLayout({ children, activePage, onNavigate }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

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
        activePage={activePage}
        onNavigate={onNavigate}
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
        {children}
        <div className={styles.footer}>SkyLab · 2026</div>
      </main>
    </div>
  );
}
