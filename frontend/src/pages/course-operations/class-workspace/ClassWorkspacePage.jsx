import { memo, startTransition, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Background, Handle, MarkerType, Position, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import EmptyState from "../../../components/EmptyState/EmptyState";
import ClassroomWatchDialog from "../../../components/Classroom/ClassroomWatchDialog";
import TerminalDialog from "../../personal/resources/TerminalDialog";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import { ClassroomService } from "../../../services/classroom";
import { courseNodeHasUsableSource, CourseEnvironmentsService } from "../../../services/courseEnvironments";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { formatDate, formatTime } from "../../../utils/formatDate";
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

const POST_ACTIVE_TABS = ["progress", "ai"];

const TABS = [
  ["overview", "dashboard", "ClassWorkspacePage.tabOverviewLabel", "ClassWorkspacePage.tabOverviewHint"],
  ["students", "groups", "ClassWorkspacePage.tabStudentsLabel", "ClassWorkspacePage.tabStudentsHint"],
  ["machines", "account_tree", "ClassWorkspacePage.tabMachinesLabel", "ClassWorkspacePage.tabMachinesHint"],
  ["weekly", "calendar_view_week", "ClassWorkspacePage.tabWeeklyLabel", "ClassWorkspacePage.tabWeeklyHint"],
  ["progress", "cast_for_education", "ClassWorkspacePage.tabProgressLabel", "ClassWorkspacePage.tabProgressHint"],
  ["ai", "auto_awesome", "ClassWorkspacePage.tabAiLabel", "ClassWorkspacePage.tabAiHint"],
];

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
  return <div className={`${styles.createDialogOverlay} ${closing ? styles.createDialogOverlayOut : ""}`} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className={styles.createDialog} role="dialog" aria-modal="true" aria-labelledby="extend-class-title">
      <header className={styles.createDialogHeader}>
        <h2 id="extend-class-title">{t("ClassWorkspacePage.extendDialogTitle")}</h2>
        <button type="button" className={styles.iconBtn} aria-label={t("ClassWorkspacePage.closeAriaLabel")} disabled={busy} onClick={onClose}><MIcon name="close" size={19} /></button>
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
    </section>
  </div>;
}

