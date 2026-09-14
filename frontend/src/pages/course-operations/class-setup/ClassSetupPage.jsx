import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import EmptyState from "../../../components/EmptyState/EmptyState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import { CourseEnvironmentsService } from "../../../services/courseEnvironments";
import EnvironmentChoice from "../EnvironmentChoice";
import shared from "../CourseOperations.module.scss";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { focusInvalidField } from "../../../utils/focusField";
import {
  BOOT_LEAD_OPTIONS,
  classSchedulePayload,
  createClassScheduleForm,
  SHUTDOWN_GRACE_OPTIONS,
} from "../classScheduleForm";
import styles from "./ClassSetupPage.module.scss";

const STEPS = [
  ["basic", "ClassSetupPage.stepBasicLabel", "ClassSetupPage.stepBasicHint"],
  ["students", "ClassSetupPage.stepStudentsLabel", "ClassSetupPage.stepStudentsHint"],
  ["environment", "ClassSetupPage.stepEnvironmentLabel", "ClassSetupPage.stepEnvironmentHint"],
  ["tasks", "ClassSetupPage.stepTasksLabel", "ClassSetupPage.stepTasksHint"],
  ["review", "ClassSetupPage.stepReviewLabel", "ClassSetupPage.stepReviewHint"],
];

const WEEKDAY_FULL_KEYS = [
  "ClassSetupPage.weekdayFullMon",
  "ClassSetupPage.weekdayFullTue",
  "ClassSetupPage.weekdayFullWed",
  "ClassSetupPage.weekdayFullThu",
  "ClassSetupPage.weekdayFullFri",
  "ClassSetupPage.weekdayFullSat",
  "ClassSetupPage.weekdayFullSun",
];

const WEEKDAY_SHORT_KEYS = [
  "ClassSetupPage.weekdayShortMon",
  "ClassSetupPage.weekdayShortTue",
  "ClassSetupPage.weekdayShortWed",
  "ClassSetupPage.weekdayShortThu",
  "ClassSetupPage.weekdayShortFri",
  "ClassSetupPage.weekdayShortSat",
  "ClassSetupPage.weekdayShortSun",
];

export function parseStudentEmails(value) {
  return [...new Set(String(value).split(/[\s,;]+/).map((email) => email.trim().toLowerCase()).filter(Boolean))];
}

// 後端只把這兩種狀態的週次送到學生端（weekly_task_service.VISIBLE_WEEK_STATUSES）。
const VISIBLE_WEEK_STATUSES = ["published", "completed"];

export function weekPayload(weeks, { publish = false } = {}) {
  return weeks.map((week, index) => {
    const title = String(week.title ?? "").trim();
    const status = week.status ?? "draft";
    return {
      week_number: Number(week.week_number ?? week.week ?? index + 1),
      session_date: week.session_date ?? week.date,
      title,
      target_node_key: week.target_node_key ?? week.target ?? null,
      // 精靈沒有班級頁那種逐週發布鈕；少了這個開關，老師填好的主題會全部停在
      // 草稿，班級建好、狀態變成可上課，學生端卻一週內容都看不到。
      status: publish && title && !VISIBLE_WEEK_STATUSES.includes(status) ? "published" : status,
      files: (week.files ?? []).map((file) => ({
        filename: file.filename,
        storage_key: file.storage_key ?? null,
        target_path: file.target_path ?? null,
      })),
    };
  });
}

export function visibleWeekCount(weeks) {
  return weeks.filter((week) => VISIBLE_WEEK_STATUSES.includes(week.status)).length;
}

export function templateBuilderPath(classId) {
  const returnTo = `/class-setup?classId=${encodeURIComponent(classId)}&step=3`;
  return `/course-template-management/new?returnTo=${encodeURIComponent(returnTo)}`;
}

function normalizeClass(item) {
  if (!item) return null;
  return {
    ...item,
    id: String(item.id),
    students: item.students ?? [],
    nodes: item.machine_nodes ?? [],
    weeks: item.weeks ?? [],
  };
}

