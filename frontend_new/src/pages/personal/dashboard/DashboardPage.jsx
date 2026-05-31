import { useEffect, useMemo, useRef, useState } from "react";
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
    accent: "rgb(43, 112, 152)",
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
    accent: "rgb(43, 112, 152)",
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
    accent: "rgb(43, 112, 152)",
  }
];

/* ── Quick start templates (參考舊前端 templateQuickStart.ts) ── */
const TEMPLATE_CATEGORIES = [
  { id: "databases",    title: "資料庫",   desc: "關聯式資料庫與 NoSQL" },
  { id: "data-science", title: "資料科學", desc: "資料分析與機器學習" },
  { id: "monitoring",   title: "監控",     desc: "系統指標收集與視覺化" },
  { id: "ai-devtools",  title: "AI 工具",  desc: "LLM 部署與 AI 應用" },
  { id: "webservers",   title: "網站服務", desc: "網頁應用與入口儀表板" },
];

const CATEGORY_ACCENT = {
  databases:      "#5471bf",
  "data-science": "#7b92d0",
  monitoring:     "#5471bf",
  "ai-devtools":  "#7b92d0",
  webservers:     "#5471bf",
};

const LOGO_BASE = "https://cdn.jsdelivr.net/gh/selfhst/icons@main/webp";

const TEMPLATES = [
  { slug: "postgresql",      name: "PostgreSQL",       icon: "storage",    logo: `${LOGO_BASE}/postgres.webp`,     categoryId: "databases",
    desc: "功能強大的開源關聯式資料庫，支援豐富的資料型別與複雜查詢。" },
  { slug: "mariadb",         name: "MariaDB",          icon: "storage",    logo: `${LOGO_BASE}/mariadb.webp`,      categoryId: "databases",
    desc: "MySQL 相容的開源分支，社群維護快速更新。" },
  { slug: "mongodb",         name: "MongoDB",          icon: "dns",        logo: `${LOGO_BASE}/mongodb.webp`,      categoryId: "databases",
    desc: "彈性的文件型 NoSQL 資料庫，適合非結構化資料。" },
  { slug: "redis",           name: "Redis",            icon: "memory",     logo: `${LOGO_BASE}/redis.webp`,        categoryId: "databases",
    desc: "記憶體型鍵值資料庫，提供極致的存取效能。" },
  { slug: "jupyternotebook", name: "Jupyter Notebook", icon: "menu_book",  logo: `${LOGO_BASE}/jupyter.webp`,      categoryId: "data-science",
    desc: "資料科學與機器學習的互動式環境，支援 Python 科學運算。" },
  { slug: "grafana",         name: "Grafana",          icon: "show_chart", logo: `${LOGO_BASE}/grafana.webp`,      categoryId: "monitoring",
    desc: "強大的監控指標視覺化儀表板，支援多種資料來源。" },
  { slug: "openwebui",       name: "Open WebUI",       icon: "psychology", logo: `${LOGO_BASE}/open-webui.webp`,   categoryId: "ai-devtools",
    desc: "本地 LLM 對話介面，支援多種開源模型。" },
  { slug: "wordpress",       name: "WordPress",        icon: "article",    logo: `${LOGO_BASE}/wordpress.webp`,    categoryId: "webservers",
    desc: "全球最受歡迎的開源內容管理系統。" },
  { slug: "homepage",        name: "Homepage",         icon: "home",       logo: `${LOGO_BASE}/homepage.webp`,     categoryId: "webservers",
    desc: "簡潔優雅的個人入口儀表板。" },
];

const CATEGORY_BY_ID = Object.fromEntries(TEMPLATE_CATEGORIES.map((c) => [c.id, c]));

/* ── TemplateCard ── */
function TemplateCard({ name, desc, icon, logo, accent, categoryTitle }) {
  const [logoFailed, setLogoFailed] = useState(false);
  return (
    <button
      type="button"
      className={styles.templateCard}
      style={{ "--accent-color": accent }}
    >
      <div className={styles.templateHeader}>
        <span className={styles.templateLogo}>
          {logo && !logoFailed ? (
            <img
              src={logo}
              alt={`${name} logo`}
              width={28}
              height={28}
              loading="lazy"
              onError={() => setLogoFailed(true)}
            />
          ) : (
            <MIcon name={icon} size={22} />
          )}
        </span>
        <span className={styles.templateCategoryChip}>{categoryTitle}</span>
      </div>
      <div className={styles.templateBody}>
        <h4 className={styles.templateName}>{name}</h4>
        <p className={styles.templateDesc}>{desc}</p>
      </div>
      <div className={styles.templateFooter}>
        <span className={styles.templateAction}>
          立即建立
          <MIcon name="arrow_forward" size={14} />
        </span>
      </div>
    </button>
  );
}

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
  const [activeCategory, setActiveCategory] = useState("all");

  const filteredTemplates = useMemo(
    () => activeCategory === "all"
      ? TEMPLATES
      : TEMPLATES.filter((t) => t.categoryId === activeCategory),
    [activeCategory],
  );

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
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>嗨，{firstName} 👋</h1>
          <p className={styles.pageSubtitle}>歡迎回來，很高興再次見到你！</p>
        </div>
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
          {COURSES.map((c, i) => (
            <CourseCard key={`${c.id}-${i}`} {...c} />
          ))}
        </div>
      </section>

      {/* ── 快速入門 ── */}
      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <div className={styles.sectionTitle}>
            <span className={styles.sectionName}>
              <MIcon name="bolt" size={20} />
              快速入門
            </span>
            <span className={styles.sectionDesc}>選擇模板一鍵建立服務環境</span>
          </div>
          <button type="button" className={styles.sectionLink}>
            查看全部
            <MIcon name="arrow_forward" size={14} />
          </button>
        </div>

        {/* Category filter chips */}
        <div className={styles.filterChips} role="tablist" aria-label="模板分類">
          {[{ id: "all", title: "全部" }, ...TEMPLATE_CATEGORIES].map((cat) => {
            const count = cat.id === "all"
              ? TEMPLATES.length
              : TEMPLATES.filter((t) => t.categoryId === cat.id).length;
            const active = activeCategory === cat.id;
            return (
              <button
                key={cat.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setActiveCategory(cat.id)}
                className={`${styles.filterChip} ${active ? styles.filterChipActive : ""}`}
              >
                {cat.title}
                <span className={styles.filterCount}>{count}</span>
              </button>
            );
          })}
        </div>

        {/* Template card grid */}
        <div className={styles.templateGrid}>
          {filteredTemplates.map((t) => (
            <TemplateCard
              key={t.slug}
              {...t}
              accent={CATEGORY_ACCENT[t.categoryId]}
              categoryTitle={CATEGORY_BY_ID[t.categoryId].title}
            />
          ))}
        </div>
      </section>

    </div>
  );
}
