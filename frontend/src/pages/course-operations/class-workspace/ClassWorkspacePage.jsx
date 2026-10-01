import { memo, startTransition, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Background, BackgroundVariant, Controls, Panel, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import EmptyState from "../../../components/EmptyState/EmptyState";
import Modal from "../../../components/Modal/Modal";
import FileDropzone from "../../../components/FileDropzone/FileDropzone";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import ClassroomWatchDialog from "../../../components/Classroom/ClassroomWatchDialog";
import TerminalDialog from "../../personal/resources/TerminalDialog";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import { ClassroomService } from "../../../services/classroom";
import { courseNodeHasUsableSource, CourseEnvironmentsService } from "../../../services/courseEnvironments";
import { ResourcesService } from "../../../services/resources";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { formatDate } from "../../../utils/formatDate";
import ClassCreateDialog from "./ClassCreateDialog";
import EnvironmentChoice from "../EnvironmentChoice";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { focusInvalidField } from "../../../utils/focusField";
import {
  machineRuntimeState,
  mergeResourceUsageByVmid,
  RESOURCE_METRICS,
  usageForMetric,
} from "./classHeatmapUsage";
import styles from "../CourseOperations.module.scss";
import fwStyles from "../../network/firewall/FirewallPage.module.scss";
import GatewayNode from "../../network/firewall/nodes/GatewayNode";
import NodeHandles from "../../network/firewall/nodes/NodeHandles";
import ConnectionEdge from "../../network/firewall/edges/ConnectionEdge";
import ConnectionDetailPanel from "../../network/firewall/ConnectionDetailPanel";
import { routeEdges } from "../../network/firewall/utils/buildFlow";
import { joinList } from "../../../utils/joinList";
import ClassWorkflowStepper from "./ClassWorkflowStepper";
import { CLASS_WORKFLOW_TABS as TABS, POST_ACTIVE_TABS } from "./classWorkflowTabs";
import { uploadSequentially } from "../../../utils/uploadSequentially";
import { mergeUnsavedWeekEdits } from "./weeklyEdits";
import { isWeekVisible, parseStudentEmails, visibleWeekCount, weekFilesPayload } from "../classWeeks";
import { environmentSpecs } from "../nodeSpecs";
import { INTERNET_KEY } from "../../../components/ConnectionDialog/intents";
import { ThemeContext } from "../../../contexts/ThemeContext";
import { frozenTopologyLayout, normalizePublication, peerDetailPort, peerEdgeLabel, publicationDetailPort, publicationLabel } from "../courseTopology";

const JOB_STATUS_KEYS = {
  pending_review: "ClassWorkspacePage.jobStatusPendingReview", approved: "ClassWorkspacePage.jobStatusApproved", pending: "ClassWorkspacePage.jobStatusPending",
  running: "ClassWorkspacePage.jobStatusRunning", completed: "ClassWorkspacePage.jobStatusCompleted", failed: "ClassWorkspacePage.jobStatusFailed",
  rejected: "ClassWorkspacePage.jobStatusRejected", cancelled: "ClassWorkspacePage.jobStatusCancelled",
};

function normalizeClass(item) {
  return {
    ...item,
    id: String(item.id),
    startDate: item.start_date,
    endDate: item.end_date,
    startTime: String(item.start_time ?? "").slice(0, 5),
    endTime: String(item.end_time ?? "").slice(0, 5),
    bootLeadMinutes: item.boot_lead_minutes,
    shutdownGraceMinutes: item.shutdown_grace_minutes,
    nodes: item.machine_nodes ?? [],
    weeks: (item.weeks ?? []).map((week) => ({
      ...week,
      id: String(week.id),
      week: week.week_number,
      date: week.session_date,
      target: week.target_node_key ?? "",
      files: (week.files ?? []).map((file) => typeof file === "string" ? { filename: file } : file),
    })),
    students: (item.students ?? []).map((student) => ({ ...student, id: String(student.id), machines: student.machines ?? [] })),
    /* 老師自己那套機器（不在學生名單裡，但一樣算機器數、一樣能整班開關機） */
    instructorMachine: item.instructor_machine ? { ...item.instructor_machine, machines: item.instructor_machine.machines ?? [] } : null,
    jobs: item.provision_jobs ?? [],
    topologyEdges: item.topology_edges ?? [],
    nodePositions: item.node_positions ?? {},
    publications: item.publications ?? [],
    readyMachines: item.ready_machines ?? 0,
    totalMachines: item.total_machines ?? 0,
    archivedAt: item.archived_at,
    reclaimRequestedAt: item.reclaim_requested_at,
    resourcesReclaimedAt: item.resources_reclaimed_at,
  };
}

function ExtendDialog({ item, closing, busy, onClose, onExtend }) {
  const { t } = useTranslation("teaching");
  const [endDate, setEndDate] = useState(item.endDate);
  /* 外框（portal、遮罩、Esc、焦點、捲動鎖）交給共用 Modal；標題列與按鈕列沿用班級對話框的版型（bare） */
  return <Modal bare size="sm" className={`${styles.createDialog} ${styles.extendDialog}`} closing={closing} onClose={onClose} busy={busy} aria-labelledby="extend-class-title">
    <header className={styles.createDialogHeader}>
      <h2 id="extend-class-title">{t("ClassWorkspacePage.extendDialogTitle")}</h2>
      <button type="button" className={styles.dialogClose} aria-label={t("ClassWorkspacePage.closeAriaLabel")} disabled={busy} onClick={onClose}><MIcon name="close" size={19} /></button>
    </header>
    <form onSubmit={(event) => { event.preventDefault(); onExtend(endDate); }}>
      <div className={styles.createDialogBody}>
        <div className={styles.compactFormSection}>
          <p className={styles.dialogNote}>{t("ClassWorkspacePage.extendDialogDesc", { endDate: item.endDate })}</p>
          <label className={styles.field}>
            <span>{t("ClassWorkspacePage.extendDateLabel")}</span>
            <input type="date" min={item.endDate} value={endDate} onChange={(event) => setEndDate(event.target.value)} autoFocus />
          </label>
        </div>
      </div>
      <footer className={styles.createDialogFooter}>
        <button type="button" className={styles.btnSecondary} disabled={busy} onClick={onClose}>{t("ClassWorkspacePage.cancelBtn")}</button>
        <button type="submit" className={styles.btnPrimary} disabled={busy || endDate <= item.endDate}>{busy ? t("ClassWorkspacePage.processingLabel") : t("ClassWorkspacePage.extendBtn")}</button>
      </footer>
    </form>
  </Modal>;
}

function machineSummary(item, t) {
  if (item.status === "planning") {
    if (!item.course_environment || !item.nodes.length) return t("ClassWorkspacePage.machineSummaryNoEnv");
    return t("ClassWorkspacePage.machineSummaryPlanned", { total: (item.students.length + (item.instructorMachine ? 1 : 0)) * item.nodes.length });
  }
  return t("ClassWorkspacePage.machineSummaryReady", { ready: item.readyMachines, total: item.totalMachines });
}

function Overview({
  item,
  template,
  onProvision,
  onNavigate,
  onRetry,
  onReset,
  onReclaim,
  provisioning,
  recovering,
  lifecycleBusy,
}) {
  const { t } = useTranslation("teaching");
  const studentsReady = item.students.length > 0;
  const machinesReady = Boolean(item.course_environment) && item.nodes.length > 0;
  const completed = [studentsReady, machinesReady].filter(Boolean).length;
  const canProvision = completed === 2 && item.status === "planning";
  const [capacity, setCapacity] = useState(null);
  const [capacityLoading, setCapacityLoading] = useState(false);
  useEffect(() => {
    let active = true;
    if (!canProvision) {
      setCapacity(null);
      return undefined;
    }
    setCapacityLoading(true);
    TeachingClassesService.capacityPreview(item.id)
      .then((result) => active && setCapacity(result))
      .catch((error) => active && setCapacity({
        ready: false,
        issues: [error?.message ?? t("ClassWorkspacePage.capacityCheckFailedFallback")],
      }))
      .finally(() => active && setCapacityLoading(false));
    return () => { active = false; };
  }, [canProvision, item.id, item.students.length, item.nodes.length, item.course_version_id, t]);
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: item.timezone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const currentWeek = [...item.weeks].reverse().find((week) => week.date <= today) ?? item.weeks.find((week) => week.date > today);
  const weekLabel = currentWeek?.date === today ? t("ClassWorkspacePage.weekLabelThisWeek") : currentWeek?.date > today ? t("ClassWorkspacePage.weekLabelNextSession") : currentWeek === item.weeks.at(-1) && today > currentWeek.date ? t("ClassWorkspacePage.weekLabelLastWeek") : t("ClassWorkspacePage.weekLabelCurrentWeek");
  // 建置完成後，checklist 與建機工作只是把狀態面板已經說過的話再說兩次；
  // 一直留著會讓概覽的焦點散掉，只在還沒定案時顯示。
  const settled = ["active", "archived"].includes(item.status);
  const showChecklist = !settled;
  const showJobs = item.jobs.length > 0 && !settled;
  const setupItems = [
    [studentsReady, t("ClassWorkspacePage.fieldStudentsLabel"), studentsReady ? t("ClassWorkspacePage.studentsCountLabel", { count: item.students.length }) : t("ClassWorkspacePage.noStudentsYet")],
    [machinesReady, t("ClassWorkspacePage.fieldMachinesLabel"), machinesReady ? t("ClassWorkspacePage.machinesReadyValue", { name: template?.name ?? t("ClassWorkspacePage.appliedEnvFallback"), count: item.nodes.length }) : t("ClassWorkspacePage.noEnvSelected")],
  ];
  let title = t("ClassWorkspacePage.remainingSetupTitle", { count: 2 - completed });
  let description = t("ClassWorkspacePage.defaultDesc");
  let actionLabel = studentsReady ? t("ClassWorkspacePage.chooseEnvBtn") : t("ClassWorkspacePage.addStudentsBtn");
  let actionIcon = studentsReady ? "account_tree" : "person_add";
  let action = () => onNavigate(studentsReady ? "machines" : "students");
  let actionDisabled = false;
  let secondaryAction = null;
  if (canProvision) {
    title = capacityLoading ? t("ClassWorkspacePage.capacityFullCheckingTitle") : capacity?.ready ? t("ClassWorkspacePage.capacityReadyTitle") : t("ClassWorkspacePage.capacityNotReadyTitle");
    description = capacity
      ? t("ClassWorkspacePage.capacityDetailDesc", { machines: capacity.machine_count, cpu: capacity.cpu_cores, memory: Math.round(capacity.memory_mb / 1024), disk: capacity.disk_gb, ips: capacity.ip_count })
      : t("ClassWorkspacePage.noCapacityDesc", { students: item.students.length, nodes: item.nodes.length });
    if (capacity?.issues?.length) description = capacity.issues.join("；");
    actionLabel = provisioning ? t("ClassWorkspacePage.submittingLabel") : t("ClassWorkspacePage.confirmSubmitBtn");
    actionIcon = "rocket_launch";
    action = onProvision;
    actionDisabled = capacityLoading || !capacity?.ready;
  } else if (item.status === "pending_review") {
    title = t("ClassWorkspacePage.pendingReviewTitle"); description = t("ClassWorkspacePage.pendingReviewDesc"); actionLabel = "";
  } else if (item.status === "provisioning") {
    title = t("ClassWorkspacePage.provisioningTitle"); description = t("ClassWorkspacePage.provisioningDesc"); actionLabel = "";
  } else if (item.status === "partial_failed") {
    title = t("ClassWorkspacePage.partialFailedTitle");
    description = t("ClassWorkspacePage.partialFailedDesc");
    actionLabel = recovering ? t("ClassWorkspacePage.processingLabel") : t("ClassWorkspacePage.retryFailedBtn");
    actionIcon = "refresh";
    action = onRetry;
    actionDisabled = recovering;
    secondaryAction = item.readyMachines === 0 ? onReset : null;
  } else if (item.status === "active") {
    // 機器就緒數由下方機器列顯示、課堂監看在步驟列，狀態卡只說班級已就緒
    title = t("ClassWorkspacePage.classReadyTitle"); description = ""; actionLabel = "";
  } else if (item.status === "archived") {
    title = t("ClassWorkspacePage.classEndedTitle");
    description = item.resourcesReclaimedAt
      ? t("ClassWorkspacePage.archivedReclaimedDesc")
      : t("ClassWorkspacePage.archivedNotReclaimedDesc");
    actionLabel = item.resourcesReclaimedAt
      ? ""
      : lifecycleBusy ? t("ClassWorkspacePage.processingLabel") : t("ClassWorkspacePage.retryReclaimBtn");
    actionIcon = "refresh";
    action = onReclaim;
    actionDisabled = lifecycleBusy;
  }
  return <div className={styles.stack}>
    <section className={styles.readinessPanel}>
      <div className={styles.setupSummary}>
        <span className={styles.setupSummaryIcon}><MIcon name={item.status === "active" ? "check" : item.status === "partial_failed" ? "error_outline" : "assignment"} size={22} /></span>
        <div>{!settled && <span>{t("ClassWorkspacePage.setupProgressLabel", { completed })}</span>}<h2>{title}</h2>{description && <p>{description}</p>}</div>
        {actionLabel && <div className={styles.setupActions}>{secondaryAction && <button type="button" className={styles.btnSecondary} disabled={recovering} onClick={secondaryAction}>{t("ClassWorkspacePage.unlockAndEditBtn")}</button>}<button type="button" className={styles.btnPrimary} disabled={provisioning || actionDisabled} onClick={action}><MIcon name={actionIcon} size={17} />{actionLabel}</button></div>}
      </div>
      {showChecklist && <div className={styles.setupChecklist}>{setupItems.map(([done, label, note]) => <div key={label} className={done ? styles.setupItemDone : styles.setupItemTodo}><span><MIcon name={done ? "check" : "radio_button_unchecked"} size={17} /></span><div><strong>{label}</strong><small>{note}</small></div><em>{done ? t("ClassWorkspacePage.doneLabel") : t("ClassWorkspacePage.pendingLabel")}</em></div>)}</div>}
      {showJobs && <div className={styles.jobGrid}>{item.jobs.map((job, index) => <article key={job.id}><span>{t("ClassWorkspacePage.nodeIndexLabel", { index: index + 1 })}</span><strong>{JOB_STATUS_KEYS[job.status] ? t(JOB_STATUS_KEYS[job.status]) : job.status}</strong><small>{t("ClassWorkspacePage.jobResultSummary", { done: job.done, total: job.total, failed: job.failed_count })}</small></article>)}</div>}
    </section>
    <div className={styles.overviewDetailGrid}>
      <section className={styles.overviewInfoCard}>
        <div className={styles.overviewCardHeader}><h2>{weekLabel}</h2><button type="button" onClick={() => onNavigate("weekly")}>{t("ClassWorkspacePage.viewAllWeeksBtn")}<MIcon name="arrow_forward" size={15} /></button></div>
        {currentWeek ? <div className={styles.currentWeekSummary}><div><span>{t("ClassWorkspacePage.weekNumberLabel", { week: currentWeek.week })}</span><strong>{currentWeek.title || t("ClassWorkspacePage.noTopicSet")}</strong><small>{formatDate(`${currentWeek.date}T00:00:00`)}</small></div><span className={styles.weekFileCount}><MIcon name="attach_file" size={15} />{t("ClassWorkspacePage.fileCountUnit", { count: currentWeek.files.length })}</span></div> : <EmptyState icon="event" title={t("ClassWorkspacePage.noWeeksTitle")} action={<button type="button" className={styles.btnSecondary} onClick={() => onNavigate("weekly")}><MIcon name="calendar_view_week" size={16} />{t("ClassWorkspacePage.tabWeeklyLabel")}</button>} />}
      </section>
      <div className={styles.stack}>
        <section className={styles.overviewInfoCard}>
          <button type="button" className={styles.summaryRow} onClick={() => onNavigate("machines")}>
            <span><MIcon name="dns" size={17} />{machineSummary(item, t)}</span>
            <em>{t("ClassWorkspacePage.tabMachinesLabel")}<MIcon name="arrow_forward" size={15} /></em>
          </button>
        </section>
        <details className={`${styles.overviewInfoCard} ${styles.classInfoFold}`}>
          <summary>
            <b>{t("ClassWorkspacePage.classInfoTitle")}</b>
            <span>{formatDate(`${item.startDate}T00:00:00`)}–{formatDate(`${item.endDate}T00:00:00`)} · {t("ClassWorkspacePage.bootLeadShort", { minutes: item.bootLeadMinutes })}</span>
            <MIcon name="expand_more" size={19} />
          </summary>
          <div className={styles.classFacts}>
            {/* 學期、地點、固定課表都在頁首，這裡只列頁首沒有的 */}
            <div><span>{t("ClassWorkspacePage.coursePeriodLabel")}</span><strong>{formatDate(`${item.startDate}T00:00:00`)}–{formatDate(`${item.endDate}T00:00:00`)}</strong></div>
            <div><span>{t("ClassWorkspacePage.bootLeadFieldLabel")}</span><strong>{t("ClassWorkspacePage.minutesSuffix", { minutes: item.bootLeadMinutes })}</strong></div><div><span>{t("ClassWorkspacePage.shutdownGraceFieldLabel")}</span><strong>{t("ClassWorkspacePage.minutesSuffix", { minutes: item.shutdownGraceMinutes })}</strong></div>
          </div>
        </details>
      </div>
    </div>
  </div>;
}

function Students({ item, onRefresh }) {
  const { t } = useTranslation("teaching");
  const confirm = useConfirm();
  const toast = useToast();
  const [emails, setEmails] = useState("");
  const [busy, setBusy] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const addDialog = useDialogPresence(showAdd);
  const fileRef = useRef(null);
  const [emailsInvalid, setEmailsInvalid] = useState(false);
  const emailsInputRef = useRef(null);
  const locked = item.status !== "planning";
  async function add(event) {
    event.preventDefault();
    const values = parseStudentEmails(emails);
    if (!values.length) {
      setEmailsInvalid(true);
      focusInvalidField(emailsInputRef.current);
      return;
    }
    setBusy(true);
    try {
      const result = await TeachingClassesService.addStudents(item.id, values);
      setEmails("");
      setShowAdd(false);
      const notices = [t("ClassWorkspacePage.addedStudentsCount", { count: result.added })];
      if (result.not_found?.length) notices.push(t("ClassWorkspacePage.notFoundList", { list: joinList(result.not_found) }));
      if (result.invalid_role?.length) notices.push(t("ClassWorkspacePage.invalidRoleList", { list: joinList(result.invalid_role) }));
      toast.success(`${notices.join("；")}。`);
      onRefresh(result.class);
    } catch (error) { toast.error(error?.message ?? t("ClassWorkspacePage.addStudentsFailed")); }
    finally { setBusy(false); }
  }
  async function importCsv() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      const result = await TeachingClassesService.importStudents(item.id, file);
      const notices = [t("ClassWorkspacePage.csvImportedCount", { count: result.added })];
      if (result.not_found?.length) notices.push(t("ClassWorkspacePage.accountsNotFoundCount", { count: result.not_found.length }));
      if (result.invalid_role?.length) notices.push(t("ClassWorkspacePage.accountsInvalidRoleCount", { count: result.invalid_role.length }));
      toast.success(`${notices.join("；")}。`);
      onRefresh(result.class);
    } catch (error) { toast.error(error?.message ?? t("ClassWorkspacePage.csvImportFailed")); }
    finally { if (fileRef.current) fileRef.current.value = ""; setBusy(false); }
  }
  async function remove(studentId) {
    const ok = await confirm({
      title: t("ClassWorkspacePage.removeStudentConfirmTitle"),
      message: t("ClassWorkspacePage.removeStudentConfirmMessage"),
      confirmText: t("ClassWorkspacePage.removeLabel"),
      danger: true,
    });
    if (!ok) return;
    try { onRefresh(await TeachingClassesService.removeStudent(item.id, studentId)); }
    catch (error) { toast.error(error?.message ?? t("ClassWorkspacePage.removeFailed")); }
  }
  return <div className={styles.stack}>
    <div className={styles.memberPageHeader}>
      <div><h2>{t("ClassWorkspacePage.studentsTitle")}</h2><span>{t("ClassWorkspacePage.peopleCountUnit", { count: item.students.length })}</span></div>
      <div className={styles.memberActions}>
        <input ref={fileRef} className={styles.hiddenFileInput} disabled={locked} type="file" accept=".csv,text/csv" onChange={importCsv} />
        <button type="button" className={styles.btnSecondary} disabled={locked || busy} onClick={() => fileRef.current?.click()}><MIcon name="upload" size={16} />{t("ClassWorkspacePage.importCsvBtn")}</button>
        <button type="button" className={styles.btnSecondary} disabled={locked || busy} onClick={() => setShowAdd(true)}><MIcon name="person_add" size={16} />{t("ClassWorkspacePage.addStudentsBtn")}</button>
      </div>
    </div>

    <section className={styles.memberPanel}>
      <div className={styles.memberPanelHead}><strong>{t("ClassWorkspacePage.memberListHeader", { count: item.students.length })}</strong><span>{t("ClassWorkspacePage.machinesReadyShort", { ready: item.readyMachines, total: item.totalMachines || 0 })}</span></div>
      {item.students.length ? <div className={styles.memberList}>
        {/* 欄位標題：和下方每列用同一組欄寬，VMID、日期這些數字才看得出是什麼 */}
        <div className={styles.memberColumns}>
          <span>{t("ClassWorkspacePage.memberColStudent")}</span>
          <span>{t("ClassWorkspacePage.memberColVmid")}</span>
          <span>{t("ClassWorkspacePage.memberColMachines")}</span>
          <span>{t("ClassWorkspacePage.memberColJoined")}</span>
          <span />
        </div>
        {item.students.map((student) => {
        const ready = student.machines.filter((machine) => machine.status === "completed").length;
        return <article className={styles.memberRow} key={student.id}>
          <div className={styles.memberIdentity}><strong>{student.full_name || student.email}</strong><span>{student.email}</span></div>
          <span>{student.machines.length ? joinList(student.machines.map((machine) => machine.vmid ?? "—")) : "—"}</span>
          <span className={`${styles.memberMachineState} ${ready === item.nodes.length && item.nodes.length ? styles.memberReady : ""}`}>{item.nodes.length ? t("ClassWorkspacePage.readyCountLabel", { ready, total: item.nodes.length }) : t("ClassWorkspacePage.notBuiltLabel")}</span>
          <span>{formatDate(student.joined_at)}</span>
          {!locked ? <button type="button" className={styles.memberRemove} aria-label={t("ClassWorkspacePage.removeStudentAriaLabel")} onClick={() => remove(student.id)}><MIcon name="person_remove" size={17} /></button> : <span />}
        </article>;
      })}</div> : <EmptyState icon="group_add" title={t("ClassWorkspacePage.emptyStudentsTitle")} action={!locked && <button type="button" className={styles.btnPrimary} disabled={busy} onClick={() => setShowAdd(true)}><MIcon name="person_add" size={16} />{t("ClassWorkspacePage.addStudentsBtn")}</button>} />}
    </section>

    {addDialog.open && <Modal bare size="md" className={`${styles.createDialog} ${styles.studentDialog}`} closing={addDialog.closing} onClose={() => setShowAdd(false)} busy={busy} aria-labelledby="add-student-title"><header className={styles.createDialogHeader}><h2 id="add-student-title">{t("ClassWorkspacePage.addStudentsBtn")}</h2><button type="button" className={styles.iconBtn} aria-label={t("ClassWorkspacePage.closeAriaLabel")} onClick={() => setShowAdd(false)}><MIcon name="close" size={19} /></button></header><form onSubmit={add}><div className={styles.studentDialogBody}><label className={styles.field}><span>{t("ClassWorkspacePage.emailFieldLabel")}</span><textarea ref={emailsInputRef} className={emailsInvalid ? styles.fieldInvalid : undefined} rows={6} value={emails} onChange={(event) => { setEmails(event.target.value); setEmailsInvalid(false); }} placeholder="student01@example.edu&#10;student02@example.edu" autoFocus /></label></div><footer className={styles.createDialogFooter}><button type="button" className={styles.btnSecondary} onClick={() => setShowAdd(false)}>{t("ClassWorkspacePage.cancelBtn")}</button><button type="submit" className={styles.btnPrimary} disabled={busy}>{busy ? t("ClassWorkspacePage.addingLabel") : t("ClassWorkspacePage.addStudentsBtn")}</button></footer></form></Modal>}
  </div>;
}

