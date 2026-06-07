import { useRef, useState } from "react";
import { useAuth } from "../../../contexts/AuthContext";
import { useDragScroll } from "../../../hooks/useDragScroll";
import MIcon from "../../../components/MIcon";
import styles from "./DashboardPage.module.scss";
import QuickTemplateFormPage from "./QuickTemplateFormPage";
import {
  COURSES,
  TEMPLATE_CATEGORIES,
  CATEGORY_ACCENT,
  CATEGORY_BY_ID,
  TEMPLATES,
} from "./dashboard.data";

/* ── SectionHeader ── */
function SectionHeader({ icon, title, desc, onSeeAll }) {
  return (
    <div className={styles.sectionHeader}>
      <div className={styles.sectionTitle}>
        <span className={styles.sectionName}>
          <MIcon name={icon} size={20} />
          {title}
        </span>
        <span className={styles.sectionDesc}>{desc}</span>
      </div>
      <button type="button" className={styles.sectionLink} onClick={onSeeAll}>
        查看全部
        <MIcon name="arrow_forward" size={14} />
      </button>
    </div>
  );
}

/* ── TemplateCard ── */
function TemplateCard({ name, desc, icon, logo, accent, categoryTitle, onSelect }) {
  const [logoFailed, setLogoFailed] = useState(false);
  return (
    <button
      type="button"
      className={styles.templateCard}
      style={{ "--accent-color": accent }}
      onClick={onSelect}
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
        <div className={styles.cardSubjects}>
          {subjects.map((s) => (
            <span key={s} className={styles.cardSubjectTag}>{s}</span>
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
export default function DashboardPage({ onNavigate }) {
  const { user } = useAuth();
  const firstName = user?.full_name?.split(" ")[0] ?? user?.email?.split("@")[0] ?? "同學";

  const scrollRef = useRef(null);
  const [activeCategory, setActiveCategory] = useState("all");
  const [quickSlug, setQuickSlug]           = useState(null);

  const filteredTemplates = activeCategory === "all"
    ? TEMPLATES
    : TEMPLATES.filter((t) => t.categoryId === activeCategory);

  useDragScroll(scrollRef, { draggingClass: styles.dragging });

  if (quickSlug) {
    return (
      <QuickTemplateFormPage
        slug={quickSlug}
        onBack={() => setQuickSlug(null)}
        onSubmitted={() => {
          setQuickSlug(null);
          onNavigate?.("my-resources");
        }}
      />
    );
  }

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
        <SectionHeader
          icon="school"
          title="課程推薦"
          desc="根據你的學習歷程精選推薦"
        />

        <div className={styles.courseScroll} ref={scrollRef}>
          {COURSES.map((c, i) => (
            <CourseCard key={`${c.id}-${i}`} {...c} />
          ))}
        </div>
      </section>

      {/* ── 快速入門 ── */}
      <section className={styles.section}>
        <SectionHeader
          icon="bolt"
          title="快速入門"
          desc="選擇模板一鍵建立服務環境"
        />

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
              onSelect={() => setQuickSlug(t.slug)}
            />
          ))}
        </div>
      </section>

    </div>
  );
}
