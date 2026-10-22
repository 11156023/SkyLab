import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import LoadingState from "../../../components/LoadingState/LoadingState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import { useClassStudentsQuery } from "../../../queries/classroom";
import { useTeachingClassQuery } from "../../../queries/teachingClasses";
import { useUnsavedChanges } from "../../../contexts/UnsavedChangesContext";
import { useToast } from "../../../hooks/useToast";
import { useQueryView } from "../../../hooks/useQueryLoadState";
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

const MEMBERS_REFRESH_MS = 15_000;
const EMPTY = Object.freeze([]);

/* 讀不到班級：找不到／沒權限直接說原因；網路或伺服器錯誤給重試，不要一律說成「找不到班級」 */
function classLoadError(reason) {
  if (!reason) return null;
  if (reason.status === 404) return { kind: "not_found" };
  if (reason.status === 403) return { kind: "forbidden", message: reason.message };
  return { kind: "failed" };
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
    <section className={styles.lockedFeature} data-guide="ai-judge-locked">
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
  const toast = useToast();

  /* 班級只在進頁時讀一次：AI 核查是長時間互動的工作流，切回分頁不重抓，免得週次等 props 在操作中被換掉 */
  const classQuery = useTeachingClassQuery(classId, { select: normalizeAiJudgeClass, refetchOnWindowFocus: false });
  const item = classQuery.data ?? null;
  const loading = classQuery.data === undefined && (classQuery.isPending || classQuery.isFetching);
  const loadError = classQuery.data === undefined && !classQuery.isFetching ? classLoadError(classQuery.error) : null;

  /* 老師上課中途才開學生機器，機器標籤的開機燈號要跟著變：15 秒刷新、分頁隱藏時暫停、
     背景刷新失敗保留上一份；內容沒變時 structural sharing 讓 members 維持同一個參考，不重繪 */
  const membersActive = item?.status === "active";
  const membersQuery = useClassStudentsQuery(item?.id, {
    enabled: membersActive,
    refetchInterval: MEMBERS_REFRESH_MS,
    select: toAiJudgeMembers,
  });
  const members = membersQuery.data ?? EMPTY;
  /* 只有第一次載入顯示載入中：首載失敗後的輪詢重試不可把 AiJudgePanel 換成載入畫面（會清掉面板狀態）。
     沒資料時 v5 每次重抓都把 status 改回 pending、清掉 error，所以用 useQueryView：失敗後重試期間仍算失敗 */
  const membersView = useQueryView(membersQuery);
  const membersLoading = membersActive && membersView.loading;
  /* 讀不到學生機器不能默默當成沒有機器：核查會少掉學生姓名與機器，老師要知道是載入失敗。
     依訊息字串觸發，輪詢重試又失敗時訊息相同就不重複提示 */
  const membersFailedMessage = membersActive && membersView.error
    ? membersView.error?.message ?? t("Error.generic", { ns: "common" })
    : null;
  useEffect(() => {
    if (membersFailedMessage) toast.error(membersFailedMessage);
  }, [membersFailedMessage, toast]);

  if (loading) return <LoadingState fullPage text={t("ClassWorkspacePage.loadingClassText")} />;
  if (!item) {
    return (
      <div className={styles.page}>
        <button type="button" className={styles.backLink} onClick={() => navigate("/class-management")}>
          <MIcon name="arrow_back" size={18} />{t("ClassWorkspacePage.backToClassManagementBtn")}
        </button>
        {loadError?.kind === "failed"
          ? <ErrorState onRetry={() => classQuery.refetch()} />
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