export function WeeklyContent({ item, onRefresh }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const [weeks, setWeeks] = useState(item.weeks);
  const [saving, setSaving] = useState(false);
  const [uploadingWeek, setUploadingWeek] = useState("");
  /* 還沒填主題就按「發布」的那週：主題欄亮紅框，開始打字就解除 */
  const [titleMissingWeek, setTitleMissingWeek] = useState("");
  const titleInputRefs = useRef({});
  const locked = item.status === "archived";
  /* 有還沒儲存的修改時，重抓回來的班級資料（審核／建機中每 3 秒一次）只更新檔案等
     伺服器欄位，不蓋掉老師正在打的主題、機器與發布狀態；按儲存成功後才整份換回伺服器版本 */
  const unsavedRef = useRef(false);
  useEffect(() => {
    setWeeks((current) => (unsavedRef.current ? mergeUnsavedWeekEdits(item.weeks, current) : item.weeks));
  }, [item.weeks]);
  function update(id, key, value) {
    unsavedRef.current = true;
    setWeeks((rows) => rows.map((row) => row.id === id ? { ...row, [key]: value } : row));
  }
  /* 沒填主題就按「發布」：不反灰擋掉（使用者會以為壞了），改成告訴他缺什麼——
     主題欄亮紅框、游標跳進去，並跳提示 */
  function setWeekStatus(week, published, value) {
    if ((value === "published") === published) return;
    if (value === "published" && !week.title.trim()) {
      setTitleMissingWeek(week.id);
      focusInvalidField(titleInputRefs.current[week.id]);
      toast.info(t("ClassWorkspacePage.publishNeedsTopicHint"));
      return;
    }
    update(week.id, "status", value);
  }
  function mergeUploadedFiles(result) {
    setWeeks((current) => mergeUnsavedWeekEdits(normalizeClass(result).weeks, current));
  }
  async function upload(weekId, fileList) {
    const files = Array.from(fileList ?? []);
    if (!files.length) return;
    setUploadingWeek(weekId);
    try {
      /* 每傳完一個就併進畫面：後面的檔案失敗時，前面已存到伺服器的檔案仍要出現在清單上，
         否則接著按「儲存每週內容」送出的檔案清單會漏掉它，後端就把它刪了 */
      const { failed, lastError } = await uploadSequentially(files, async (file) => {
        mergeUploadedFiles(await TeachingClassesService.uploadWeekFile(item.id, weekId, file));
      });
      const done = files.length - failed.length;
      if (!failed.length) toast.success(t("ClassWorkspacePage.uploadedFilesCount", { count: done }));
      else if (files.length === 1) toast.error(lastError?.message ?? t("Error.generic", { ns: "common" }));
      else toast.error(t("CourseTemplateEditorPage.filesUploadPartialFail", { files: joinList(failed) }));
    } finally { setUploadingWeek(""); }
  }
  async function removeFile(weekId, file) {
    if (!file.id) return;
    setUploadingWeek(weekId);
    try { mergeUploadedFiles(await TeachingClassesService.deleteWeekFile(item.id, weekId, file.id)); }
    catch (error) { toast.error(error?.message ?? t("ClassWorkspacePage.removeFileFailed")); }
    finally { setUploadingWeek(""); }
  }
  async function save() {
    setSaving(true);
    try {
      // 教材只送已上傳檔案的 id：storage_key 由後端自己查，不再由前端帶
      const result = await TeachingClassesService.replaceWeeks(item.id, weeks.map((week) => ({ week_number: week.week, session_date: week.date, title: week.title.trim(), target_node_key: week.target || null, status: week.status, files: weekFilesPayload(week.files) })));
      unsavedRef.current = false;
      onRefresh(result); toast.success(t("ClassWorkspacePage.weeklySavedMsg"));
    } catch (error) { toast.error(error?.message ?? t("ClassWorkspacePage.saveFailed")); }
    finally { setSaving(false); }
  }
  return <div className={styles.stack}>
    <section className={styles.card}>
      <div className={styles.cardHeader}><div><h2>{t("ClassWorkspacePage.weeklyContentHeader", { count: weeks.length })}</h2><p>{t("ClassWorkspacePage.weeklyPublishHint")}</p></div><span className={styles.weekVisibleCount}>{t("ClassWorkspacePage.weeksVisibleCount", { count: visibleWeekCount(weeks) })}</span></div>
      <div className={styles.weekRows}>
        <div className={styles.weekRowsHead}><span>{t("ClassWorkspacePage.weekColWeek")}</span><span>{t("ClassWorkspacePage.topicTaskLabel")}</span><span>{t("ClassWorkspacePage.weekColMachine")}</span><span>{t("ClassWorkspacePage.taskFilesLabel")}</span><span>{t("ClassWorkspacePage.weekColVisible")}</span></div>
        {weeks.map((week) => {
          const published = isWeekVisible(week);
          return <article key={week.id}>
            <div className={styles.weekDate}><strong>{t("ClassWorkspacePage.weekNumberLabel", { week: week.week })}</strong><span>{week.date}</span></div>
            <input
              ref={(node) => { titleInputRefs.current[week.id] = node; }}
              className={`${styles.weekTitleInput} ${titleMissingWeek === week.id ? styles.fieldInvalid : ""}`}
              aria-invalid={titleMissingWeek === week.id}
              disabled={locked}
              value={week.title}
              onChange={(event) => {
                update(week.id, "title", event.target.value);
                if (titleMissingWeek === week.id) setTitleMissingWeek("");
              }}
              placeholder={t("ClassWorkspacePage.topicPlaceholder")}
            />
            <select className={styles.weekMachineSelect} disabled={locked} value={week.target} onChange={(event) => update(week.id, "target", event.target.value)} aria-label={t("ClassWorkspacePage.weekMachineAria", { week: week.week })}>
              <option value="">{t("ClassWorkspacePage.weekMachineAll")}</option>
              {item.nodes.map((node) => <option key={node.node_key} value={node.node_key}>{node.name}</option>)}
            </select>
            <div className={styles.weekFileList}>
              {week.files.map((file) => <span className={styles.weekFileChip} key={file.id ?? file.filename}><MIcon name="description" size={15} /><b>{file.filename}</b>{!locked && file.id && <button type="button" disabled={uploadingWeek === week.id} aria-label={t("ClassWorkspacePage.removeFileAria", { filename: file.filename })} onClick={() => removeFile(week.id, file)}><MIcon name="close" size={14} /></button>}</span>)}
              {!locked && <FileDropzone compact multiple title={t("common:FileDropzone.titleShort")} uploading={uploadingWeek === week.id} onFiles={(files) => upload(week.id, files)} />}
            </div>
            {/* 草稿｜發布 二選一：兩個選項都看得到，這週目前是哪個就亮哪個。
                沒填主題時「發布」看起來淡一點但點得下去，點了由 setWeekStatus 說明缺什麼 */}
            <div className={styles.weekVisible}>
              <SegmentedControl
                className={styles.weekStatusControl}
                ariaLabel={t("ClassWorkspacePage.weekVisibleAria", { week: week.week })}
                value={published ? "published" : "draft"}
                onChange={(value) => setWeekStatus(week, published, value)}
                options={[
                  { value: "draft", label: t("ClassWorkspacePage.weekStatusDraft"), buttonProps: { disabled: locked } },
                  {
                    value: "published",
                    label: t("ClassWorkspacePage.weekStatusPublished"),
                    buttonProps: { disabled: locked, "aria-disabled": !published && !week.title.trim() ? "true" : undefined },
                  },
                ]}
              />
            </div>
          </article>;
        })}
      </div>
      {!locked && <div className={styles.actionFooter}><button type="button" className={styles.btnPrimary} disabled={saving || Boolean(uploadingWeek)} onClick={save}><MIcon name="save" size={16} />{saving ? t("ClassWorkspacePage.savingLabel") : t("ClassWorkspacePage.saveWeeklyBtn")}</button></div>}
    </section>
  </div>;
}