function SummaryLine({ done, label, value }) {
  const { t } = useTranslation("teaching");
  return <div className={done ? styles.summaryDone : styles.summaryPending}><span><MIcon name={done ? "check" : "radio_button_unchecked"} size={17} /></span><div><strong>{label}</strong><small>{value}</small></div><em>{done ? t("ClassSetupPage.summaryDone") : t("ClassSetupPage.summaryPending")}</em></div>;
}

export default function ClassSetupPage() {
  const { t } = useTranslation("teaching");
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const classId = params.get("classId") ?? "";
  const requestedStep = Number(params.get("step") ?? 1);
  const step = Math.min(5, Math.max(1, requestedStep));
  const [form, setForm] = useState(() => createClassScheduleForm());
  const [item, setItem] = useState(null);
  const [templates, setTemplates] = useState([]);
  const [templateId, setTemplateId] = useState("");
  const [emails, setEmails] = useState("");
  const [weeks, setWeeks] = useState([]);
  const [publishWeeks, setPublishWeeks] = useState(true);
  const [capacity, setCapacity] = useState(null);
  const [loading, setLoading] = useState(Boolean(classId));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [invalidField, setInvalidField] = useState("");
  const nameRef = useRef(null);
  const emailsRef = useRef(null);

  function markInvalid(key, ref) {
    setInvalidField(key);
    focusInvalidField(ref.current);
  }

  function clearInvalid(key) { setInvalidField((current) => (current === key ? "" : current)); }

  const selectedTemplate = templates.find((template) => String(template.versionId) === String(templateId));
  const studentsReady = Boolean(item?.students.length);
  const environmentReady = Boolean(item?.course_environment && item?.nodes.length);
  const completed = [Boolean(item), studentsReady, environmentReady, weeks.some((week) => String(week.title ?? "").trim())];
  // 與班級頁的「建機準備 x/2」同一份定義，兩邊不會算出不同數字。
  const provisionReady = [studentsReady, environmentReady].filter(Boolean).length;

  function applyClass(result) {
    const normalized = normalizeClass(result);
    setItem(normalized);
    setWeeks(normalized.weeks);
    if (normalized.course_version_id) setTemplateId(String(normalized.course_version_id));
    setForm(createClassScheduleForm(normalized));
  }

  useEffect(() => {
    let active = true;
    CourseEnvironmentsService.listPublished()
      .then((rows) => {
        if (!active) return;
        setTemplates(rows);
        const created = location.state?.createdTemplateId;
        if (created && rows.some((row) => String(row.id) === String(created))) {
          setTemplateId(String(rows.find((row) => String(row.id) === String(created)).versionId));
          setMessage(t("ClassSetupPage.templatePublishedMsg"));
        }
      })
      .catch((reason) => active && setMessage(reason?.message ?? t("ClassSetupPage.loadTemplatesFailed")));
    return () => { active = false; };
  }, [location.state?.createdTemplateId, t]);

  useEffect(() => {
    if (!classId) { setLoading(false); return undefined; }
    let active = true;
    setLoading(true);
    TeachingClassesService.get(classId)
      .then((result) => active && applyClass(result))
      .catch((reason) => active && setMessage(reason?.message ?? t("ClassSetupPage.loadDraftFailed")))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [classId, t]);

  useEffect(() => {
    if (step !== 5 || !classId || !item?.students.length || !item?.nodes.length) return undefined;
    let active = true;
    setCapacity(null);
    TeachingClassesService.capacityPreview(classId)
      .then((result) => active && setCapacity(result))
      .catch((reason) => active && setCapacity({ ready: false, issues: [reason?.message ?? t("ClassSetupPage.capacityCheckFailed")] }));
    return () => { active = false; };
  }, [step, classId, item?.students.length, item?.nodes.length, t]);

  function updateForm(key, value) { setForm((current) => ({ ...current, [key]: value })); clearInvalid(key); }
  function go(nextStep) { setParams(classId ? { classId, step: String(nextStep) } : { step: String(nextStep) }); window.scrollTo({ top: 0, behavior: "smooth" }); }
  function createTemplate() { navigate(templateBuilderPath(classId)); }
  function pauseSetup() {
    navigate("/class-management", {
      state: { message: t("ClassSetupPage.pausedMsg", { name: item?.name ?? t("ClassSetupPage.defaultClassName") }) },
    });
  }

  async function saveBasic() {
    if (!form.name.trim()) { markInvalid("name", nameRef); return false; }
    const payload = classSchedulePayload(form);
    const saved = classId ? await TeachingClassesService.update(classId, payload) : await TeachingClassesService.create(payload);
    applyClass(saved);
    if (!classId) setParams({ classId: String(saved.id), step: "2" });
    return true;
  }

  async function saveStudents() {
    const parsed = parseStudentEmails(emails);
    if (!item?.students.length && !parsed.length) { markInvalid("emails", emailsRef); return false; }
    if (parsed.length) {
      const result = await TeachingClassesService.addStudents(classId, parsed);
      applyClass(result.class);
      setEmails("");
      if (result.not_found?.length) setMessage(t("ClassSetupPage.addedStudentsMsg", { added: result.added, notFound: result.not_found.join("、") }));
    }
    return true;
  }

  async function saveEnvironment() {
    if (!templateId) { setMessage(t("ClassSetupPage.selectEnvironmentPrompt")); return false; }
    applyClass(await TeachingClassesService.selectCourse(classId, templateId));
    return true;
  }

  async function saveTasks() {
    applyClass(await TeachingClassesService.replaceWeeks(classId, weekPayload(weeks, { publish: publishWeeks })));
    return true;
  }

  async function next() {
    setBusy(true); setMessage(""); setInvalidField("");
    try {
      const saved = step === 1 ? await saveBasic() : step === 2 ? await saveStudents() : step === 3 ? await saveEnvironment() : await saveTasks();
      if (saved && step < 5 && !(step === 1 && !classId)) go(step + 1);
    } catch (reason) { setMessage(reason?.message ?? t("ClassSetupPage.saveFailed")); }
    finally { setBusy(false); }
  }

  async function provision() {
    if (!capacity?.ready) return;
    setBusy(true); setMessage("");
    try {
      const result = await TeachingClassesService.provision(classId);
      navigate(`/class-management/${result.id}`, { replace: true, state: { message: t("ClassSetupPage.provisionSuccessMsg") } });
    } catch (reason) { setMessage(reason?.message ?? t("ClassSetupPage.provisionFailed")); }
    finally { setBusy(false); }
  }

  const taskCount = useMemo(() => weeks.filter((week) => String(week.title ?? "").trim()).length, [weeks]);
  const visibleCount = useMemo(() => visibleWeekCount(weeks), [weeks]);
  if (loading) return <LoadingState fullPage text={t("ClassSetupPage.restoringText")} />;

  return <div className={styles.page}>
    <PageHeader title={item?.name || t("ClassSetupPage.defaultPageTitle")} subtitle={t("ClassSetupPage.pageSubtitle")}>
      <button type="button" className={styles.backBtn} onClick={() => navigate("/class-management")}><MIcon name="arrow_back" size={18} />{t("ClassSetupPage.backToClassManagement")}</button>
    </PageHeader>

    <section className={styles.stepperBar}>
      <nav className={styles.stepper} aria-label={t("ClassSetupPage.stepperAriaLabel")}>{STEPS.map(([key, labelKey], index) => {
        const number = index + 1;
        const done = number < step || (number <= 4 && completed[index]);
        return <button type="button" key={key} disabled={!classId && number > 1} className={`${step === number ? styles.stepActive : ""} ${done ? styles.stepDone : ""}`} onClick={() => number <= step && go(number)}><span>{done ? <MIcon name="check" size={13} /> : number}</span><strong>{t(labelKey)}</strong></button>;
      })}</nav>
      <div className={styles.stepperProgress}><span>{t("ClassSetupPage.progressLabel")}</span><strong>{t("ClassSetupPage.progressCount", { count: provisionReady })}</strong></div>
    </section>

    {message && <div className={styles.message}><MIcon name="info" size={17} />{message}</div>}

    <main className={styles.content}>
      {step === 1 && <section className={styles.card}><div className={styles.sectionHeader}><span>1</span><div><h2>{t("ClassSetupPage.step1Title")}</h2><p>{t("ClassSetupPage.step1Desc")}</p></div></div><div className={styles.formGrid}>
        <label className={styles.full}><span>{t("ClassSetupPage.fieldClassName")}</span><input ref={nameRef} className={invalidField === "name" ? styles.fieldInvalid : undefined} aria-invalid={invalidField === "name"} value={form.name} onChange={(event) => updateForm("name", event.target.value)} placeholder={t("ClassSetupPage.classNamePlaceholder")} autoFocus /></label>
        <label className={styles.full}><span>{t("ClassSetupPage.fieldLocation")}</span><input value={form.location} onChange={(event) => updateForm("location", event.target.value)} placeholder={t("ClassSetupPage.locationPlaceholder")} /></label>
        <label><span>{t("ClassSetupPage.fieldStartDate")}</span><input type="date" value={form.startDate} onChange={(event) => updateForm("startDate", event.target.value)} /></label>
        <label><span>{t("ClassSetupPage.fieldEndDate")}</span><input type="date" value={form.endDate} onChange={(event) => updateForm("endDate", event.target.value)} /></label>
        <label><span>{t("ClassSetupPage.fieldWeekday")}</span><select value={form.weekday} onChange={(event) => updateForm("weekday", Number(event.target.value))}>{WEEKDAY_FULL_KEYS.map((labelKey, index) => <option key={labelKey} value={index}>{t(labelKey)}</option>)}</select></label>
        <label><span>{t("ClassSetupPage.fieldClassTime")}</span><div className={styles.timePair}><input type="time" value={form.startTime} onChange={(event) => updateForm("startTime", event.target.value)} /><i>{t("ClassSetupPage.timeRangeSeparator")}</i><input type="time" value={form.endTime} onChange={(event) => updateForm("endTime", event.target.value)} /></div></label>
        <details className={styles.advanced}><summary>{t("ClassSetupPage.advancedSettings")}</summary><div className={styles.advancedGrid}><label><span>{t("ClassSetupPage.fieldTerm")}</span><input value={form.term} onChange={(event) => updateForm("term", event.target.value)} /></label><label><span>{t("ClassSetupPage.fieldBootLead")}</span><select value={form.bootLeadMinutes} onChange={(event) => updateForm("bootLeadMinutes", Number(event.target.value))}>{BOOT_LEAD_OPTIONS.map((minutes) => <option key={minutes} value={minutes}>{minutes === 0 ? t("ClassSetupPage.bootLeadOnTime") : t("ClassSetupPage.bootLeadMinutesOption", { minutes })}</option>)}</select></label><label><span>{t("ClassSetupPage.fieldShutdownGrace")}</span><select value={form.shutdownGraceMinutes} onChange={(event) => updateForm("shutdownGraceMinutes", Number(event.target.value))}>{SHUTDOWN_GRACE_OPTIONS.map((minutes) => <option key={minutes} value={minutes}>{minutes === 0 ? t("ClassSetupPage.shutdownGraceImmediate") : t("ClassSetupPage.shutdownGraceMinutesOption", { minutes })}</option>)}</select></label></div></details>
      </div></section>}

      {step === 2 && <section className={styles.card}><div className={styles.sectionHeader}><span>2</span><div><h2>{t("ClassSetupPage.step2Title")}</h2><p>{t("ClassSetupPage.step2Desc")}</p></div></div><div className={styles.studentLayout}><label><span>{t("ClassSetupPage.fieldStudentEmails")}</span><textarea ref={emailsRef} className={invalidField === "emails" ? styles.fieldInvalid : undefined} aria-invalid={invalidField === "emails"} rows={10} value={emails} onChange={(event) => { setEmails(event.target.value); clearInvalid("emails"); }} placeholder={"student01@example.edu\nstudent02@example.edu"} autoFocus /><small>{t("ClassSetupPage.pendingEmailsCount", { count: parseStudentEmails(emails).length })}</small></label><aside><strong>{t("ClassSetupPage.currentStudentsLabel")}</strong><span>{item?.students.length ?? 0}<small>{t("ClassSetupPage.unitPeople")}</small></span><p>{item?.students.length ? t("ClassSetupPage.canAddMoreStudents") : t("ClassSetupPage.needAtLeastOneStudent")}</p>{item?.students.slice(0, 5).map((student) => <em key={student.id}>{student.full_name || student.email}</em>)}</aside></div></section>}

      {step === 3 && (
        <section className={styles.card}>
          <div className={styles.sectionHeader}>
            <span>3</span>
            <div>
              <h2>{t("ClassSetupPage.step3Title")}</h2>
              <p>{t("ClassSetupPage.step3Desc")}</p>
            </div>
          </div>

          {templates.length > 0 ? (
            <div className={shared.envChoices}>
              {templates.map((candidate) => (
                <EnvironmentChoice
                  key={candidate.versionId}
                  candidate={candidate}
                  selected={String(candidate.versionId) === String(templateId)}
                  onSelect={() => setTemplateId(String(candidate.versionId))}
                />
              ))}
            </div>
          ) : (
            <EmptyState
              icon="view_quilt"
              title={t("ClassSetupPage.noPublishedTemplatesTitle")}
              action={
                <button type="button" className={styles.btnPrimary} onClick={createTemplate}>
                  <MIcon name="add" size={16} />{t("ClassSetupPage.createTemplateNowBtn")}
                </button>
              }
            />
          )}

          {selectedTemplate && (
            <div className={styles.environmentSummary}>
              <strong>{item?.students.length
                ? t("ClassSetupPage.estimatedMachinesTotal", { count: item.students.length * selectedTemplate.nodes.length })
                : t("ClassSetupPage.estimatedMachinesNoStudents")}</strong>
              <span>{t("ClassSetupPage.studentsTimesMachines", { students: item?.students.length ?? 0, perStudent: selectedTemplate.nodes.length })}</span>
            </div>
          )}

          {/* 找不到合適範本是例外，放在清單後面當出口，不要擋在選擇之前 */}
          <div className={styles.templatePauseCard}>
            <span className={styles.templatePauseIcon}><MIcon name="bookmark_added" size={22} /></span>
            <div>
              <strong>{t("ClassSetupPage.noSuitableTemplateTitle")}</strong>
              <p>{t("ClassSetupPage.templatePauseDesc")}</p>
              <small><MIcon name="cloud_done" size={14} />{t("ClassSetupPage.noDataLossHint")}</small>
            </div>
            <div className={styles.templatePauseActions}>
              <button type="button" className={styles.btnPrimary} onClick={createTemplate}>
                <MIcon name="add" size={16} />{t("ClassSetupPage.createNewTemplateBtn")}
              </button>
              <button type="button" className={styles.btnSecondary} onClick={pauseSetup}>
                {t("ClassSetupPage.saveForLaterBtn")}
              </button>
            </div>
          </div>
        </section>
      )}

      {step === 4 && <section className={styles.card}><div className={styles.sectionHeader}><span>4</span><div><h2>{t("ClassSetupPage.step4Title")}</h2><p>{t("ClassSetupPage.step4Desc")}</p></div><em>{t("ClassSetupPage.weeksConfiguredCount", { done: taskCount, total: weeks.length })}</em></div><label className={styles.publishToggle}><input type="checkbox" checked={publishWeeks} onChange={(event) => setPublishWeeks(event.target.checked)} /><div><strong>{t("ClassSetupPage.publishWeeksLabel")}</strong><small>{t("ClassSetupPage.publishWeeksHint")}</small></div></label><div className={styles.weekList}>{weeks.map((week, index) => <label key={week.id ?? week.session_date}><span><strong>{t("ClassSetupPage.weekNumberLabel", { week: week.week_number ?? index + 1 })}</strong><small>{week.session_date}</small></span><input value={week.title ?? ""} onChange={(event) => setWeeks((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, title: event.target.value } : row))} placeholder={t("ClassSetupPage.weekTitlePlaceholder")} /></label>)}</div></section>}

      {step === 5 && <div className={styles.stack}>
        <section className={styles.readyPanel}>
          <div className={styles.readySummary}>
            <span className={styles.readyIcon}><MIcon name={capacity?.ready ? "check" : "hourglass_top"} size={22} /></span>
            <div>
              <span>{t("ClassSetupPage.progressLabel")} · {t("ClassSetupPage.progressCount", { count: provisionReady })}</span>
              <h2>{capacity ? capacity.ready ? t("ClassSetupPage.capacityReady") : t("ClassSetupPage.capacityNotReady") : t("ClassSetupPage.capacityChecking")}</h2>
              <p>{capacity?.ready ? t("ClassSetupPage.capacitySummary", { machines: capacity.machine_count, ips: capacity.ip_count }) : capacity?.issues?.join("；") ?? t("ClassSetupPage.capacityCheckingDetail")}</p>
            </div>
            <div className={styles.readyActions}>
              <button type="button" className={styles.btnSecondary} onClick={() => navigate(`/class-management/${classId}`)}>{t("ClassSetupPage.saveDraftLaterBtn")}</button>
              <button type="button" className={styles.btnPrimary} disabled={!capacity?.ready || busy} onClick={provision}><MIcon name="rocket_launch" size={17} />{busy ? t("ClassSetupPage.submittingBtn") : t("ClassSetupPage.finishAndSubmitBtn")}</button>
            </div>
          </div>
          {capacity?.ready && <div className={styles.capacityFacts}>
            <div><span>{t("ClassSetupPage.capacityFactMachines")}</span><strong>{capacity.machine_count}</strong></div>
            <div><span>CPU</span><strong>{capacity.cpu_cores}</strong></div>
            <div><span>RAM</span><strong>{Math.round(capacity.memory_mb / 1024)} GB</strong></div>
            <div><span>Disk</span><strong>{capacity.disk_gb} GB</strong></div>
            <div><span>{t("ClassSetupPage.capacityFactIps")}</span><strong>{capacity.ip_count}</strong></div>
          </div>}
        </section>
        <section className={styles.card}>
          <div className={styles.sectionHeader}><span>5</span><div><h2>{t("ClassSetupPage.step5Title")}</h2><p>{t("ClassSetupPage.step5Desc")}</p></div></div>
          <div className={styles.summaryList}>
            <SummaryLine done={Boolean(item)} label={t("ClassSetupPage.summaryLabelSchedule")} value={item ? `${item.start_date} ${t("ClassSetupPage.scheduleDateTo")} ${item.end_date} · ${t("ClassSetupPage.weeklyPrefix")}${t(WEEKDAY_SHORT_KEYS[item.weekday])} ${String(item.start_time).slice(0, 5)}` : t("ClassSetupPage.notCreatedYet")} />
            <SummaryLine done={studentsReady} label={t("ClassSetupPage.summaryLabelStudents")} value={t("ClassSetupPage.studentsCountLabel", { count: item?.students.length ?? 0 })} />
            <SummaryLine done={environmentReady} label={t("ClassSetupPage.summaryLabelEnvironment")} value={item?.course_environment ? t("ClassSetupPage.envSummaryValue", { name: item.course_environment.name, count: item.nodes.length }) : t("ClassSetupPage.notSelectedYet")} />
            <SummaryLine done={taskCount > 0} label={t("ClassSetupPage.summaryLabelTasks")} value={t("ClassSetupPage.weeksSetSummary", { done: taskCount, total: weeks.length, visible: visibleCount })} />
          </div>
        </section>
      </div>}
    </main>

    {step < 5 && <footer className={styles.footer}><button type="button" className={styles.btnSecondary} disabled={step === 1 || busy} onClick={() => go(step - 1)}>{t("ClassSetupPage.prevStepBtn")}</button><span>{t("ClassSetupPage.stepProgressLabel", { step })}</span>{step === 3 && !templateId ? <em className={styles.footerHint}><MIcon name="info" size={16} />{templates.length === 0 ? t("ClassSetupPage.needCreateTemplateHint") : t("ClassSetupPage.selectTemplateHint")}</em> : <button type="button" className={styles.btnPrimary} disabled={busy} onClick={next}>{busy ? t("ClassSetupPage.savingBtn") : t("ClassSetupPage.saveAndNextBtn")}<MIcon name="arrow_forward" size={16} /></button>}</footer>}
  </div>;
}
