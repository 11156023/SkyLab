import { useEffect, useRef } from "react";
import { useAuth } from "../../../contexts/AuthContext";
import MIcon from "../../../components/MIcon";
import styles from "./DashboardPage.module.scss";

/* ── Static course data ── */
const COURSES = [
  {
    id: "db-design",
    title: "資料庫設計與應用",
    description:
      "學習關聯式資料庫設計、SQL 語法、資料正規化與交易控制，部署 MySQL、PostgreSQL 或 MariaDB 進行上機實作。",
    subjects: ["資料庫設計", "後端開發", "SQL"],
    teacher: "王建明",
    classGroup: "資工系 113-A",
    icon: "storage",
    accent: "#5471bf",
  },
  {
    id: "linux-ops",
    title: "Linux 系統實作",
    description:
      "掌握 Linux 指令列操作、檔案系統管理、程序控制與基礎網路設定，在獨立容器環境中安全練習。",
    subjects: ["作業系統", "系統管理", "DevOps"],
    teacher: "李怡萱",
    classGroup: "資工系 113-B",
    icon: "terminal",
    accent: "#2b4d98",
  },
  {
    id: "data-science",
    title: "資料科學與機器學習",
    description:
      "使用 Jupyter Notebook 進行資料清理、視覺化分析與機器學習模型訓練，支援 Python 完整科學運算環境。",
    subjects: ["資料科學", "機器學習", "Python"],
    teacher: "陳文彬",
    classGroup: "資科系 113-A",
    icon: "science",
    accent: "#5471bf",
  },
  {
    id: "web-dev",
    title: "網頁應用開發",
    description:
      "建立完整的網站開發環境，部署前後端應用、CMS 或靜態網站，適合 Web 開發實作課程。",
    subjects: ["Web 開發", "網站架設", "前後端整合"],
    teacher: "林佳穎",
    classGroup: "資管系 113-A",
    icon: "public",
    accent: "#2b4d98",
  },
  {
    id: "db-design",
    title: "資料庫設計與應用",
    description:
      "學習關聯式資料庫設計、SQL 語法、資料正規化與交易控制，部署 MySQL、PostgreSQL 或 MariaDB 進行上機實作。",
    subjects: ["資料庫設計", "後端開發", "SQL"],
    teacher: "王建明",
    classGroup: "資工系 113-A",
    icon: "storage",
    accent: "#5471bf",
  },
  {
    id: "linux-ops",
    title: "Linux 系統實作",
    description:
      "掌握 Linux 指令列操作、檔案系統管理、程序控制與基礎網路設定，在獨立容器環境中安全練習。",
    subjects: ["作業系統", "系統管理", "DevOps"],
    teacher: "李怡萱",
    classGroup: "資工系 113-B",
    icon: "terminal",
    accent: "#2b4d98",
  }
];

/* ── CourseCard ── */
function CourseCard({ title, description, subjects, teacher, classGroup, icon, accent }) {
  return (
    <article
      className={styles.courseCard}
      style={{ "--accent-color": accent }}
    >
      <div className={styles.cardBanner}>
        <div className={styles.cardBannerLeft}>
          <div className={styles.cardBannerIcon}>
            <MIcon name={icon} size={22} />
          </div>
          <h3 className={styles.cardTitle}>{title}</h3>
        </div>
      </div>

      <div className={styles.cardBody}>
        <div className={styles.cardBannerMeta}>
          {subjects.map((s) => (
            <span key={s} className={styles.cardBannerTag}>{s}</span>
          ))}
        </div>
        <p className={styles.cardDesc}>{description}</p>

        <div className={styles.cardMeta}>
          <span className={styles.metaItem}>
            <MIcon name="person" size={12} />
            {teacher}
          </span>
          <span className={styles.metaItem}>
            <MIcon name="group" size={12} />
            {classGroup}
          </span>
        </div>
      </div>
    </article>
  );
}