/* 唯讀拓撲節點：外觀與防火牆拓撲、課程環境編輯器同一套 .vmNode，差別只在
   不能拖曳連線。角色與對外服務是課程環境宣告的內容，這裡一併顯示。 */
function ReadonlyMachineNode({ data }) {
  const { t } = useTranslation("teaching");
  const { node, publicationCount } = data;
  const spec = `${node.cpu} CPU · ${Math.round(node.memory_mb / 1024)} GB · ${node.disk_gb} GB`;
  return <div className={`${fwStyles.vmNode} ${styles.courseMachineNode} ${styles.flowMachineNodeStatic}`}>
    <NodeHandles />
    <div className={fwStyles.vmStatus} style={{ background: "var(--color-status-neutral)" }} />
    <div className={fwStyles.vmInfo}>
      <span className={fwStyles.vmName} title={node.name}>{node.name}</span>
      {/* 副標只寫規格，跟教學環境編輯器一致；角色與完整規格放滑過提示 */}
      <span className={`${fwStyles.vmMeta} ${styles.courseMachineMeta}`} title={node.role ? `${node.role} · ${spec}` : spec}>{spec}</span>
    </div>
    <MIcon name={node.resource_type === "lxc" ? "terminal" : "dns"} size={15} />
    {publicationCount > 0 && <span className={fwStyles.exposedBadge} title={t("ClassWorkspacePage.nodePublicCount", { count: publicationCount })}>
      <MIcon name="public" size={11} />{publicationCount}
    </span>}
  </div>;
}