function machineSummary(item, t) {
  if (item.status === "planning") {
    if (!item.course_environment || !item.nodes.length) return t("ClassWorkspacePage.machineSummaryNoEnv");
    return t("ClassWorkspacePage.machineSummaryPlanned", { total: item.students.length * item.nodes.length });
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
  const weekday = t(["ClassWorkspacePage.weekdayFullMon", "ClassWorkspacePage.weekdayFullTue", "ClassWorkspacePage.weekdayFullWed", "ClassWorkspacePage.weekdayFullThu", "ClassWorkspacePage.weekdayFullFri", "ClassWorkspacePage.weekdayFullSat", "ClassWorkspacePage.weekdayFullSun"][item.weekday]);
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
    title = t("ClassWorkspacePage.classReadyTitle"); description = t("ClassWorkspacePage.machinesCompleteDesc", { ready: item.readyMachines, total: item.totalMachines }); actionLabel = t("ClassWorkspacePage.viewStudentMachinesBtn"); actionIcon = "checklist"; action = () => onNavigate("progress");
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
        <div><span>{t("ClassWorkspacePage.setupProgressLabel", { completed })}</span><h2>{title}</h2><p>{description}</p></div>
        {actionLabel && <div className={styles.setupActions}>{secondaryAction && <button type="button" className={styles.btnSecondary} disabled={recovering} onClick={secondaryAction}>{t("ClassWorkspacePage.unlockAndEditBtn")}</button>}<button type="button" className={styles.btnPrimary} disabled={provisioning || actionDisabled} onClick={action}><MIcon name={actionIcon} size={17} />{actionLabel}</button></div>}
      </div>
      {showChecklist && <div className={styles.setupChecklist}>{setupItems.map(([done, label, note]) => <div key={label} className={done ? styles.setupItemDone : styles.setupItemTodo}><span><MIcon name={done ? "check" : "radio_button_unchecked"} size={17} /></span><div><strong>{label}</strong><small>{note}</small></div><em>{done ? t("ClassWorkspacePage.doneLabel") : t("ClassWorkspacePage.pendingLabel")}</em></div>)}</div>}
      {showJobs && <div className={styles.jobGrid}>{item.jobs.map((job, index) => <article key={job.id}><span>{t("ClassWorkspacePage.nodeIndexLabel", { index: index + 1 })}</span><strong>{JOB_STATUS_KEYS[job.status] ? t(JOB_STATUS_KEYS[job.status]) : job.status}</strong><small>{t("ClassWorkspacePage.jobResultSummary", { done: job.done, total: job.total, failed: job.failed_count })}</small></article>)}</div>}
    </section>
    <div className={styles.overviewDetailGrid}>
      <section className={styles.overviewInfoCard}>
        <div className={styles.overviewCardHeader}><h2>{weekLabel}</h2><button type="button" onClick={() => onNavigate("weekly")}>{t("ClassWorkspacePage.viewAllWeeksBtn")}<MIcon name="arrow_forward" size={15} /></button></div>
        {currentWeek ? <div className={styles.currentWeekSummary}><div><span>{t("ClassWorkspacePage.weekNumberLabel", { week: currentWeek.week })}</span><strong>{currentWeek.title || t("ClassWorkspacePage.noTopicSet")}</strong><small>{currentWeek.date} · {item.startTime}–{item.endTime}</small></div><span className={styles.weekFileCount}><MIcon name="attach_file" size={15} />{t("ClassWorkspacePage.fileCountUnit", { count: currentWeek.files.length })}</span></div> : <EmptyState icon="event" title={t("ClassWorkspacePage.noWeeksTitle")} />}
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
            <span>{weekday} {item.startTime}–{item.endTime}{item.location ? ` · ${item.location}` : ""} · {t("ClassWorkspacePage.bootLeadShort", { minutes: item.bootLeadMinutes })}</span>
            <MIcon name="expand_more" size={19} />
          </summary>
          <div className={styles.classFacts}>
            <div><span>{t("ClassWorkspacePage.termLabel")}</span><strong>{item.term}</strong></div><div><span>{t("ClassWorkspacePage.locationLabel")}</span><strong>{item.location || t("ClassWorkspacePage.locationUnset")}</strong></div>
            <div><span>{t("ClassWorkspacePage.fixedScheduleLabel")}</span><strong>{weekday} {item.startTime}–{item.endTime}</strong></div><div><span>{t("ClassWorkspacePage.coursePeriodLabel")}</span><strong>{item.startDate}–{item.endDate}</strong></div>
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
    const values = emails.split(/[\n,;]/).map((value) => value.trim()).filter(Boolean);
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
      if (result.not_found?.length) notices.push(t("ClassWorkspacePage.notFoundList", { list: result.not_found.join("、") }));
      if (result.invalid_role?.length) notices.push(t("ClassWorkspacePage.invalidRoleList", { list: result.invalid_role.join("、") }));
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
      {item.students.length ? <div className={styles.memberList}>{item.students.map((student) => {
        const ready = student.machines.filter((machine) => machine.status === "completed").length;
        return <article className={styles.memberRow} key={student.id}>
          <div className={styles.memberIdentity}><strong>{student.full_name || student.email}</strong><span>{student.email}</span></div>
          <span>{student.machines.length ? student.machines.map((machine) => machine.vmid ?? "—").join("、") : "—"}</span>
          <span className={`${styles.memberMachineState} ${ready === item.nodes.length && item.nodes.length ? styles.memberReady : ""}`}>{item.nodes.length ? t("ClassWorkspacePage.readyCountLabel", { ready, total: item.nodes.length }) : t("ClassWorkspacePage.notBuiltLabel")}</span>
          <span>{formatDate(student.joined_at)}</span>
          {!locked ? <button type="button" className={styles.memberRemove} aria-label={t("ClassWorkspacePage.removeStudentAriaLabel")} onClick={() => remove(student.id)}><MIcon name="person_remove" size={17} /></button> : <span />}
        </article>;
      })}</div> : <EmptyState icon="group_add" title={t("ClassWorkspacePage.emptyStudentsTitle")} />}
    </section>

    {addDialog.open && <div className={`${styles.createDialogOverlay} ${addDialog.closing ? styles.createDialogOverlayOut : ""}`} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) setShowAdd(false); }}><section className={`${styles.createDialog} ${styles.studentDialog}`} role="dialog" aria-modal="true" aria-labelledby="add-student-title"><header className={styles.createDialogHeader}><h2 id="add-student-title">{t("ClassWorkspacePage.addStudentsBtn")}</h2><button type="button" className={styles.iconBtn} aria-label={t("ClassWorkspacePage.closeAriaLabel")} onClick={() => setShowAdd(false)}><MIcon name="close" size={19} /></button></header><form onSubmit={add}><div className={styles.studentDialogBody}><label className={styles.field}><span>{t("ClassWorkspacePage.emailFieldLabel")}</span><textarea ref={emailsInputRef} className={emailsInvalid ? styles.fieldInvalid : undefined} rows={6} value={emails} onChange={(event) => { setEmails(event.target.value); setEmailsInvalid(false); }} placeholder="student01@example.edu&#10;student02@example.edu" autoFocus /></label></div><footer className={styles.createDialogFooter}><button type="button" className={styles.btnSecondary} onClick={() => setShowAdd(false)}>{t("ClassWorkspacePage.cancelBtn")}</button><button type="submit" className={styles.btnPrimary} disabled={busy}>{busy ? t("ClassWorkspacePage.addingLabel") : t("ClassWorkspacePage.addStudentsBtn")}</button></footer></form></section></div>}
  </div>;
}

