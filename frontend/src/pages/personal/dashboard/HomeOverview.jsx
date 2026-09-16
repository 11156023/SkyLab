import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import LoadingState from "../../../components/LoadingState/LoadingState";
import CourseCard from "../courses/CourseCard";
import styles from "./HomeOverview.module.scss";

function SectionHeading({ id, icon, title, action, onAction }) {
  return <header className={styles.sectionHeading}>
    <div className={styles.headingCopy}>
      <span className={styles.sectionIcon}><MIcon name={icon} size={22} /></span>
      <h2 id={id}>{title}</h2>
    </div>
    {action && <button type="button" className={styles.textButton} onClick={onAction}>{action}<MIcon name="arrow_forward" size={17} /></button>}
  </header>;
}

function EmptyPanel({ icon, title, action, onAction }) {
  return <div className={styles.emptyPanel}>
    <MIcon name={icon} size={20} /><span>{title}</span>
    {action && <button type="button" className={styles.textButton} onClick={onAction}>{action}<MIcon name="add" size={17} /></button>}
  </div>;
}

export default function HomeOverview({ paths, resources, resourcesError, coursesError, templates,
  templatesLoading, templatesError, openingMachineId, onOpenMachine, todayLabel }) {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();
  const displayedMachines = resources.slice(0, 4);
  const goToCourses = () => navigate("/courses");
  return <>
    <PageHeader title={t("StudentHomePage.title")} subtitle={todayLabel}>
      <button type="button" className={styles.headerButton} onClick={() => navigate("/my-resources")}>
        <MIcon name="computer" size={18} />{t("HomeOverview.allResources")}
      </button>
    </PageHeader>

    <section className={styles.section} aria-labelledby="recent-machines-title">
      <SectionHeading id="recent-machines-title" icon="history" title={t("HomeOverview.recentMachines")} />
      {resourcesError ? <EmptyPanel icon="cloud_off" title={t("HomeOverview.resourcesFailed")} />
        : displayedMachines.length ? <div className={styles.machineGrid}>
          {displayedMachines.map((machine) => <article className={styles.machineCard} key={machine.vmid}>
            <div className={styles.machineTop}>
              <span className={styles.machineIcon}><MIcon name={machine.type === "lxc" ? "terminal" : "desktop_windows"} size={20} /></span>
              <h3>{machine.name}</h3>
              <span className={`${styles.machineStatus} ${machine.status === "running" ? styles.running : ""}`}>
                {t(`HomeOverview.machineStatus.${["running", "stopped", "provisioning", "failed", "expired"].includes(machine.status) ? machine.status : "unknown"}`)}
              </span>
            </div>
            <div className={styles.machineMeta}>
              <span>{machine.type === "lxc" ? "LXC" : "VM"} · #{machine.vmid}</span>
            </div>
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
        </div> : <EmptyPanel icon="computer" title={t("HomeOverview.noMachines")}
          action={t("HomeOverview.createMachine")} onAction={() => navigate("/my-requests", { state: { create: true } })} />}
    </section>

    <section className={styles.section} aria-labelledby="joined-courses-title" data-guide="home-schedule">
      <SectionHeading id="joined-courses-title" icon="school" title={t("HomeOverview.joinedCourses")}
        action={t("HomeOverview.allCourses")} onAction={goToCourses} />
      {coursesError ? <EmptyPanel icon="cloud_off" title={t("StudentHomePage.errorTitle")} />
        : paths.length ? <div className={styles.courseGrid}>
          {paths.map((path) => <CourseCard key={path.id} path={path}
            onOpen={() => navigate(`/courses/${path.id}`, { state: { from: "/dashboard" } })} />)}
        </div> : <EmptyPanel icon="school" title={t("StudentHomePage.noPublishedCoursesTitle")} />}
    </section>

    <section className={styles.section} aria-labelledby="quick-template-title" data-guide="home-quick-templates">
      <SectionHeading id="quick-template-title" icon="bolt" title={t("StudentHomePage.quickPracticeEnv")} />
      {templatesLoading ? <LoadingState /> : templatesError ? <EmptyPanel icon="cloud_off" title={t("HomeOverview.templatesFailed")} />
        : templates.length ? <div className={styles.templateGrid}>
          {templates.map((template) => <button type="button" className={styles.templateCard} key={template.id}
            onClick={() => navigate(`/quick-template/${template.id}`, { state: { from: "/dashboard" } })}>
            <span className={styles.templateTop}><span className={styles.machineIcon}><MIcon name="layers" size={20} /></span>
              <strong>{template.name}</strong>
              <span className={styles.templateBadge}>{t("StudentHomePage.noManualReviewChip")}</span></span>
            <span className={styles.templateFooter}><span>{t("HomeOverview.machineCount", { count: template.nodes?.length ?? 0 })}</span>
              <span>{t("StudentHomePage.createNow")}<MIcon name="arrow_forward" size={18} /></span></span>
          </button>)}
        </div> : <EmptyPanel icon="inventory_2" title={t("StudentHomePage.noQuickTemplatesTitle")} />}
    </section>

    <aside className={styles.researchLink} data-guide="home-other-needs" data-student-tour="research">
      <div><MIcon name="science" size={22} /><span>{t("StudentHomePage.buildResearchEnv")}</span></div>
      <button type="button" className={styles.textButton} onClick={() => navigate("/my-requests")}>{t("StudentHomePage.goToMyRequests")}<MIcon name="arrow_forward" size={17} /></button>
    </aside>
  </>;
}