const READONLY_NODE_TYPES = { classMachine: ReadonlyMachineNode, gateway: GatewayNode };
const READONLY_EDGE_TYPES = { connection: ConnectionEdge };

/* 對外服務清單：與課程環境編輯器側欄的摘要同一套說法。網址是每位學生各一個，
   模板只存主機名樣板，所以這裡顯示的是樣板而不是實際網址。 */
function PublicationSummary({ item }) {
  const { t } = useTranslation("teaching");
  const nameByKey = useMemo(
    () => Object.fromEntries(item.nodes.map((node) => [node.node_key, node.name])),
    [item.nodes],
  );
  return <div className={styles.classPublicationList}>
    {item.publications.map((publication) => <div key={publication.id} className={styles.classPublicationRow}>
      <span className={styles.classPublicationIcon}>
        <MIcon name={publication.mode === "domain" ? "public" : "swap_horiz"} size={16} />
      </span>
      <div>
        <strong>{nameByKey[publication.node_key] ?? publication.node_key} · Port {publication.port}</strong>
        <small>{publication.mode === "domain"
          ? t("ClassWorkspacePage.publicationDomainHint", { hostname: `${publication.hostname_prefix ?? ""}` })
          : t("ClassWorkspacePage.publicationForwardHint")}</small>
      </div>
    </div>)}
  </div>;
}

function TopologyPreview({ item }) {
  const { t } = useTranslation("teaching");
  /* 畫布配色跟防火牆頁一樣跟著主題；沒有 provider 就當淺色 */
  const theme = useContext(ThemeContext)?.theme ?? "light";
  const [showInternet, setShowInternet] = useState(false);
  const [selectedKey, setSelectedKey] = useState("");
  const publications = useMemo(
    () => (item.publications ?? []).map((publication, index) => normalizePublication(publication, index)),
    [item.publications],
  );
  const publicationCounts = useMemo(() => {
    const counts = {};
    for (const publication of publications) counts[publication.nodeKey] = (counts[publication.nodeKey] ?? 0) + 1;
    return counts;
  }, [publications]);
  const nameOf = (key) => (key === null || key === undefined || key === INTERNET_KEY
    ? t("GatewayNode.internet", { ns: "network" })
    : (item.nodes.find((node) => String(node.node_key) === String(key))?.name ?? String(key)));

  const nodes = useMemo(() => {
    const machines = item.nodes.map((node, index) => {
      // 老師在課程環境排好的座標優先；環境版本查不到才退回依序排開
      const saved = item.nodePositions?.[node.node_key];
      return {
        id: String(node.node_key),
        type: "classMachine",
        position: saved
          ? { x: Number(saved.x), y: Number(saved.y) }
          : { x: 70 + index * 250, y: 95 + (index % 2) * 35 },
        data: { node, publicationCount: publicationCounts[node.node_key] ?? 0 },
      };
    });
    /* 唯讀畫布不能拖：機器靠太近、連線標籤會被隔壁那台蓋住時，顯示時把它們推開
       （只影響這裡畫出來的位置，不改課程環境存的座標；已發布的教學環境編輯器同一套） */
    const { positions, internet } = frozenTopologyLayout(machines);
    const placed = machines.map((node) => ({ ...node, position: positions.get(node.id) }));
    return [...placed, { id: INTERNET_KEY, type: "gateway", position: internet, data: {} }];
  }, [item.nodes, item.nodePositions, publicationCounts]);

  const edges = useMemo(() => {
    const peers = item.topologyEdges.map((edge, index) => {
      const id = `peer-${edge.id ?? index}`;
      const bidirectional = edge.direction === "bidirectional";
      return {
        id,
        source: String(edge.source_node_key),
        target: String(edge.target_node_key),
        type: "connection",
        data: {
          edge: { source_vmid: String(edge.source_node_key), target_vmid: String(edge.target_node_key), direction: edge.direction, ports: [peerDetailPort(edge)] },
          label: peerEdgeLabel(bidirectional ? t("ClassWorkspacePage.directionBidirectional") : t("ClassWorkspacePage.directionOneWay"), edge),
          showLabel: true,
          selected: selectedKey === id,
          onSelect: () => setSelectedKey((current) => (current === id ? "" : id)),
        },
      };
    });
    const inbound = publications.map((publication) => {
      const id = `publication-${publication.id}`;
      return {
        id,
        source: INTERNET_KEY,
        target: String(publication.nodeKey),
        type: "connection",
        data: {
          edge: { source_vmid: null, target_vmid: String(publication.nodeKey), direction: "one_way", ports: [publicationDetailPort(publication)] },
          label: publicationLabel(t, publication),
          showLabel: true,
          selected: selectedKey === id,
          onSelect: () => setSelectedKey((current) => (current === id ? "" : id)),
        },
      };
    });
    /* 上網線是預設策略：每台都有，預設藏起來、要看再開（與防火牆頁相同） */
    const outbound = item.nodes.map((node) => {
      const id = `outbound-${node.node_key}`;
      return {
        id,
        source: String(node.node_key),
        target: INTERNET_KEY,
        type: "connection",
        hidden: !showInternet,
        data: {
          edge: { source_vmid: String(node.node_key), target_vmid: null, direction: "one_way", ports: [] },
          label: "",
          showLabel: true,
          selected: selectedKey === id,
          onSelect: () => setSelectedKey((current) => (current === id ? "" : id)),
        },
      };
    });
    return routeEdges([...peers, ...inbound, ...outbound], nodes);
  }, [item.topologyEdges, item.nodes, publications, nodes, selectedKey, showInternet, t]);

  const detail = useMemo(() => edges.find((edge) => edge.id === selectedKey)?.data.edge ?? null, [edges, selectedKey]);
  const detailPanel = useDialogPresence(detail, 220);
  function toggleInternet() {
    const next = !showInternet;
    setShowInternet(next);
    if (!next && selectedKey.startsWith("outbound-")) setSelectedKey("");
  }
  // 高度跟著節點數走，一台機器不該撐出一整片空網格。
  const canvasHeight = Math.min(520, 360 + Math.max(0, item.nodes.length - 1) * 80);
  /* .card 是 flex column：.flowWrap 的 flex:1（basis 0）+ min-height:0 會把畫布壓成 0 高，
     inline 的 flex:none 才壓得過兩個 class */
  return <div className={`${styles.readonlyTopology} ${fwStyles.flowWrap}`} style={{ height: canvasHeight, flex: "none" }}>
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={READONLY_NODE_TYPES}
      edgeTypes={READONLY_EDGE_TYPES}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      onEdgeClick={(_, edge) => edge.data?.onSelect?.()}
      onPaneClick={() => setSelectedKey("")}
      deleteKeyCode={null}
      fitView
      /* 自動置中最多放大到 1 倍：機器少時才不會被放到 1.5 倍、字比編輯器大一圈；手動仍可拉近 */
      fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
      minZoom={0.5}
      maxZoom={1.5}
      colorMode={theme}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
      <Controls showInteractive={false} />
      <Panel position="top-left">
        <div className={fwStyles.toolbar}>
          <button type="button" className={`${fwStyles.toolbarBtn} ${showInternet ? fwStyles.toolbarBtnActive : ""}`} onClick={toggleInternet}>
            <MIcon name={showInternet ? "public" : "public_off"} size={16} />
            {t("FirewallPage.internetLines", { ns: "network" })}
          </button>
        </div>
      </Panel>
      <Panel position="bottom-left" style={{ marginLeft: 60 }}>
        <div className={fwStyles.legend}>
          <span className={fwStyles.legendItem}><i className={`${fwStyles.legendLine} ${fwStyles.legendInbound}`} />{t("FirewallPage.legendInbound", { ns: "network" })}</span>
          <span className={`${fwStyles.legendItem} ${showInternet ? "" : fwStyles.legendItemHidden}`}><i className={`${fwStyles.legendLine} ${fwStyles.legendOutbound}`} />{t("FirewallPage.legendOutbound", { ns: "network" })}</span>
          <span className={fwStyles.legendItem}><i className={`${fwStyles.legendLine} ${fwStyles.legendInternal}`} />{t("FirewallPage.legendInternal", { ns: "network" })}</span>
        </div>
      </Panel>
    </ReactFlow>
    {/* 只看不改：同一個細節面板，但沒有刪除鈕 */}
    {detailPanel.item && <ConnectionDetailPanel
      edge={detailPanel.item}
      resolveName={nameOf}
      allowOpen={false}
      closing={detailPanel.closing}
      onClose={() => setSelectedKey("")}
    />}
  </div>;
}