function WeeklyContent({ item, onRefresh }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const [weeks, setWeeks] = useState(item.weeks);
  const [saving, setSaving] = useState(false);
  const [uploadingWeek, setUploadingWeek] = useState("");
  const locked = item.status === "archived";
  useEffect(() => setWeeks(item.weeks), [item.weeks]);
  function update(id, key, value) { setWeeks((rows) => rows.map((row) => row.id === id ? { ...row, [key]: value } : row)); }
  function mergeUploadedFiles(result) {
    const serverWeeks = normalizeClass(result).weeks;
    setWeeks((current) => serverWeeks.map((serverWeek) => ({ ...serverWeek, title: current.find((week) => week.date === serverWeek.date)?.title ?? serverWeek.title })));
  }
  async function upload(weekId, fileList) {
    const files = Array.from(fileList ?? []);
    if (!files.length) return;
    setUploadingWeek(weekId);
    try {
      let result;
      for (const file of files) result = await TeachingClassesService.uploadWeekFile(item.id, weekId, file);
      if (result) mergeUploadedFiles(result);
      toast.success(t("ClassWorkspacePage.uploadedFilesCount", { count: files.length }));
    } catch (error) { toast.error(error?.message ?? t("ClassWorkspacePage.uploadFailed")); }
    finally { setUploadingWeek(""); }
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
      const result = await TeachingClassesService.replaceWeeks(item.id, weeks.map((week) => ({ week_number: week.week, session_date: week.date, title: week.title.trim(), target_node_key: week.target || null, status: week.status, files: week.files.map((file) => ({ filename: file.filename, storage_key: file.storage_key ?? null, target_path: file.target_path ?? null })) })));
      onRefresh(result); toast.success(t("ClassWorkspacePage.weeklySavedMsg"));
    } catch (error) { toast.error(error?.message ?? t("ClassWorkspacePage.saveFailed")); }
    finally { setSaving(false); }
  }
  return <div className={styles.stack}>
    <section className={styles.card}>
      <div className={styles.cardHeader}><div><h2>{t("ClassWorkspacePage.weeklyContentHeader", { count: weeks.length })}</h2><p>{t("ClassWorkspacePage.weeklyPublishHint")}</p></div><span className={styles.weekVisibleCount}>{t("ClassWorkspacePage.weeksVisibleCount", { count: weeks.filter((week) => ["published", "completed"].includes(week.status)).length })}</span></div>
      <div className={styles.weekRows}>
        <div className={styles.weekRowsHead}><span>{t("ClassWorkspacePage.weekColWeek")}</span><span>{t("ClassWorkspacePage.topicTaskLabel")}</span><span>{t("ClassWorkspacePage.weekColMachine")}</span><span>{t("ClassWorkspacePage.taskFilesLabel")}</span><span>{t("ClassWorkspacePage.weekColVisible")}</span></div>
        {weeks.map((week) => {
          const published = ["published", "completed"].includes(week.status);
          return <article key={week.id}>
            <div className={styles.weekDate}><strong>{t("ClassWorkspacePage.weekNumberLabel", { week: week.week })}</strong><span>{week.date}</span></div>
            <input className={styles.weekTitleInput} disabled={locked} value={week.title} onChange={(event) => update(week.id, "title", event.target.value)} placeholder={t("ClassWorkspacePage.topicPlaceholder")} />
            <select className={styles.weekMachineSelect} disabled={locked} value={week.target} onChange={(event) => update(week.id, "target", event.target.value)} aria-label={t("ClassWorkspacePage.weekMachineAria", { week: week.week })}>
              <option value="">{t("ClassWorkspacePage.weekMachineAll")}</option>
              {item.nodes.map((node) => <option key={node.node_key} value={node.node_key}>{node.name}</option>)}
            </select>
            <div className={styles.weekFileList}>
              {week.files.map((file) => <span className={styles.weekFileChip} key={file.id ?? file.filename}><MIcon name="description" size={15} /><b>{file.filename}</b>{!locked && file.id && <button type="button" disabled={uploadingWeek === week.id} aria-label={t("ClassWorkspacePage.removeFileAria", { filename: file.filename })} onClick={() => removeFile(week.id, file)}><MIcon name="close" size={14} /></button>}</span>)}
              {!locked && <label className={styles.weekUploadButton}><input type="file" multiple disabled={uploadingWeek === week.id} onChange={(event) => { upload(week.id, event.target.files); event.target.value = ""; }} /><MIcon name="upload_file" size={16} />{uploadingWeek === week.id ? t("ClassWorkspacePage.uploadingLabel") : t("ClassWorkspacePage.uploadFileBtn")}</label>}
            </div>
            <button type="button" disabled={locked || !week.title.trim()} title={!week.title.trim() ? t("ClassWorkspacePage.publishNeedsTopicHint") : undefined} className={`${styles.weekPublishButton} ${published ? styles.weekPublished : ""}`} onClick={() => update(week.id, "status", published ? "draft" : "published")}><MIcon name={published ? "visibility" : "visibility_off"} size={15} />{published ? t("ClassWorkspacePage.publishedShortLabel") : t("ClassWorkspacePage.draftKeepLabel")}</button>
          </article>;
        })}
      </div>
      {!locked && <div className={styles.actionFooter}><button type="button" className={styles.btnPrimary} disabled={saving || Boolean(uploadingWeek)} onClick={save}><MIcon name="save" size={16} />{saving ? t("ClassWorkspacePage.savingLabel") : t("ClassWorkspacePage.saveWeeklyBtn")}</button></div>}
    </section>
  </div>;
}

