import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import QuickTemplateCards from "../quick-practice/QuickTemplateCards";
import CourseFolder from "./CourseFolder";
import MachineCard from "./MachineCard";
import styles from "./HomeOverview.module.scss";

function SectionHeading({ id, title, action, onAction }) {
  return <header className={styles.sectionHeading}>
    <h2 id={id}>{title}</h2>
    {action && <button type="button" className={styles.textButton} onClick={onAction}>{action}<MIcon name="arrow_forward" size={16} /></button>}
  </header>;
}

function EmptyPanel({ icon, title, action, onAction }) {
  return <div className={styles.emptyPanel}>
    <MIcon name={icon} size={20} /><span>{title}</span>
    {action && <button type="button" className={styles.textButton} onClick={onAction}>{action}<MIcon name="add" size={16} /></button>}
  </div>;
}

/**
 * 學生首頁：機器與快速練習用同一款「玻璃外框＋白底內頁」卡（HomeCard），課堂用扁平資料夾。
 * 兩者構圖取自複刻的 pin-card／folder-card，外觀維持全站的玻璃卡語言。
 */
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
      <SectionHeading id="recent-machines-title" title={t("HomeOverview.recentMachines")} />
      {resourcesError ? <EmptyPanel icon="cloud_off" title={t("HomeOverview.resourcesFailed")} />
        : displayedMachines.length ? <div className={styles.machineGrid}>
          {displayedMachines.map((machine) => <MachineCard key={machine.vmid} machine={machine}
            openingMachineId={openingMachineId} onOpen={onOpenMachine}
            onInfo={(target) => navigate(`/my-resources/${target.vmid}`)} />)}
        </div> : <EmptyPanel icon="computer" title={t("HomeOverview.noMachines")}
          action={t("HomeOverview.createMachine")} onAction={() => navigate("/my-requests", { state: { create: true } })} />}
    </section>

    <section className={styles.section} aria-labelledby="joined-courses-title" data-guide="home-schedule">
      <SectionHeading id="joined-courses-title" title={t("HomeOverview.joinedCourses")}
        action={t("HomeOverview.allCourses")} onAction={goToCourses} />
      {coursesError ? <EmptyPanel icon="cloud_off" title={t("StudentHomePage.errorTitle")} />
        : paths.length ? <div className={styles.folderGrid}>
          {paths.map((path) => <CourseFolder key={path.id} path={path}
            onOpen={() => navigate(`/courses/${path.id}`, { state: { from: "/dashboard" } })} />)}
        </div> : <EmptyPanel icon="school" title={t("StudentHomePage.noPublishedCoursesTitle")} />}
    </section>

    <section className={styles.section} aria-labelledby="quick-template-title" data-guide="home-quick-templates">
      <SectionHeading id="quick-template-title" title={t("StudentHomePage.quickPracticeEnv")} />
      <QuickTemplateCards templates={templates} loading={templatesLoading} error={templatesError} from="/dashboard" variant="home" />
    </section>

    <aside className={styles.researchLink} data-guide="home-other-needs" data-student-tour="research">
      <div><MIcon name="science" size={22} /><span>{t("StudentHomePage.buildResearchEnv")}</span></div>
      <button type="button" className={styles.textButton} onClick={() => navigate("/my-requests")}>{t("StudentHomePage.goToMyRequests")}<MIcon name="arrow_forward" size={16} /></button>
    </aside>
  </>;
}