function Machines({ item, templates, template, onRefresh, onTemplate, createdTemplateId }) {
  const { t } = useTranslation("teaching");
  const navigate = useNavigate();
  const toast = useToast();
  const createdNoticeShown = useRef(false);
  useEffect(() => {
    if (!createdTemplateId || createdNoticeShown.current) return;
    createdNoticeShown.current = true;
    toast.info(t("ClassWorkspacePage.templateCreatedMsg"));
  }, [createdTemplateId, toast, t]);
  const locked = item.status !== "planning";
  async function choose(candidate) {
    const invalidNode = candidate.nodes.find((node) => !courseNodeHasUsableSource(node));
    if (invalidNode) {
      toast.error(t("ClassWorkspacePage.unboundSourceMsg", { name: invalidNode.name, sourceLabel: invalidNode.sourceType === "custom" ? t("ClassWorkspacePage.baseImageLabel") : t("ClassWorkspacePage.machineTemplateLabel") }));
      return;
    }
    try {
      const result = await TeachingClassesService.selectCourse(item.id, candidate.versionId);
      onTemplate(candidate.id); onRefresh(result);
    } catch (error) { toast.error(error?.message ?? t("ClassWorkspacePage.applyEnvFailed")); }
  }
  // 鎖定後不該再擺一份選不了的清單：直接呈現已套用的環境與它的拓撲。
  if (locked) {
    /* 每位學生的規格合計 × 人數 = 開課要吃掉的配額 */
    const perStudentSpec = environmentSpecs(item.nodes);
    const classSpec = environmentSpecs(item.nodes, Math.max(1, item.students.length));
    return <div className={styles.stack}>
      <section className={styles.card}>
        <div className={styles.cardHeader}>
          <div><h2>{t("ClassWorkspacePage.appliedEnvTitle")}</h2><p>{item.course_environment ? `${item.course_environment.name} · v${item.course_environment.version}` : t("ClassWorkspacePage.appliedEnvFallback")}</p></div>
          <span className={styles.lockBadge}><MIcon name="lock" size={14} />{t("ClassWorkspacePage.settingsLockedLabel")}</span>
        </div>
        <div className={styles.envFacts}>
          <div><span>{t("ClassWorkspacePage.envFactPerStudent")}</span><strong>{t("ClassWorkspacePage.machineCountUnit", { count: item.nodes.length })}</strong></div>
          <div><span>{t("ClassWorkspacePage.envFactStudents")}</span><strong>{t("ClassWorkspacePage.peopleCountUnit", { count: item.students.length })}</strong></div>
          {/* 拓撲與對外服務在下面的畫布看得到，這裡只講開課會吃掉多少資源 */}
          <div><span>{t("ClassWorkspacePage.envFactPerStudentSpec")}</span><strong title={t("ClassWorkspacePage.envFactSpecValue", perStudentSpec)}>{t("ClassWorkspacePage.envFactSpecValue", perStudentSpec)}</strong></div>
          <div><span>{t("ClassWorkspacePage.envFactClassSpec")}</span><strong title={t("ClassWorkspacePage.envFactSpecValue", classSpec)}>{t("ClassWorkspacePage.envFactSpecValue", classSpec)}</strong></div>
        </div>
        {item.publications.length > 0 && <PublicationSummary item={item} />}
        {item.nodes.length > 0 && <TopologyPreview item={item} />}
      </section>
    </div>;
  }

  return <div className={styles.stack}>
    <section className={styles.card}>
      <div className={styles.cardHeader}><div><h2>{t("ClassWorkspacePage.chooseEnvTitle")}</h2><p>{t("ClassWorkspacePage.chooseEnvDesc")}</p></div><div className={styles.pageActions}><button type="button" className={styles.btnSecondary} onClick={() => navigate(`/course-template-management/new?returnTo=${encodeURIComponent(`/class-management/${item.id}/machines`)}`)}><MIcon name="add" size={16} />{t("ClassWorkspacePage.createNewEnvBtn")}</button></div></div>
      <div className={styles.envChoices}>{templates.map((candidate) => <EnvironmentChoice key={candidate.versionId} candidate={candidate} selected={template?.id === candidate.id} suggested={String(candidate.id) === String(createdTemplateId)} onSelect={() => choose(candidate)} />)}</div>
      {!templates.length && <div className={styles.emptyState}><p>{t("ClassWorkspacePage.noPublishedEnvNote")}</p></div>}
    </section>
    {item.nodes.length > 0 && <section className={styles.card}>
      <div className={styles.cardHeader}>
        <div><h2>{t("ClassWorkspacePage.topologyTitle")}</h2></div>
        <span className={styles.topologySummary}>{t("ClassWorkspacePage.perStudentUnit", { count: item.nodes.length })} · {item.topologyEdges.length ? t("ClassWorkspacePage.envFactLinkCount", { count: item.topologyEdges.length }) : t("ClassWorkspacePage.envFactNoLink")} · {item.publications.length ? t("ClassWorkspacePage.envFactPublicCount", { count: item.publications.length }) : t("ClassWorkspacePage.envFactNoPublic")}</span>
      </div>
      <TopologyPreview item={item} />
      {item.publications.length > 0 && <PublicationSummary item={item} />}
    </section>}
  </div>;
}

// 熱力圖：精簡卡片（座號 姓名＋數值＋細進度條）。使用中的依使用率上三階主題色，越忙越深；
// 高負載改淡紅；關機、暫無資料用虛線、點線框不上色。旁邊的「需要注意」清單再把該看的人列出來。
const HIGH_USAGE = 80;

function heatTone(state, usage) {
  if (state === "off") return "off";
  if (state !== "on" || usage === null) return "unknown";
  if (usage >= HIGH_USAGE) return "high";
  if (usage >= 60) return "busy";
  return usage >= 30 ? "mid" : "low";
}

const StudentHeatTile = memo(function StudentHeatTile({
  canWatch,
  email,
  index,
  machine,
  machineName,
  metricLabel,
  name,
  nodeName,
  nodeType,
  onWatch,
  state,
  student,
  usage,
  vmid,
  watching,
}) {
  const { t } = useTranslation("teaching");
  const seat = String(index + 1).padStart(2, "0");
  const tone = heatTone(state, usage);
  const detail = tone === "off" ? t("ClassWorkspacePage.offLabel") : tone === "unknown" ? t("ClassWorkspacePage.noDataLabel") : `${metricLabel} ${usage}%`;
  const machineLabel = vmid ? t("ClassWorkspacePage.vmFallbackName", { vmid }) : machineName || t("ClassWorkspacePage.notBuiltLabel");
  const clickable = canWatch && !watching;
  const value = tone === "off" ? t("ClassWorkspacePage.offLabel") : tone === "unknown" ? t("ClassWorkspacePage.noDataLabel") : `${usage}%`;
  // 不用 disabled：停用的按鈕在部分瀏覽器不顯示 title 提示，VM 編號等細節放在提示裡
  return <button
    type="button"
    className={`${styles.heatTile} ${styles[`heatTile_${tone}`] ?? ""} ${clickable ? styles.heatTileClickable : ""}`}
    title={`${seat} ${name}
${email ?? ""}
${nodeName} · ${machineLabel}
${detail}${canWatch ? `
${t("ClassWorkspacePage.clickToWatchLabel")}` : ""}`}
    aria-label={canWatch ? t("ClassWorkspacePage.openStudentMachineAria", { machine: nodeName, name: `${seat} ${name}` }) : `${seat} ${name}，${detail}`}
    aria-disabled={!clickable}
    onClick={() => { if (clickable) onWatch(student, machine, { name: nodeName, resource_type: nodeType }); }}
  >
    <span className={styles.heatTileHead}><span>{seat} {name}</span><b>{value}</b></span>
    {tone !== "off" && tone !== "unknown" && <span className={styles.heatBar}><i style={{ width: `${usage}%` }} /></span>}
  </button>;
});

