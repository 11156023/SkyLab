import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { RECENT_MACHINES_EVENT, readRecentMachines } from "../../../services/recentMachines";
import QuickTemplateCards from "../quick-practice/QuickTemplateCards";
import CourseTicket from "./CourseTicket";
import MachineCard from "./MachineCard";
import styles from "./HomeOverview.module.scss";

function SectionHeading({ id, title, action, onAction }) {
  return <header className={styles.sectionHeading}>
    <h2 id={id}>{title}</h2>
    {action && <button type="button" className={styles.headingAction} onClick={onAction}>{action}<MIcon name="arrow_forward" size={16} /></button>}
  </header>;
}

function EmptyPanel({ icon, title, action, onAction }) {
  return <div className={styles.emptyPanel}>
    <MIcon name={icon} size={20} /><span>{title}</span>
    {action && <button type="button" className={styles.textButton} onClick={onAction}>{action}<MIcon name="add" size={16} /></button>}
  </div>;
}

/* 這個瀏覽器的連線紀錄（主控台連上時 recordMachineUse 會寫入並發事件），vmid → usedAt */
function useRecentUse(userId) {
  const [history, setHistory] = useState(() => readRecentMachines(userId));
  useEffect(() => {
    const sync = () => setHistory(readRecentMachines(userId));
    sync();
    window.addEventListener(RECENT_MACHINES_EVENT, sync);
    return () => window.removeEventListener(RECENT_MACHINES_EVENT, sync);
  }, [userId]);
  return useMemo(() => new Map(history.map((entry) => [entry.vmid, entry.usedAt])), [history]);
}

/**
 * 學生首頁：機器用「玻璃外框＋白底內頁」卡（HomeCard，構圖取自複刻的 pin-card），
 * 快速練習用資料夾（QuickTemplateFolder：一台機器一張紙，構圖取自複刻的 folder-card），
 * 課堂用票券（CourseTicket）：票面寫上課時間地點，票根的條碼是練習進度。
 */
export default function HomeOverview({ paths, resources, resourcesError, coursesError, templates,
  templatesLoading, templatesError, openingMachineId, onOpenMachine, todayLabel, userId = null,
  shuttingDownId = null, onShutdownMachine }) {
  const { t } = useTranslation("personal");
  const navigate = useNavigate();
  const usedAt = useRecentUse(userId);
  /* 順序維持資源順序；連線紀錄只拿來印終端裡的「last」 */
  const displayedMachines = resources.slice(0, 4)
    .map((machine) => ({ ...machine, usedAt: usedAt.get(Number(machine.vmid)) }));
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
            onShutdown={onShutdownMachine} shuttingDown={shuttingDownId === machine.vmid}
            onInfo={(target) => navigate(`/my-resources/${target.vmid}`)} />)}
        </div> : <EmptyPanel icon="computer" title={t("HomeOverview.noMachines")}
          action={t("HomeOverview.createMachine")} onAction={() => navigate("/my-requests", { state: { create: true } })} />}
    </section>

    <section className={styles.section} aria-labelledby="joined-courses-title" data-guide="home-schedule">
      <SectionHeading id="joined-courses-title" title={t("HomeOverview.joinedCourses")}
        action={t("HomeOverview.allCourses")} onAction={goToCourses} />
      {coursesError ? <EmptyPanel icon="cloud_off" title={t("StudentHomePage.errorTitle")} />
        : paths.length ? <div className={styles.courseGrid}>
          {paths.map((path) => <CourseTicket key={path.id} path={path}
            onOpen={() => navigate(`/courses/${path.id}`, { state: { from: "/dashboard" } })} />)}
        </div> : <EmptyPanel icon="school" title={t("StudentHomePage.noPublishedCoursesTitle")} />}
    </section>

    <section className={styles.section} aria-labelledby="quick-template-title" data-guide="home-quick-templates">
      <SectionHeading id="quick-template-title" title={t("StudentHomePage.quickPracticeEnv")} />
      <QuickTemplateCards templates={templates} loading={templatesLoading} error={templatesError} from="/dashboard" />
    </section>

    <aside data-guide="home-other-needs" data-student-tour="research">
      <button type="button" className={styles.researchLink} onClick={() => navigate("/my-requests")}>
        <span className={styles.researchLabel}><MIcon name="science" size={22} />{t("StudentHomePage.buildResearchEnv")}</span>
        <span className={styles.researchGo}>{t("StudentHomePage.goToMyRequests")}<MIcon name="arrow_forward" size={16} /></span>
      </button>
    </aside>
  </>;
}
