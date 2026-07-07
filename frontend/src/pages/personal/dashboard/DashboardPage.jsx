import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../../contexts/AuthContext";
import { useDragScroll } from "../../../hooks/useDragScroll";
import { TemplatesService } from "../../../services/templates";
import MIcon from "../../../components/MIcon";
import styles from "./DashboardPage.module.scss";
import { COURSES } from "./dashboard.data";

const TEMPLATE_ACCENT = "#5471bf";

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
      {onSeeAll && (
        <button type="button" className={styles.sectionLink} onClick={onSeeAll}>
          查看全部
          <MIcon name="arrow_forward" size={14} />
        </button>
      )}
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
export default function DashboardPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const firstName = user?.full_name?.split(" ")[0] ?? user?.email?.split("@")[0] ?? "同學";

  const scrollRef = useRef(null);

  /* 快速入門：撈範本系統中可用（ready）的 LXC 範本 */
  const [templates, setTemplates] = useState([]);
  const [tplLoading, setTplLoading] = useState(true);
  useEffect(() => {
    TemplatesService.list()
      .then((res) => setTemplates(
        (res?.data ?? []).filter(
          (t) => t.resource_type === "lxc" && t.status === "ready" && t.pve_exists !== false,
        ),
      ))
      .catch(() => setTemplates([]))
      .finally(() => setTplLoading(false));
  }, []);

  useDragScroll(scrollRef, { draggingClass: styles.dragging });

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
          desc="選擇範本一鍵克隆練習環境"
        />

        {tplLoading ? (
          <p className={styles.sectionEmpty}>載入範本中…</p>
        ) : templates.length === 0 ? (
          <p className={styles.sectionEmpty}>
            目前沒有可用的範本。範本由老師或管理員在「範本管理」建立後即會出現在這裡。
          </p>
        ) : (
          <div className={styles.templateGrid}>
            {templates.map((t) => (
              <TemplateCard
                key={t.id}
                name={t.name}
                desc={t.description || "由範本克隆建立，數秒內完成佈建。"}
                icon="layers"
                accent={TEMPLATE_ACCENT}
                categoryTitle={`v${t.version}`}
                onSelect={() => navigate(`/quick-template/${t.id}`)}
              />
            ))}
          </div>
        )}
      </section>

    </div>
  );
}