/** 座位格旁的「需要注意」：高負載（由高到低）與上課中關機的學生；全班都關機時只說一句，不列整班 */
function HeatAttention({ cells, nodeName, nodeType, onWatch, watching }) {
  const { t } = useTranslation("teaching");
  const anyOn = cells.some((cell) => cell.state === "on");
  // 只有每一台都確定是關機才說「全部關機」；讀不到資料（還在載入、查詢失敗）不算
  const allOff = cells.length > 0 && cells.every((cell) => cell.state === "off");
  const high = cells.filter((cell) => heatTone(cell.state, cell.usage) === "high").sort((a, b) => b.usage - a.usage);
  const off = anyOn ? cells.filter((cell) => cell.state === "off") : [];
  const rows = [...high, ...off];
  return <aside className={styles.heatAttention} aria-label={t("ClassWorkspacePage.attentionTitle")}>
    <div className={styles.heatAttentionHead}><strong>{t("ClassWorkspacePage.attentionTitle")}</strong>{rows.length > 0 && <span>{rows.length}</span>}</div>
    {allOff
      ? <p>{t("ClassWorkspacePage.attentionAllOff")}</p>
      : rows.length === 0
        ? <p>{t("ClassWorkspacePage.attentionEmpty")}</p>
        : <ul>{rows.map(({ student, machine, index, state, usage }) => {
          const seat = String(index + 1).padStart(2, "0");
          const name = student.full_name || student.email || t("ClassWorkspacePage.studentFallbackName", { index: index + 1 });
          const canWatch = Boolean(machine?.vmid) && state === "on" && !watching;
          const value = state === "off" ? <em>{t("ClassWorkspacePage.offLabel")}</em> : <b>{usage}%</b>;
          return <li key={student.id}>{canWatch
            ? <button type="button" onClick={() => onWatch(student, machine, { name: nodeName, resource_type: nodeType })} aria-label={t("ClassWorkspacePage.openStudentMachineAria", { machine: nodeName, name })}><span>{seat}</span><strong>{name}</strong>{value}</button>
            : <div><span>{seat}</span><strong>{name}</strong>{value}</div>}</li>;
        })}</ul>}
  </aside>;
}

