import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../../contexts/AuthContext";
import { readRecentMachines, RECENT_MACHINES_EVENT, selectRecentMachines } from "../../../services/recentMachines";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import LoadingState from "../../../components/LoadingState/LoadingState";
import CourseCard from "../courses/CourseCard";
import styles from "./HomeOverview.module.scss";

function SectionHeading({ id, icon, title, description, action, onAction }) {
  return <header className={styles.sectionHeading}>
    <div className={styles.headingCopy}>
      <span className={styles.sectionIcon}><MIcon name={icon} size={22} /></span>
      <div><h2 id={id}>{title}</h2><p>{description}</p></div>
    </div>
    {action && <button type="button" className={styles.textButton} onClick={onAction}>{action}<MIcon name="arrow_forward" size={17} /></button>}
  </header>;
}

function EmptyPanel({ icon, title, description }) {
  return <div className={styles.emptyPanel}>
    <MIcon name={icon} size={28} /><div><strong>{title}</strong><p>{description}</p></div>
  </div>;
}

export default function HomeOverview({ paths, resources, resourcesError, coursesError, templates,
  templatesLoading, templatesError, openingMachineId, onOpenMachine, todayLabel }) {
  const { t, i18n } = useTranslation("personal");
  const { user } = useAuth();
  const navigate = useNavigate();
  const [history, setHistory] = useState(() => readRecentMachines(user?.id));
  useEffect(() => {
    const update = () => setHistory(readRecentMachines(user?.id));
    update();
    window.addEventListener(RECENT_MACHINES_EVENT, update);
    window.addEventListener("storage", update);
    return () => {
      window.removeEventListener(RECENT_MACHINES_EVENT, update);
      window.removeEventListener("storage", update);
    };
  }, [user?.id]);
  const recent = selectRecentMachines(resources, history);
  const formatUsedAt = (value) => new Intl.DateTimeFormat(i18n.language, {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(value);
  const goToCourses = () => navigate("/courses");
  return <>
    <PageHeader title={t("StudentHomePage.title")} subtitle={t("HomeOverview.subtitle", { today: todayLabel })}>
      <button type="button" className={styles.headerButton} onClick={() => navigate("/my-resources")}>
        <MIcon name="computer" size={18} />{t("HomeOverview.allResources")}
      </button>
    </PageHeader>

    <section className={styles.section} aria-labelledby="recent-machines-title">
      <SectionHeading id="recent-machines-title" icon="history" title={t("HomeOverview.recentMachines")}
        description={t("HomeOverview.recentDescription")} />
      {resourcesError ? <EmptyPanel icon="cloud_off" title={t("HomeOverview.resourcesFailed")} description={t("StudentHomePage.errorDesc")} />
        : recent.length ? <div className={styles.machineGrid}>
          {recent.map((machine) => <article className={styles.machineCard} key={machine.vmid}>
            <div className={styles.machineTop}>
              <span className={styles.machineIcon}><MIcon name={machine.type === "lxc" ? "terminal" : "desktop_windows"} size={24} /></span>
              <span className={`${styles.machineStatus} ${machine.status === "running" ? styles.running : ""}`}>
                {t(`HomeOverview.machineStatus.${["running", "stopped", "provisioning", "failed", "expired"].includes(machine.status) ? machine.status : "unknown"}`)}
              </span>
            </div>
            <h3>{machine.name}</h3>
            <p className={styles.machineMeta}>{machine.type === "lxc" ? "LXC" : "VM"} · #{machine.vmid}</p>
            <p className={styles.lastUsed}>{t("HomeOverview.lastUsed", { time: formatUsedAt(machine.usedAt) })}</p>
            <div className={styles.machineActions}>
              <button type="button" className={styles.launchButton} onClick={() => onOpenMachine(machine)}
                disabled={openingMachineId !== null || !["running", "stopped"].includes(machine.status)}>
                <MIcon name={openingMachineId === machine.vmid ? "hourglass_top" : "play_arrow"} size={18} />
                {t(openingMachineId === machine.vmid ? "StudentHomePage.actionStarting" : machine.status === "running" ? "StudentHomePage.actionEnter" : machine.status === "stopped" ? "StudentHomePage.actionStartAndEnter" : "HomeOverview.unavailable")}
              </button>
              <button type="button" className={styles.detailButton} onClick={() => navigate(`/my-resources/${machine.vmid}`)}
                aria-label={t("StudentHomePage.machineInfoAria", { name: machine.name })}><MIcon name="info" size={20} /></button>
            </div>
          </article>)}
        </div> : <EmptyPanel icon="history" title={t("HomeOverview.noRecentMachines")} description={t("HomeOverview.noRecentDescription")} />}
    </section>

    <section className={styles.section} aria-labelledby="joined-courses-title" data-guide="home-schedule">
      <SectionHeading id="joined-courses-title" icon="school" title={t("HomeOverview.joinedCourses")}
        description={t("HomeOverview.joinedDescription", { count: paths.length })}
        action={t("HomeOverview.allCourses")} onAction={goToCourses} />
      {coursesError ? <EmptyPanel icon="cloud_off" title={t("StudentHomePage.errorTitle")} description={t("StudentHomePage.errorDesc")} />
        : paths.length ? <div className={styles.courseGrid}>
          {paths.map((path) => <CourseCard key={path.id} path={path}
            onOpen={() => navigate(`/courses/${path.id}`, { state: { from: "/dashboard" } })} />)}
        </div> : <EmptyPanel icon="school" title={t("StudentHomePage.noPublishedCoursesTitle")} description={t("StudentHomePage.noPublishedCoursesDesc")} />}
    </section>

    <section className={styles.section} aria-labelledby="quick-template-title" data-guide="home-quick-templates">
      <SectionHeading id="quick-template-title" icon="bolt" title={t("StudentHomePage.quickPracticeEnv")}
        description={t("HomeOverview.quickDescription")} />
      {templatesLoading ? <LoadingState /> : templatesError ? <EmptyPanel icon="cloud_off" title={t("HomeOverview.templatesFailed")} description={t("HomeOverview.tryAgain")} />
        : templates.length ? <div className={styles.templateGrid}>
          {templates.map((template) => <button type="button" className={styles.templateCard} key={template.id}
            onClick={() => navigate(`/quick-template/${template.id}`, { state: { from: "/dashboard" } })}>
            <span className={styles.templateTop}><span className={styles.machineIcon}><MIcon name="layers" size={24} /></span>
              <span className={styles.templateBadge}>{t("StudentHomePage.noManualReviewChip")}</span></span>
            <strong>{template.name}</strong>
            <span className={styles.templateDescription}>{template.description || t("StudentHomePage.templateDescFallback", { count: template.nodes?.length ?? 0 })}</span>
            <span className={styles.templateFooter}><span>{t("HomeOverview.machineCount", { count: template.nodes?.length ?? 0 })}</span>
              <span>{t("StudentHomePage.createNow")}<MIcon name="arrow_forward" size={18} /></span></span>
          </button>)}
        </div> : <EmptyPanel icon="inventory_2" title={t("StudentHomePage.noQuickTemplatesTitle")} description={t("StudentHomePage.noQuickTemplatesDesc")} />}
    </section>

    <aside className={styles.researchLink} data-guide="home-other-needs" data-student-tour="research">
      <div><MIcon name="science" size={22} /><span>{t("StudentHomePage.buildResearchEnv")}</span></div>
      <button type="button" className={styles.textButton} onClick={() => navigate("/my-requests")}>{t("StudentHomePage.goToMyRequests")}<MIcon name="arrow_forward" size={17} /></button>
    </aside>
  </>;
}