/* ── Page ── */
export default function DashboardPage() {
  const { user } = useAuth();
  const firstName = user?.full_name?.split(" ")[0] ?? user?.email?.split("@")[0] ?? "同學";

  const scrollRef = useRef(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    let dragging = false;
    let startX = 0;
    let startScroll = 0;
    let moved = 0;
    let latestX = 0;
    let target = 0;                 /* desired scrollLeft */
    let current = el.scrollLeft;    /* animated scrollLeft */
    let velocity = 0;               /* px per ms (for momentum) */
    let lastSampleX = 0;
    let lastSampleT = 0;
    let rafId = null;

    const tick = () => {
      if (dragging) {
        /* Lerp current toward target for smooth follow */
        current += (target - current) * 0.25;
        if (Math.abs(target - current) < 0.5) current = target;
        el.scrollLeft = current;
        rafId = requestAnimationFrame(tick);
        return;
      }
      /* Momentum phase */
      if (Math.abs(velocity) < 0.02) {
        rafId = null;
        el.classList.remove(styles.dragging);
        return;
      }
      current -= velocity * 16;      /* 16ms ≈ one frame */
      velocity *= 0.95;              /* friction */
      const max = el.scrollWidth - el.clientWidth;
      if (current < 0) { current = 0; velocity = 0; }
      else if (current > max) { current = max; velocity = 0; }
      el.scrollLeft = current;
      rafId = requestAnimationFrame(tick);
    };

    const ensureLoop = () => {
      if (rafId == null) rafId = requestAnimationFrame(tick);
    };
    const cancelLoop = () => {
      if (rafId != null) {
        cancelAnimationFrame(rafId);
        rafId = null;
      }
    };

    const onWheel = (e) => {
      if (e.deltaY === 0) return;
      e.preventDefault();
      cancelLoop();
      velocity = 0;
      el.scrollLeft += e.deltaY;
      current = el.scrollLeft;
    };

    const onMouseDown = (e) => {
      if (e.button !== 0) return;
      cancelLoop();
      dragging = true;
      startX = e.pageX;
      latestX = e.pageX;
      lastSampleX = e.pageX;
      lastSampleT = performance.now();
      startScroll = el.scrollLeft;
      current = el.scrollLeft;
      target = el.scrollLeft;
      velocity = 0;
      moved = 0;
      el.classList.add(styles.dragging);
      ensureLoop();
    };
    const onMouseMove = (e) => {
      if (!dragging) return;
      latestX = e.pageX;
      const dx = latestX - startX;
      moved = Math.abs(dx);
      target = startScroll - dx;
      const now = performance.now();
      const dt = now - lastSampleT;
      if (dt > 4) {
        const v = (latestX - lastSampleX) / dt;
        velocity = 0.7 * v + 0.3 * velocity;
        lastSampleX = latestX;
        lastSampleT = now;
      }
    };
    const onMouseUp = () => {
      if (!dragging) return;
      dragging = false;
      if (performance.now() - lastSampleT > 80) velocity = 0;
      ensureLoop();
    };
    /* Suppress card click triggered by a drag */
    const onClickCapture = (e) => {
      if (moved > 5) {
        e.preventDefault();
        e.stopPropagation();
        moved = 0;
      }
    };

    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("mousedown", onMouseDown);
    el.addEventListener("click", onClickCapture, true);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);

    return () => {
      cancelLoop();
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("mousedown", onMouseDown);
      el.removeEventListener("click", onClickCapture, true);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  return (
    <div className={styles.page}>

      {/* ── Greeting ── */}
      <div className={styles.header}>
        <h1 className={styles.greeting}>嗨，{firstName} 👋</h1>
        <p className={styles.subtitle}>歡迎回來，很高興再次見到你！</p>
      </div>

      {/* ── 課程推薦 ── */}
      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <div className={styles.sectionTitle}>
            <span className={styles.sectionName}>
              <MIcon name="school" size={20} />
              課程推薦
            </span>
            <span className={styles.sectionDesc}>根據你的學習歷程精選推薦</span>
          </div>
          <button type="button" className={styles.sectionLink}>
            查看全部
            <MIcon name="arrow_forward" size={14} />
          </button>
        </div>

        <div className={styles.courseScroll} ref={scrollRef}>
          {COURSES.map((c) => (
            <CourseCard key={c.id} {...c} />
          ))}
        </div>
      </section>

    </div>
  );
}