export function StudentMachines({ item }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const [selectedNodeId, setSelectedNodeId] = useState(() => String(item.nodes[0]?.id ?? ""));
  const [metric, setMetric] = useState("cpu");
  const [usageByVmid, setUsageByVmid] = useState({});
  const [usageStatus, setUsageStatus] = useState("loading");
  const [sources, setSources] = useState([]);
  const [watch, setWatch] = useState(null);
  const [terminal, setTerminal] = useState(null);
  const [watching, setWatching] = useState(false);
  const [broadcasting, setBroadcasting] = useState(false);
  const [broadcast, setBroadcast] = useState(null);
  const [power, setPower] = useState(null);
  const usageByVmidRef = useRef(null);
  const confirm = useConfirm();
  const classVmids = useMemo(
    () => [...item.students, ...(item.instructorMachine ? [item.instructorMachine] : [])]
      .flatMap((member) => member.machines.map((machine) => machine.vmid).filter(Boolean)),
    [item.students, item.instructorMachine],
  );

  const watchRef = useRef(null);
  const mountedRef = useRef(false);

  useEffect(() => {
    watchRef.current = watch;
  }, [watch]);

  /* 觀看（monitor）session 只屬於這個畫面的對話框：切到別的步驟、瀏覽器上一頁等直接卸載時
     不會經過 closeWatch，這裡補收掉，不要留一個佔住該 VM 的 session 在伺服器上。
     mountedRef 讓卸載後才建好的 session（createSession 還在路上就切走）也能在 openWatch 裡收掉。
     關閉分頁／重新整理不會觸發 React 卸載，另掛 pagehide 盡力收掉（送不出去時後端 30 秒閒置也會回收） */
  useEffect(() => {
    mountedRef.current = true;
    function stopCurrentWatch() {
      const current = watchRef.current;
      watchRef.current = null;
      if (current) ClassroomService.stopSession(current.sessionId).catch(() => {});
    }
    /* 頁面進 bfcache 再回來時 session 已結束，順手關掉對話框，不要留一個連不上的畫面 */
    function handlePageHide() {
      stopCurrentWatch();
      setWatch(null);
    }
    window.addEventListener("pagehide", handlePageHide);
    return () => {
      mountedRef.current = false;
      window.removeEventListener("pagehide", handlePageHide);
      stopCurrentWatch();
    };
  }, []);

  /* 廣播要跨頁面持續（老師切去別的步驟學生仍看得到），但重新整理或切回來時本地狀態已經沒了；
     從伺服器找回這個班級進行中的廣播，「停止廣播」按鈕才會再出現 */
  useEffect(() => {
    let active = true;
    ClassroomService.listSessions()
      .then((sessions) => {
        if (!active || !Array.isArray(sessions)) return;
        const live = sessions.find((session) => session.mode === "broadcast" && String(session.class_id) === String(item.id));
        if (live) setBroadcast((current) => current ?? live);
      })
      .catch(() => {});
    return () => { active = false; };
  }, [item.id]);

  useEffect(() => {
    if (!item.nodes.some((node) => String(node.id) === selectedNodeId)) {
      setSelectedNodeId(String(item.nodes[0]?.id ?? ""));
    }
  }, [item.nodes, selectedNodeId]);

  useEffect(() => {
    let active = true;
    let timer = null;
    usageByVmidRef.current = null;
    setUsageByVmid({});
    setUsageStatus("loading");

    async function loadUsage() {
      // 分頁隱藏時不打 API，只排下一輪
      if (document.hidden && usageByVmidRef.current !== null) {
        timer = window.setTimeout(loadUsage, 10_000);
        return;
      }
      try {
        const response = await TeachingClassesService.resourceUsage(item.id);
        if (!active) return;
        const nextUsage = mergeResourceUsageByVmid(usageByVmidRef.current ?? {}, response?.items);
        if (usageByVmidRef.current === null || nextUsage !== usageByVmidRef.current) {
          usageByVmidRef.current = nextUsage;
          startTransition(() => setUsageByVmid(nextUsage));
        }
        setUsageStatus("ready");
      } catch {
        if (active) setUsageStatus("error");
      } finally {
        if (active) timer = window.setTimeout(loadUsage, 10_000);
      }
    }

    loadUsage();
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [item.id]);

  useEffect(() => {
    let active = true;
    ClassroomService.listClassBroadcastSources(item.id)
      .then((result) => { if (active) setSources(result); })
      .catch(() => { if (active) setSources([]); });
    return () => { active = false; };
  }, [item.id]);

  const openWatch = useCallback(async (student, machine, node) => {
    if (!machine?.vmid) return;
    const machineName = node?.name || t("ClassWorkspacePage.vmFallbackName", { vmid: machine.vmid });
    if (String(node?.resource_type).toLowerCase() === "lxc") {
      setTerminal({ vmid: machine.vmid, name: `${student.full_name || student.email} · ${machineName}`, type: "lxc", status: "running" });
      return;
    }
    setWatching(true);
    try {
      const session = await ClassroomService.createSession({ vmid: machine.vmid, mode: "monitor", class_id: item.id });
      if (!mountedRef.current) {
        ClassroomService.stopSession(session.id).catch(() => {});
        return;
      }
      setWatch({
        sessionId: session.id,
        title: `${student.full_name || student.email} · ${machineName}`,
      });
    } catch (error) {
      toast.error(error?.message ?? t("ClassWorkspacePage.openWatchFailed"));
    } finally {
      setWatching(false);
    }
  }, [item.id, t, toast]);

  function closeWatch() {
    if (watch) ClassroomService.stopSession(watch.sessionId).catch(() => {});
    setWatch(null);
  }

  async function startBroadcast(vmid) {
    if (!vmid) return;
    setBroadcasting(true);
    try {
      const session = await ClassroomService.createSession({ vmid: Number(vmid), mode: "broadcast", class_id: item.id });
      setBroadcast(session);
      toast.success(t("ClassWorkspacePage.broadcastStartedMsg"));
    } catch (error) {
      toast.error(error?.message ?? t("ClassWorkspacePage.startBroadcastFailed"));
    } finally {
      setBroadcasting(false);
    }
  }

  async function stopBroadcast() {
    if (!broadcast) return;
    setBroadcasting(true);
    try {
      await ClassroomService.stopSession(broadcast.id);
      setBroadcast(null);
      toast.success(t("ClassWorkspacePage.broadcastEndedMsg"));
    } catch (error) {
      toast.error(error?.message ?? t("ClassWorkspacePage.stopBroadcastFailed"));
    } finally {
      setBroadcasting(false);
    }
  }

  /* 整班開關機：和排程器一樣分小批送，整班一次送會超過後端上限與代理逾時 */
  async function runClassPower(action) {
    if (!classVmids.length || power) return;
    const label = action === "start" ? t("ClassWorkspacePage.classPowerStartBtn") : t("ClassWorkspacePage.classPowerShutdownBtn");
    if (action === "shutdown") {
      const ok = await confirm({
        title: t("ClassWorkspacePage.classPowerShutdownConfirmTitle"),
        message: t("ClassWorkspacePage.classPowerShutdownConfirmMessage", { count: classVmids.length }),
        confirmText: label,
        danger: true,
      });
      if (!ok) return;
    }
    setMessage("");
    setPower({ action, done: 0, total: classVmids.length });
    try {
      const result = await ResourcesService.batchActionInChunks(classVmids, action, {
        onProgress: ({ done, total }) => setPower({ action, done, total }),
      });
      // 操作回饋一律走 toast：部分失敗用 warning，全部成功用 success
      if (result.failed) toast.warning(t("ClassWorkspacePage.classPowerPartialMsg", { label, succeeded: result.succeeded, failed: result.failed }));
      else toast.success(t("ClassWorkspacePage.classPowerDoneMsg", { label, count: result.succeeded }));
    } finally {
      setPower(null);
    }
  }

  const selectedNode = item.nodes.find((node) => String(node.id) === selectedNodeId) ?? item.nodes[0];
  const cells = useMemo(() => item.students.map((student, index) => {
    const machine = student.machines.find((candidate) => String(candidate.machine_node_id) === String(selectedNode?.id));
    const runtime = usageByVmid[String(machine?.vmid)] ?? null;
    const state = machineRuntimeState(machine, runtime);
    return {
      student,
      machine,
      index,
      state,
      usage: state === "on" ? usageForMetric(runtime, metric) : null,
    };
  }), [item.students, metric, selectedNode?.id, usageByVmid]);

  const activeCells = cells.filter((cell) => cell.state === "on");
  const measuredCells = activeCells.filter((cell) => cell.usage !== null);
  const average = measuredCells.length ? Math.round(measuredCells.reduce((total, cell) => total + cell.usage, 0) / measuredCells.length) : null;
  const highUsage = measuredCells.filter((cell) => cell.usage >= HIGH_USAGE).length;
  const metricInfo = RESOURCE_METRICS[metric];
  const badgeText = usageStatus === "loading" ? t("ClassWorkspacePage.loadingLiveDataLabel") : usageStatus === "error" ? t("ClassWorkspacePage.updateFailedLabel") : t("ClassWorkspacePage.every10SecUpdateLabel");

  return <div className={styles.stack}>
    <section className={`${styles.card} ${styles.heatmapCard}`}>
      <div className={styles.heatmapHeader}>
        <div><h2>{t("ClassWorkspacePage.heatmapTitle")}</h2><p>{t("ClassWorkspacePage.heatmapDesc")}</p></div>
        <span className={usageStatus === "error" ? styles.prototypeBadge : styles.liveBadge}><MIcon name={usageStatus === "error" ? "sync_problem" : "sensors"} size={15} />{badgeText}</span>
      </div>

      <div className={styles.broadcastTools}><MIcon name="sensors" size={18} /><strong>{t("ClassWorkspacePage.broadcastDemoLabel")}</strong><div className={styles.broadcastControls}>{broadcast ? <><span>{t("ClassWorkspacePage.broadcastInProgress")}</span><button type="button" className={styles.btnSecondary} disabled={broadcasting} onClick={stopBroadcast}>{t("ClassWorkspacePage.stopBroadcastBtn")}</button></> : <select disabled={broadcasting || !sources.length} defaultValue="" onChange={(event) => { startBroadcast(event.target.value); event.target.value = ""; }}><option value="">{sources.length ? t("ClassWorkspacePage.selectRunningVmOption") : t("ClassWorkspacePage.noBroadcastVmOption")}</option>{sources.map((source) => <option key={source.vmid} value={source.vmid}>{source.name || t("ClassWorkspacePage.vmFallbackName", { vmid: source.vmid })}</option>)}</select>}</div></div>
      <div className={styles.broadcastTools}><MIcon name="power_settings_new" size={18} /><strong>{t("ClassWorkspacePage.classPowerLabel", { count: classVmids.length })}</strong><div className={styles.broadcastControls}><button type="button" className={styles.btnSecondary} disabled={!!power || !classVmids.length || item.status !== "active"} onClick={() => runClassPower("start")}><MIcon name="play_arrow" size={16} />{t("ClassWorkspacePage.classPowerStartBtn")}</button><button type="button" className={styles.btnDangerOutline} disabled={!!power || !classVmids.length || item.status !== "active"} onClick={() => runClassPower("shutdown")}><MIcon name="power_settings_new" size={16} />{t("ClassWorkspacePage.classPowerShutdownBtn")}</button>{power ? <span>{t("ClassWorkspacePage.classPowerProgress", { done: power.done, total: power.total })}</span> : <span>{t("ClassWorkspacePage.classPowerHint")}</span>}</div></div>

      <div className={styles.heatmapToolbar}>
        <div className={styles.machineTabs} role="tablist" aria-label={t("ClassWorkspacePage.selectMachineAria")}>
          {item.nodes.map((node, index) => {
            const selected = String(node.id) === String(selectedNode?.id);
            return <button key={node.id} type="button" role="tab" aria-selected={selected} className={selected ? styles.machineTabActive : ""} onClick={() => setSelectedNodeId(String(node.id))}>
              <span><MIcon name={node.resource_type === "lxc" ? "terminal" : "dns"} size={17} /></span>
              <span><small>{t("ClassWorkspacePage.machineIndexLabel", { index: String(index + 1).padStart(2, "0") })}</small><strong>{node.name}</strong></span>
            </button>;
          })}
        </div>
        <SegmentedControl
          className={styles.metricSwitch}
          ariaLabel={t("ClassWorkspacePage.selectMetricAria")}
          value={metric}
          onChange={setMetric}
          options={Object.entries(RESOURCE_METRICS).map(([key, info]) => ({ value: key, label: info.label, icon: info.icon }))}
        />
      </div>

      {selectedNode && item.students.length ? <>
        <div className={styles.heatmapSummary}>
          <dl><div><dt>{t("ClassWorkspacePage.poweredOnLabel")}</dt><dd>{activeCells.length}<small>/{cells.length}</small></dd></div><div><dt>{t("ClassWorkspacePage.averageLabel")}</dt><dd>{average ?? "—"}{average !== null && <small>%</small>}</dd></div><div><dt>{t("ClassWorkspacePage.highLoadLabel")}</dt><dd>{highUsage}<small>{t("ClassWorkspacePage.highLoadPeopleUnit")}</small></dd></div></dl>
        </div>

        <div className={styles.heatBoard}>
          <div className={styles.heatTiles} aria-label={`${selectedNode.name} ${metricInfo.label} ${t("ClassWorkspacePage.usageRateLabel")}`}>
            {cells.map(({ student, machine, index, state, usage }) => <StudentHeatTile
              key={student.id}
              canWatch={Boolean(machine?.vmid) && state === "on"}
              email={student.email}
              index={index}
              machine={machine}
              machineName={machine?.name}
              metricLabel={metricInfo.label}
              name={student.full_name || student.email || t("ClassWorkspacePage.studentFallbackName", { index: index + 1 })}
              nodeName={selectedNode.name}
              nodeType={selectedNode.resource_type}
              onWatch={openWatch}
              state={state}
              student={student}
              usage={usage}
              vmid={machine?.vmid}
              watching={watching}
            />)}
          </div>
          <HeatAttention cells={cells} nodeName={selectedNode.name} nodeType={selectedNode.resource_type} onWatch={openWatch} watching={watching} />
        </div>

        <div className={styles.heatLegend} aria-label={t("ClassWorkspacePage.heatLegendAria")}>
          <span className={styles.heatLegendScale}>{t("ClassWorkspacePage.usageRateLabel")}<i className={styles.heatTile_low} /><i className={styles.heatTile_mid} /><i className={styles.heatTile_busy} /></span>
          <span><i className={styles.heatTile_high} />{t("ClassWorkspacePage.highLoadLegend", { percent: HIGH_USAGE })}</span>
          <span><i className={styles.heatTile_off} />{t("ClassWorkspacePage.offLabel")}</span>
          <span><i className={styles.heatTile_unknown} />{t("ClassWorkspacePage.noDataLabel")}</span>
        </div>
      </> : <EmptyState icon="grid_view" title={selectedNode ? t("ClassWorkspacePage.noStudentsInClassTitle") : t("ClassWorkspacePage.noClassroomMachinesTitle")} />}
    </section>
    {watch && <ClassroomWatchDialog sessionId={watch.sessionId} title={watch.title} canControl onClose={closeWatch} />}
    {terminal && <TerminalDialog resource={terminal} onClose={() => setTerminal(null)} />}
  </div>;
}

function LockedFeature({ section }) {
  const { t } = useTranslation("teaching");
  const label = section === "ai" ? t("ClassWorkspacePage.tabAiLabel") : section === "classroom" ? t("ClassWorkspacePage.tabClassroomLabel") : t("ClassWorkspacePage.studentMachinesLabel");
  return <section className={styles.lockedFeature}><span><MIcon name="lock" size={22} /></span><div><h2>{t("ClassWorkspacePage.notYetAvailableTitle", { label })}</h2><p>{t("ClassWorkspacePage.lockedFeatureDesc")}</p></div></section>;
}

export default function ClassWorkspacePage() {
  const { t } = useTranslation("teaching");
  const confirm = useConfirm();
  const toast = useToast();
  const { classId, section } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [item, setItem] = useState(null);
  // 從班級連結（沒帶分頁）進來時：可以上課的班級直接進課堂監看；
  // 還在設定、建機、已封存的班級要看狀態面板，停在班級總覽。
  // 總覽分頁自己有 /overview 網址，點回總覽不會又被帶走。
  const landOnMonitor = !section && item?.status === "active";
  const tab = section === "classroom" ? "progress" : section ?? (landOnMonitor ? "progress" : "overview");
  const [loading, setLoading] = useState(true);
  const [provisioning, setProvisioning] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const scheduleDialog = useDialogPresence(scheduleOpen);
  const [extendOpen, setExtendOpen] = useState(false);
  const extendDialog = useDialogPresence(extendOpen);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  const [templateId, setTemplateId] = useState("");
  const [templates, setTemplates] = useState([]);
  const template = templates.find((row) => row.id === templateId);

  function refresh(result) {
    const normalized = normalizeClass(result);
    setItem(normalized);
    if (normalized.course_environment?.id) setTemplateId(String(normalized.course_environment.id));
  }
  useEffect(() => {
    let active = true;
    TeachingClassesService.get(classId).then((result) => active && refresh(result)).catch((reason) => active && toast.error(reason?.message ?? t("ClassWorkspacePage.loadClassFailed"))).finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [classId, toast, t]);
  useEffect(() => {
    let active = true;
    CourseEnvironmentsService.listPublished()
      .then((rows) => active && setTemplates(rows))
      .catch((reason) => active && toast.error(reason?.message ?? t("ClassWorkspacePage.loadPublishedCoursesFailed")));
    return () => { active = false; };
  }, [toast, t]);
  // 直接顯示課堂監看之外，網址也換成 /progress，重新整理或加書籤才會停在同一頁
  useEffect(() => {
    if (landOnMonitor) navigate(`/class-management/${classId}/progress`, { replace: true });
  }, [landOnMonitor, classId, navigate]);
  useEffect(() => {
    if (!menuOpen) return undefined;
    function dismiss(event) {
      if (event.type === "keydown" && event.key !== "Escape") return;
      if (event.type === "mousedown" && menuRef.current?.contains(event.target)) return;
      setMenuOpen(false);
    }
    window.addEventListener("mousedown", dismiss);
    window.addEventListener("keydown", dismiss);
    return () => { window.removeEventListener("mousedown", dismiss); window.removeEventListener("keydown", dismiss); };
  }, [menuOpen]);
  useEffect(() => {
    if (!item || !["pending_review", "provisioning"].includes(item.status)) return undefined;
    // 進度本身是純讀取；每五輪（15 秒）才請後端把建機結果寫回班級，
    // 那一支會實際呼叫 PVE，不適合每三秒打一次。
    let ticks = 0;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      ticks += 1;
      const request = ticks % 5 === 1
        ? TeachingClassesService.reconcile(item.id)
        : TeachingClassesService.provisionStatus(item.id);
      request.then(refresh).catch(() => {});
    }, 3000);
    return () => window.clearInterval(timer);
  }, [item?.id, item?.status]);

  async function provision() {
    const ok = await confirm({
      title: t("ClassWorkspacePage.submitReviewConfirmTitle"),
      message: t("ClassWorkspacePage.submitReviewConfirmMessage"),
      confirmText: t("ClassWorkspacePage.submitLabel"),
    });
    if (!ok) return;
    setProvisioning(true);
    try { refresh(await TeachingClassesService.provision(classId)); toast.success(t("ClassWorkspacePage.provisionSubmittedMsg")); }
    catch (reason) { toast.error(reason?.message ?? t("ClassWorkspacePage.provisionFailedMsg")); }
    finally { setProvisioning(false); }
  }

  async function retryFailed() {
    setRecovering(true);
    try {
      const result = await TeachingClassesService.retryFailed(classId);
      refresh(result);
      if (result.status === "active") toast.success(t("ClassWorkspacePage.topologyReappliedMsg"));
      else toast.info(t("ClassWorkspacePage.retryPartialMsg"));
    } catch (reason) { toast.error(reason?.message ?? t("ClassWorkspacePage.retryFailedMsg")); }
    finally { setRecovering(false); }
  }

  async function resetFailed() {
    const ok = await confirm({
      title: t("ClassWorkspacePage.backToEditConfirmTitle"),
      message: t("ClassWorkspacePage.resetConfirmMessage"),
      confirmText: t("ClassWorkspacePage.releaseAndReturnLabel"),
      danger: true,
    });
    if (!ok) return;
    setRecovering(true);
    try {
      refresh(await TeachingClassesService.resetFailed(classId));
      toast.success(t("ClassWorkspacePage.resetSuccessMsg"));
    } catch (reason) { toast.error(reason?.message ?? t("ClassWorkspacePage.resetFailedMsg")); }
    finally { setRecovering(false); }
  }

  async function extendClass(endDate) {
    setLifecycleBusy(true);
    try {
      refresh(await TeachingClassesService.extend(classId, endDate));
      setExtendOpen(false);
      toast.success(t("ClassWorkspacePage.extendedMsg", { endDate }));
    } catch (reason) { toast.error(reason?.message ?? t("ClassWorkspacePage.extendFailedMsg")); }
    finally { setLifecycleBusy(false); }
  }

  async function archiveClass() {
    const ok = await confirm({
      title: t("ClassWorkspacePage.archiveConfirmTitle"),
      message: t("ClassWorkspacePage.archiveConfirmMessage"),
      confirmText: t("ClassWorkspacePage.archiveAndReclaimBtn"),
      danger: true,
    });
    if (!ok) return;
    setLifecycleBusy(true);
    try {
      const result = await TeachingClassesService.archive(classId);
      refresh(result.class);
      const failed = result.reclaim?.failed?.length ?? 0;
      if (failed) toast.error(t("ClassWorkspacePage.archivedWithFailuresMsg", { count: failed }));
      else toast.success(t("ClassWorkspacePage.archivedSuccessMsg"));
    } catch (reason) { toast.error(reason?.message ?? t("ClassWorkspacePage.archiveFailedMsg")); }
    finally { setLifecycleBusy(false); }
  }

  async function reclaimClass() {
    setLifecycleBusy(true);
    try {
      const result = await TeachingClassesService.reclaim(classId, { force: true });
      refresh(await TeachingClassesService.get(classId));
      const failed = result.failed?.length ?? 0;
      if (failed) toast.error(t("ClassWorkspacePage.reclaimStillFailedMsg", { count: failed }));
      else toast.success(t("ClassWorkspacePage.reclaimSuccessMsg"));
    } catch (reason) { toast.error(reason?.message ?? t("ClassWorkspacePage.reclaimFailedMsg")); }
    finally { setLifecycleBusy(false); }
  }

  if (loading) return <LoadingState fullPage text={t("ClassWorkspacePage.loadingClassText")} />;
  if (!item) return <div className={styles.page}><button type="button" className={styles.backLink} onClick={() => navigate("/class-management")}><MIcon name="arrow_back" size={18} />{t("ClassWorkspacePage.backToClassManagementBtn")}</button><p className={styles.errorMessage}>{t("ClassWorkspacePage.classNotFoundText")}</p></div>;
  const postUnavailable = POST_ACTIVE_TABS.includes(tab) && item.status !== "active";
  // 已封存的班級沒有任何可用選項，就不要留一顆會打開空選單的按鈕；
  // 「重試回收」已經是狀態面板的行動。
  const canEditSchedule = item.status === "planning";
  const canManageLifecycle = item.status !== "archived";

  return <div className={styles.page}>
    <PageHeader
      eyebrow={item.location ? `${item.term} · ${item.location}` : item.term}
      title={item.name}
      subtitle={t("ClassWorkspacePage.subtitleTemplate", { students: item.students.length, weeks: item.weeks.length, weekday: t(["ClassWorkspacePage.weekdayShortMon", "ClassWorkspacePage.weekdayShortTue", "ClassWorkspacePage.weekdayShortWed", "ClassWorkspacePage.weekdayShortThu", "ClassWorkspacePage.weekdayShortFri", "ClassWorkspacePage.weekdayShortSat", "ClassWorkspacePage.weekdayShortSun"][item.weekday]), start: item.startTime, end: item.endTime })}
    >
      <div className={styles.pageActions}>
        <button type="button" className={`${styles.btnSecondary} ${styles.backBtn}`} onClick={() => navigate("/class-management")}><MIcon name="arrow_back" size={18} />{t("ClassWorkspacePage.backToClassManagementBtn")}</button>
        {(canEditSchedule || canManageLifecycle) && <div className={styles.headerMenuWrap} ref={menuRef}>
          <button type="button" className={`${styles.iconBtn} ${styles.headerMenuBtn}`} aria-haspopup="menu" aria-expanded={menuOpen} aria-label={t("ClassWorkspacePage.moreActionsAria")} onClick={() => setMenuOpen((open) => !open)}><MIcon name="more_horiz" size={19} /></button>
          {menuOpen && <div className={styles.headerMenu} role="menu">
            {canEditSchedule && <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); setScheduleOpen(true); }}><MIcon name="edit_calendar" size={16} />{t("ClassWorkspacePage.editScheduleBtn")}</button>}
            {canManageLifecycle && <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); setExtendOpen(true); }}><MIcon name="event_repeat" size={16} />{t("ClassWorkspacePage.extendBtn")}</button>}
            {canManageLifecycle && <><hr /><button type="button" role="menuitem" className={styles.headerMenuRisky} disabled={lifecycleBusy} onClick={() => { setMenuOpen(false); archiveClass(); }}><MIcon name="archive" size={16} />{t("ClassWorkspacePage.archiveAndReclaimBtn")}</button></>}
          </div>}
        </div>}
      </div>
    </PageHeader>
    <ClassWorkflowStepper item={item} activeKey={tab} onSelect={(key) => navigate(`/class-management/${classId}/${key}`)} />
    <div className={styles.workspaceContent}>
      {tab === "overview" && <Overview item={item} template={template} onProvision={provision} onNavigate={(target) => navigate(`/class-management/${classId}/${target}`)} onRetry={retryFailed} onReset={resetFailed} onReclaim={reclaimClass} provisioning={provisioning} recovering={recovering} lifecycleBusy={lifecycleBusy} />}
      {tab === "students" && <Students item={item} onRefresh={refresh} />}
      {tab === "weekly" && <WeeklyContent item={item} onRefresh={refresh} />}
      {tab === "machines" && <Machines item={item} templates={templates} template={template} onRefresh={refresh} onTemplate={setTemplateId} createdTemplateId={location.state?.createdTemplateId} />}
      {postUnavailable && <LockedFeature section={tab} />}
      {tab === "progress" && !postUnavailable && <StudentMachines item={item} />}
      {!TABS.some(([key]) => key === tab) && <LockedFeature section={tab} />}
    </div>
    {scheduleDialog.open && <ClassCreateDialog item={item} closing={scheduleDialog.closing} onClose={() => setScheduleOpen(false)} onUpdated={(result) => { refresh(result); setScheduleOpen(false); toast.success(t("ClassWorkspacePage.scheduleUpdatedMsg")); }} />}
    {extendDialog.open && <ExtendDialog item={item} closing={extendDialog.closing} busy={lifecycleBusy} onClose={() => setExtendOpen(false)} onExtend={extendClass} />}
  </div>;
}