/* 唯讀拓撲節點：外觀與課程環境編輯器的 TopologyMachineNode 一致（同一份
   .flowMachineNode 樣式），差別只在不能拖曳連線。角色與對外服務是課程環境
   宣告的內容，這裡一併顯示，老師才對得起來。 */
function ReadonlyMachineNode({ data }) {
  const { t } = useTranslation("teaching");
  const { node, publicationCount } = data;
  return <div className={`${styles.flowMachineNode} ${styles.flowMachineNodeStatic}`}>
    <Handle type="target" position={Position.Left} isConnectable={false} />
    <div className={styles.flowNodeIcon}><MIcon name={node.resource_type === "lxc" ? "terminal" : "dns"} size={18} /></div>
    <div className={styles.flowNodeLabel}>
      <strong title={node.name}>{node.name}</strong>
      <span>{node.role ? `${node.role} · ` : ""}{node.source_type === "custom" ? t("ClassWorkspacePage.sourceCustomSpecLabel") : t("ClassWorkspacePage.machineTemplateLabel")} · {node.resource_type === "lxc" ? t("ClassWorkspacePage.typeContainerLxc") : t("ClassWorkspacePage.typeVm")}</span>
      <small>{node.cpu} CPU · {Math.round(node.memory_mb / 1024)} GB RAM · {node.disk_gb} GB</small>
      {publicationCount > 0 && <em className={styles.flowNodePublic}>
        <MIcon name="public" size={12} />
        {t("ClassWorkspacePage.nodePublicCount", { count: publicationCount })}
      </em>}
    </div>
    <Handle type="source" position={Position.Right} isConnectable={false} />
  </div>;
}

