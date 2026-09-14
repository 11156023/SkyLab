import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { ClassroomService } from "../../../services/classroom";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { useToast } from "../../../hooks/useToast";
import AiJudgePanel from "../class-workspace/AiJudgePanel";
import styles from "../CourseOperations.module.scss";

const WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"];

export function normalizeAiJudgeClass(item) {
  const source = item ?? {};
  return {
    ...source,
    id: String(source.id),
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
      user_id: student.user_id,
      email: student.email,
      full_name: student.full_name,
      vmid: vm.vmid,
      vm_status: vm.status,
      vm_type: vm.vm_type,
      vm_cpu_usage_pct: null,
      vm_ram_usage_pct: null,
      vm_disk_usage_pct: null,
    })),
  );
}

function LockedFeature() {
  return (
    <section className={styles.lockedFeature}>
      <span><MIcon name="lock" size={22} /></span>
      <div>
        <h2>AI 檢查尚未開放</h2>
        <p>班級必須通過審核，且每位學生的所有節點都建立成功後才會正式啟用。</p>
      </div>
    </section>
  );
}

export default function AiJudgePage() {
  const { classId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [item, setItem] = useState(null);
  const [loading, setLoading] = useState(true);
  const [members, setMembers] = useState([]);
  const [membersLoading, setMembersLoading] = useState(false);

  useEffect(() => {
    let active = true;
    setItem(null);
    setLoading(true);
    TeachingClassesService.get(classId)
      .then((result) => active && setItem(normalizeAiJudgeClass(result)))
      .catch((reason) => active && toast.error(reason?.message ?? "無法讀取班級"))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [classId, toast]);

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

  if (loading) return <LoadingState fullPage text="正在讀取班級…" />;
  if (!item) {
    return (
      <div className={styles.page}>
        <button type="button" className={styles.backLink} onClick={() => navigate("/class-management")}>
          <MIcon name="arrow_back" size={18} />返回班級管理
        </button>
        <p className={styles.errorMessage}>找不到班級</p>
      </div>
    );
  }

  const weekday = WEEKDAY_LABELS[item.weekday] ?? "—";
  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow={`${item.code} · ${item.term}`}
        title={item.name}
        subtitle={`${item.students.length} 位學生 · ${item.weeks.length} 個課次 · 每週${weekday} ${item.startTime}–${item.endTime}`}
      >
        <div className={styles.pageActions}>
          <button type="button" className={`${styles.btnSecondary} ${styles.backBtn}`} onClick={() => navigate(`/class-management/${classId}`)}>
            <MIcon name="arrow_back" size={18} />返回班級工作頁
          </button>
        </div>
      </PageHeader>
      <main className={styles.workspaceContent}>
        {item.status !== "active" ? <LockedFeature /> : membersLoading ? <LoadingState text="正在讀取班級機器…" /> : <AiJudgePanel classId={item.id} members={members} weeks={item.weeks} />}
      </main>
    </div>
  );
}
