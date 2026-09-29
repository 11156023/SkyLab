import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import LoadingState from "../../../components/LoadingState/LoadingState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { ClassroomService } from "../../../services/classroom";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { useUnsavedChanges } from "../../../contexts/UnsavedChangesContext";
import AiJudgePanel from "../class-workspace/AiJudgePanel";
import ClassWorkflowStepper from "../class-workspace/ClassWorkflowStepper";
import styles from "../CourseOperations.module.scss";

/* 頁首沿用班級工作頁的翻譯（同一個班級，兩頁標頭要一致） */
const WEEKDAY_KEYS = [
  "ClassWorkspacePage.weekdayShortMon",
  "ClassWorkspacePage.weekdayShortTue",
  "ClassWorkspacePage.weekdayShortWed",
  "ClassWorkspacePage.weekdayShortThu",
  "ClassWorkspacePage.weekdayShortFri",
  "ClassWorkspacePage.weekdayShortSat",
  "ClassWorkspacePage.weekdayShortSun",
];

export function normalizeAiJudgeClass(item) {
  const source = item ?? {};
  return {
    ...source,
    id: String(source.id),
    nodes: source.machine_nodes ?? [],
    startTime: String(source.start_time ?? "").slice(0, 5),
    endTime: String(source.end_time ?? "").slice(0, 5),
    weeks: (source.weeks ?? []).map((week) => ({
      ...week,
      id: String(week.id),
      week: week.week_number,
      title: week.title ?? "",
    })),
    students: (source.students ?? []).map((student) => ({
      ...student,
      id: String(student.id),
    })),
  };
}

export function toAiJudgeMembers(students) {
  return (Array.isArray(students) ? students : []).flatMap((student) =>
    (student.vms ?? []).map((vm) => ({
      student_id: student.id,
      user_id: student.user_id,
      email: student.email,
      full_name: student.full_name,
      vmid: vm.vmid,
      node_key: vm.node_key,
      display_label: vm.display_label,
      node_name: vm.name,
      vm_status: vm.status,
      vm_type: vm.vm_type,
    })),
  );
}

function LockedFeature() {
  const { t } = useTranslation("teaching");
  return (
    <section className={styles.lockedFeature}>
      <span><MIcon name="lock" size={22} /></span>
      <div>
        <h2>{t("ClassWorkspacePage.notYetAvailableTitle", { label: t("ClassWorkspacePage.tabAiLabel") })}</h2>
        <p>{t("ClassWorkspacePage.lockedFeatureDesc")}</p>
      </div>
    </section>
  );
}

export default function AiJudgePage() {
  const { t } = useTranslation("teaching");
  const { classId } = useParams();
  const navigate = useNavigate();
  const { confirmLeave } = useUnsavedChanges();
  const [item, setItem] = useState(null);
  const [loading, setLoading] = useState(true);
  // 讀不到班級：找不到／沒權限直接說原因；網路或伺服器錯誤給重試，不要一律說成「找不到班級」
  const [loadError, setLoadError] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [members, setMembers] = useState([]);
  const [membersLoading, setMembersLoading] = useState(false);

  useEffect(() => {
    let active = true;
    setItem(null);
    setLoadError(null);
    setLoading(true);
    TeachingClassesService.get(classId)
      .then((result) => active && setItem(normalizeAiJudgeClass(result)))
      .catch((reason) => {
        if (!active) return;
        if (reason?.status === 404) setLoadError({ kind: "not_found" });
        else if (reason?.status === 403) setLoadError({ kind: "forbidden", message: reason.message });
        else setLoadError({ kind: "failed" });
      })
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [classId, reloadKey]);

  useEffect(() => {
    let active = true;
    if (!item?.id || item.status !== "active") {
      setMembers([]);
      setMembersLoading(false);
      return () => { active = false; };
    }

    setMembersLoading(true);
    ClassroomService.listClassStudents(item.id)
      .then((students) => active && setMembers(toAiJudgeMembers(students)))
      .catch(() => active && setMembers([]))
      .finally(() => active && setMembersLoading(false));
    return () => { active = false; };
  }, [item?.id, item?.status]);

  if (loading) return <LoadingState fullPage text={t("ClassWorkspacePage.loadingClassText")} />;
  if (!item) {
    return (
      <div className={styles.page}>
        <button type="button" className={styles.backLink} onClick={() => navigate("/class-management")}>
          <MIcon name="arrow_back" size={18} />{t("ClassWorkspacePage.backToClassManagementBtn")}
        </button>
        {loadError?.kind === "failed"
          ? <ErrorState onRetry={() => setReloadKey((key) => key + 1)} />
          : <p className={styles.errorMessage}>{loadError?.kind === "forbidden" && loadError.message ? loadError.message : t("ClassWorkspacePage.classNotFoundText")}</p>}
      </div>
    );
  }

  const weekdayKey = WEEKDAY_KEYS[item.weekday];
  // AI 提案、打到一半的訊息、核查草稿都只在畫面上，離開前先確認
  async function leaveTo(path) {
    if (await confirmLeave()) navigate(path);
  }

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow={item.location ? `${item.term} · ${item.location}` : item.term}
        title={item.name}
        subtitle={t("ClassWorkspacePage.subtitleTemplate", {
          students: item.students.length,
          weeks: item.weeks.length,
          weekday: weekdayKey ? t(weekdayKey) : "—",
          start: item.startTime,
          end: item.endTime,
        })}
      >
        <div className={styles.pageActions}>
          <button type="button" className={`${styles.btnSecondary} ${styles.backBtn}`} onClick={() => leaveTo("/class-management")}>
            <MIcon name="arrow_back" size={18} />{t("ClassWorkspacePage.backToClassManagementBtn")}
          </button>
        </div>
      </PageHeader>
      <ClassWorkflowStepper
        item={item}
        activeKey="ai"
        onSelect={(key) => { if (key !== "ai") leaveTo(`/class-management/${classId}/${key}`); }}
      />
      <div className={styles.workspaceContent}>
        {item.status !== "active" ? <LockedFeature /> : membersLoading ? <LoadingState text={t("AiJudgePage.loadingMachines")} /> : <AiJudgePanel classId={item.id} members={members} machineNodes={item.machine_nodes ?? []} weeks={item.weeks} />}
      </div>
    </div>
  );
}