const READONLY_NODE_TYPES = { classMachine: ReadonlyMachineNode };

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
        <MIcon name={publication.mode === "domain" ? "public" : "lock"} size={16} />
      </span>
      <div>
        <strong>{nameByKey[publication.node_key] ?? publication.node_key} · Port {publication.port}</strong>
        <small>{publication.mode === "domain"
          ? t("ClassWorkspacePage.publicationDomainHint", { hostname: `${publication.hostname_prefix ?? ""}` })
          : t("ClassWorkspacePage.publicationFirewallHint")}</small>
      </div>
    </div>)}
  </div>;
}

function TopologyPreview({ item }) {
  const { t } = useTranslation("teaching");
  const publicationCounts = useMemo(() => {
    const counts = {};
    for (const publication of item.publications ?? []) {
      counts[publication.node_key] = (counts[publication.node_key] ?? 0) + 1;
    }
    return counts;
  }, [item.publications]);
  const nodes = item.nodes.map((node, index) => {
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
  const edges = item.topologyEdges.map((edge, index) => {
    const bidirectional = edge.direction === "bidirectional";
    return {
      id: String(edge.id ?? `topology-${index}`),
      source: edge.source_node_key,
      target: edge.target_node_key,
      type: "smoothstep",
      animated: true,
      label: `${bidirectional ? t("ClassWorkspacePage.directionBidirectional") : t("ClassWorkspacePage.directionOneWay")} · ${String(edge.protocol).toUpperCase()}${edge.port ? `/${edge.port}` : ""}`,
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--color-primary)" },
      markerStart: bidirectional ? { type: MarkerType.ArrowClosed, color: "var(--color-primary)" } : undefined,
      style: { stroke: "var(--color-primary)", strokeWidth: 2 },
      labelStyle: { fill: "var(--color-text-secondary)", fontSize: 10, fontWeight: 600 },
      labelBgStyle: { fill: "var(--color-surface)", fillOpacity: 0.95 },
    };
  });
  // 高度跟著節點數走，一台機器不該撐出一整片空網格。
  const canvasHeight = Math.min(400, 260 + Math.max(0, item.nodes.length - 1) * 70);
  return <div className={styles.readonlyTopology} style={{ height: canvasHeight }}>
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={READONLY_NODE_TYPES}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      fitView
      fitViewOptions={{ padding: 0.25, maxZoom: 1.1 }}
      minZoom={0.65}
      maxZoom={1.2}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={20} size={1} />
    </ReactFlow>
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
    return <div className={styles.stack}>
      <section className={styles.card}>
        <div className={styles.cardHeader}>
          <div><h2>{t("ClassWorkspacePage.appliedEnvTitle")}</h2><p>{item.course_environment ? `${item.course_environment.name} · v${item.course_environment.version}` : t("ClassWorkspacePage.appliedEnvFallback")}</p></div>
          <span className={styles.lockBadge}><MIcon name="lock" size={14} />{t("ClassWorkspacePage.settingsLockedLabel")}</span>
        </div>
        <div className={styles.envFacts}>
          <div><span>{t("ClassWorkspacePage.envFactPerStudent")}</span><strong>{t("ClassWorkspacePage.machineCountUnit", { count: item.nodes.length })}</strong></div>
          <div><span>{t("ClassWorkspacePage.envFactStudents")}</span><strong>{t("ClassWorkspacePage.peopleCountUnit", { count: item.students.length })}</strong></div>
          <div><span>{t("ClassWorkspacePage.envFactTotal")}</span><strong>{t("ClassWorkspacePage.machineCountUnit", { count: item.students.length * item.nodes.length })}</strong></div>
          <div><span>{t("ClassWorkspacePage.envFactTopology")}</span><strong>{item.topologyEdges.length ? t("ClassWorkspacePage.envFactLinkCount", { count: item.topologyEdges.length }) : t("ClassWorkspacePage.envFactNoLink")}</strong></div>
          <div><span>{t("ClassWorkspacePage.envFactPublic")}</span><strong>{item.publications.length ? t("ClassWorkspacePage.envFactPublicCount", { count: item.publications.length }) : t("ClassWorkspacePage.envFactNoPublic")}</strong></div>
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

function heatLevel(usage) {
  if (usage >= 80) return 5;
  if (usage >= 60) return 4;
  if (usage >= 40) return 3;
  if (usage >= 20) return 2;
  return 1;
}

const StudentHeatCell = memo(function StudentHeatCell({
  canWatch,
  email,
  index,
  machineName,
  metricLabel,
  machine,
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
  const hasUsage = state === "on" && usage !== null;
  const detail = state === "off" ? t("ClassWorkspacePage.offLabel") : hasUsage ? `${metricLabel} ${usage}%` : t("ClassWorkspacePage.noDataLabel");
  const tone = state === "off" ? styles.heatOff : hasUsage ? styles[`heat_${heatLevel(usage)}`] : styles.heatUnavailable;
  const watchHint = canWatch ? t("ClassWorkspacePage.clickToWatchLabel") : "";
  const isLxc = String(nodeType).toLowerCase() === "lxc";
  return <button
    type="button"
    className={`${styles.heatCell} ${tone} ${canWatch ? styles.heatCellClickable : ""}`}
    title={`${name}\n${email ?? ""}\n${nodeName} · VM ${vmid ?? "—"}\n${detail}${watchHint ? `\n${watchHint}` : ""}`}
    aria-label={canWatch ? t("ClassWorkspacePage.openStudentMachineAria", { machine: nodeName, name }) : `${name}，${detail}`}
    disabled={!canWatch || watching}
    onClick={() => onWatch(student, machine, { name: nodeName, resource_type: nodeType })}
  >
    <span className={styles.studentNumber}>{String(index + 1).padStart(2, "0")}</span>
    <div><strong>{name}</strong><small>{vmid ? t("ClassWorkspacePage.vmFallbackName", { vmid }) : machineName || t("ClassWorkspacePage.notBuiltLabel")}</small></div>
    <b>{state === "off" ? t("ClassWorkspacePage.offLabel") : hasUsage ? `${usage}%` : t("ClassWorkspacePage.noDataLabel")}</b>
    {canWatch && <span className={styles.heatWatchCue}><MIcon name={isLxc ? "terminal" : "visibility"} size={14} />{t(isLxc ? "ClassWorkspacePage.terminalBtn" : "ClassWorkspacePage.watchBtn")}</span>}
  </button>;
});

function StudentMachines({ item }) {
  const { t } = useTranslation("teaching");
  const [selectedNodeId, setSelectedNodeId] = useState(() => String(item.nodes[0]?.id ?? ""));
  const [metric, setMetric] = useState("cpu");
  const [usageByVmid, setUsageByVmid] = useState({});
  const [usageStatus, setUsageStatus] = useState("loading");
  const [collectedAt, setCollectedAt] = useState(null);
  const [sources, setSources] = useState([]);
  const [message, setMessage] = useState("");
  const [watch, setWatch] = useState(null);
  const [terminal, setTerminal] = useState(null);
  const [watching, setWatching] = useState(false);
  const [broadcasting, setBroadcasting] = useState(false);
  const [broadcast, setBroadcast] = useState(null);
  const usageByVmidRef = useRef(null);

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
    setCollectedAt(null);

    async function loadUsage() {
      try {
        const response = await TeachingClassesService.resourceUsage(item.id);
        if (!active) return;
        const nextUsage = mergeResourceUsageByVmid(usageByVmidRef.current ?? {}, response?.items);
        if (usageByVmidRef.current === null || nextUsage !== usageByVmidRef.current) {
          usageByVmidRef.current = nextUsage;
          startTransition(() => {
            setUsageByVmid(nextUsage);
            setCollectedAt(response?.collected_at ? new Date(response.collected_at) : new Date());
          });
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
    setMessage("");
    const machineName = node?.name || t("ClassWorkspacePage.vmFallbackName", { vmid: machine.vmid });
    if (String(node?.resource_type).toLowerCase() === "lxc") {
      setTerminal({ vmid: machine.vmid, name: `${student.full_name || student.email} · ${machineName}`, type: "lxc", status: "running" });
      return;
    }
    setWatching(true);
    try {
      const session = await ClassroomService.createSession({ vmid: machine.vmid, mode: "monitor", class_id: item.id });
      setWatch({
        sessionId: session.id,
        title: `${student.full_name || student.email} · ${machineName}`,
      });
    } catch (error) {
      setMessage(error?.message ?? t("ClassWorkspacePage.openWatchFailed"));
    } finally {
      setWatching(false);
    }
  }, [item.id, t]);

  function closeWatch() {
    if (watch) ClassroomService.stopSession(watch.sessionId).catch(() => {});
    setWatch(null);
  }

  async function startBroadcast(vmid) {
    if (!vmid) return;
    setBroadcasting(true);
    setMessage("");
    try {
      const session = await ClassroomService.createSession({ vmid: Number(vmid), mode: "broadcast", class_id: item.id });
      setBroadcast(session);
      setMessage(t("ClassWorkspacePage.broadcastStartedMsg"));
    } catch (error) {
      setMessage(error?.message ?? t("ClassWorkspacePage.startBroadcastFailed"));
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
      setMessage(t("ClassWorkspacePage.broadcastEndedMsg"));
    } catch (error) {
      setMessage(error?.message ?? t("ClassWorkspacePage.stopBroadcastFailed"));
    } finally {
      setBroadcasting(false);
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
  const highUsage = measuredCells.filter((cell) => cell.usage >= 80).length;
  const metricInfo = RESOURCE_METRICS[metric];
  const badgeText = usageStatus === "loading" ? t("ClassWorkspacePage.loadingLiveDataLabel") : usageStatus === "error" ? t("ClassWorkspacePage.updateFailedLabel") : t("ClassWorkspacePage.every10SecUpdateLabel");

  return <div className={styles.stack}>
    <section className={`${styles.card} ${styles.heatmapCard}`}>
      <div className={styles.heatmapHeader}>
        <div><span className={styles.heatmapEyebrow}>{t("ClassWorkspacePage.liveResourceOverviewEyebrow")}</span><h2>{t("ClassWorkspacePage.heatmapTitle")}</h2><p>{t("ClassWorkspacePage.heatmapDesc")}</p></div>
        <span className={usageStatus === "error" ? styles.prototypeBadge : styles.liveBadge}><MIcon name={usageStatus === "error" ? "sync_problem" : "sensors"} size={15} />{badgeText}</span>
      </div>

      <div className={styles.broadcastTools}><MIcon name="sensors" size={18} /><strong>{t("ClassWorkspacePage.broadcastDemoLabel")}</strong>{broadcast ? <><span>{t("ClassWorkspacePage.broadcastInProgress")}</span><button type="button" className={styles.btnSecondary} disabled={broadcasting} onClick={stopBroadcast}>{t("ClassWorkspacePage.stopBroadcastBtn")}</button></> : <select disabled={broadcasting || !sources.length} defaultValue="" onChange={(event) => { startBroadcast(event.target.value); event.target.value = ""; }}><option value="">{sources.length ? t("ClassWorkspacePage.selectRunningVmOption") : t("ClassWorkspacePage.noBroadcastVmOption")}</option>{sources.map((source) => <option key={source.vmid} value={source.vmid}>{source.name || t("ClassWorkspacePage.vmFallbackName", { vmid: source.vmid })}</option>)}</select>}</div>
      {message && <p className={styles.inlineMessage}>{message}</p>}

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
        <div className={styles.metricTabs} role="tablist" aria-label={t("ClassWorkspacePage.selectMetricAria")}>
          {Object.entries(RESOURCE_METRICS).map(([key, info]) => <button key={key} type="button" role="tab" aria-selected={metric === key} className={metric === key ? styles.metricTabActive : ""} onClick={() => setMetric(key)}><MIcon name={info.icon} size={16} />{info.label}</button>)}
        </div>
      </div>

      {selectedNode && item.students.length ? <>
        <div className={styles.heatmapSummary}>
          <div><span className={styles.selectedMachineIcon}><MIcon name={selectedNode.resource_type === "lxc" ? "terminal" : "dns"} size={20} /></span><div><strong>{selectedNode.name}</strong><small>{t("ClassWorkspacePage.heatmapMachineSubtitle", { role: selectedNode.role || t("ClassWorkspacePage.classroomMachineFallback"), metric: metricInfo.label, updatedSuffix: collectedAt ? t("ClassWorkspacePage.updatedAtSuffix", { time: formatTime(collectedAt, "", { seconds: true }) }) : "" })}</small></div></div>
          <dl><div><dt>{t("ClassWorkspacePage.poweredOnLabel")}</dt><dd>{activeCells.length}<small>/{cells.length}</small></dd></div><div><dt>{t("ClassWorkspacePage.averageLabel")}</dt><dd>{average ?? "—"}{average !== null && <small>%</small>}</dd></div><div><dt>{t("ClassWorkspacePage.highLoadLabel")}</dt><dd>{highUsage}<small>{t("ClassWorkspacePage.highLoadPeopleUnit")}</small></dd></div></dl>
        </div>

        <div className={styles.heatGrid} aria-label={`${selectedNode.name} ${metricInfo.label} ${t("ClassWorkspacePage.usageRateLabel")}`}>
          {cells.map(({ student, machine, index, state, usage }) => <StudentHeatCell
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

        <div className={styles.heatLegend} aria-label={t("ClassWorkspacePage.heatLegendAria")}><span><i className={styles.heatOff} />{t("ClassWorkspacePage.offLabel")}</span><span><i className={styles.heatUnavailable} />{t("ClassWorkspacePage.noDataLabel")}</span><span className={styles.legendScale}>{t("ClassWorkspacePage.lowLabel")}<i className={styles.heat_1} /><i className={styles.heat_2} /><i className={styles.heat_3} /><i className={styles.heat_4} /><i className={styles.heat_5} />{t("ClassWorkspacePage.highLabel")}</span><span>{t("ClassWorkspacePage.usageRateLabel")}</span></div>
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
  const tab = section === "classroom" ? "progress" : section ?? "overview";
  const [item, setItem] = useState(null);
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
    const timer = window.setInterval(() => TeachingClassesService.provisionStatus(item.id).then(refresh).catch(() => {}), 3000);
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
  const visibleTabs = TABS.filter(([key]) => !POST_ACTIVE_TABS.includes(key) || item.status === "active");
  // 已封存的班級沒有任何可用選項，就不要留一顆會打開空選單的按鈕；
  // 「重試回收」已經是狀態面板的行動。
  const canEditSchedule = item.status === "planning";
  const canManageLifecycle = item.status !== "archived";
  const completed = [item.students.length > 0, Boolean(item.course_environment) && item.nodes.length > 0].filter(Boolean).length;

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
    <section className={styles.workflowTabsBar} aria-label={t("ClassWorkspacePage.workflowAriaLabel")}>
      <nav className={styles.workspaceTabs}>{visibleTabs.map(([key, icon, labelKey]) => {
        const done = key === "students" ? item.students.length > 0 : key === "weekly" ? item.weeks.some((week) => week.title.trim()) : key === "machines" ? Boolean(item.course_environment) && item.nodes.length > 0 : false;
        const target = key === "overview" ? `/class-management/${classId}` : key === "ai" ? `/class-management/${classId}/ai` : `/class-management/${classId}/${key}`;
        return <button type="button" key={key} className={tab === key ? styles.workspaceTabActive : ""} onClick={() => navigate(target)}><MIcon name={done ? "check" : icon} size={17} /><strong>{t(labelKey)}</strong></button>;
      })}</nav>
      <div className={styles.workflowProgress}><span>{t("ClassWorkspacePage.setupProgressLabelShort")}</span><strong>{item.status === "active" ? t("ClassWorkspacePage.allReadyLabel") : t("ClassWorkspacePage.completedCountLabel", { count: completed })}</strong></div>
    </section>
    <main className={styles.workspaceContent}>
      {tab === "overview" && <Overview item={item} template={template} onProvision={provision} onNavigate={(target) => navigate(`/class-management/${classId}/${target}`)} onRetry={retryFailed} onReset={resetFailed} onReclaim={reclaimClass} provisioning={provisioning} recovering={recovering} lifecycleBusy={lifecycleBusy} />}
      {tab === "students" && <Students item={item} onRefresh={refresh} />}
      {tab === "weekly" && <WeeklyContent item={item} onRefresh={refresh} />}
      {tab === "machines" && <Machines item={item} templates={templates} template={template} onRefresh={refresh} onTemplate={setTemplateId} createdTemplateId={location.state?.createdTemplateId} />}
      {postUnavailable && <LockedFeature section={tab} />}
      {tab === "progress" && !postUnavailable && <StudentMachines item={item} />}
      {!TABS.some(([key]) => key === tab) && <LockedFeature section={tab} />}
    </main>
    {scheduleDialog.open && <ClassCreateDialog item={item} closing={scheduleDialog.closing} onClose={() => setScheduleOpen(false)} onUpdated={(result) => { refresh(result); setScheduleOpen(false); toast.success(t("ClassWorkspacePage.scheduleUpdatedMsg")); }} />}
    {extendDialog.open && <ExtendDialog item={item} closing={extendDialog.closing} busy={lifecycleBusy} onClose={() => setExtendOpen(false)} onExtend={extendClass} />}
  </div>;
}
