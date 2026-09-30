import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";
import Stepper from "../../../components/Stepper/Stepper";
import EmptyState from "../../../components/EmptyState/EmptyState";
import LoadingState from "../../../components/LoadingState/LoadingState";
import { CourseEnvironmentsService } from "../../../services/courseEnvironments";
import EnvironmentChoice from "../EnvironmentChoice";
import shared from "../CourseOperations.module.scss";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { focusInvalidField } from "../../../utils/focusField";
import { formatDate } from "../../../utils/formatDate";
import { joinList } from "../../../utils/joinList";
import {
  BOOT_LEAD_OPTIONS,
  classSchedulePayload,
  createClassScheduleForm,
  SHUTDOWN_GRACE_OPTIONS,
} from "../classScheduleForm";
import { parseStudentEmails, VISIBLE_WEEK_STATUSES, visibleWeekCount, weekFilesPayload } from "../classWeeks";
import styles from "./ClassSetupPage.module.scss";
import useAiScreen from "../../../hooks/useAiScreen";
import { useToast } from "../../../hooks/useToast";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useUnsavedChanges, useUnsavedChangesGuard } from "../../../contexts/UnsavedChangesContext";

const STEPS = [
  ["basic", "ClassSetupPage.stepBasicLabel"],
  ["students", "ClassSetupPage.stepStudentsLabel"],
  ["environment", "ClassSetupPage.stepEnvironmentLabel"],
  ["tasks", "ClassSetupPage.stepTasksLabel"],
  ["review", "ClassSetupPage.stepReviewLabel"],
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
      files: weekFilesPayload(week.files),
    };
  });
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

/** 必要步驟（班級、學生、教學環境）存到後端後才能往後走；每週任務是選填，
 *  必要步驟都完成後第 4、5 步都能直接到。步驟列能點到哪、網址能停在哪都看這個。 */
export function furthestReachableStep({ saved, hasStudents, hasEnvironment }) {
  if (!saved) return 1;
  if (!hasStudents) return 2;
  if (!hasEnvironment) return 3;
  return 5;
}

function titledWeekCount(weeks) {
  return (weeks ?? []).filter((week) => String(week.title ?? "").trim()).length;
}

/** 課表日期是 YYYY-MM-DD；補上當地午夜再格式化，時區在 UTC 以西時才不會差一天 */
function formatClassDay(value) {
  return value ? formatDate(`${value}T00:00:00`) : "—";
}

function SummaryLine({ done, optional = false, label, value, onOpen }) {
  const { t } = useTranslation("teaching");
  const status = done ? t("ClassSetupPage.summaryDone") : optional ? t("ClassSetupPage.summaryOptional") : t("ClassSetupPage.summaryPending");
  return <button type="button" className={done ? styles.summaryDone : styles.summaryPending} onClick={onOpen}>
    <span><MIcon name={done ? "check" : "radio_button_unchecked"} size={17} /></span>
    <div><strong>{label}</strong><small>{value}</small></div>
    <em>{status}</em>
    <MIcon name="chevron_right" size={18} className={styles.summaryArrow} />
  </button>;
}

export default function ClassSetupPage() {
  const { t } = useTranslation("teaching");
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const confirm = useConfirm();
  const { confirmLeave } = useUnsavedChanges();
  const [params, setParams] = useSearchParams();
  const classId = params.get("classId") ?? "";
  const requestedStep = Number(params.get("step") ?? 1);
  const wantedStep = Number.isFinite(requestedStep) ? Math.min(5, Math.max(1, Math.trunc(requestedStep))) : 1;
  const [initialForm] = useState(() => createClassScheduleForm());
  const [form, setForm] = useState(initialForm);
  const [item, setItem] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [loadRetryable, setLoadRetryable] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [templates, setTemplates] = useState([]);
  const [templatesLoaded, setTemplatesLoaded] = useState(false);
  const [templateId, setTemplateId] = useState("");
  const [emails, setEmails] = useState("");
  const [weeks, setWeeks] = useState([]);
  const [publishWeeks, setPublishWeeks] = useState(true);
  const [capacity, setCapacity] = useState(null);
  const [loading, setLoading] = useState(Boolean(classId));
  const [busy, setBusy] = useState(false);
  const [invalidField, setInvalidField] = useState("");
  const [draftOpen, setDraftOpen] = useState(false);
  const nameRef = useRef(null);
  const emailsRef = useRef(null);
  const fileRef = useRef(null);
  const loadedClassIdRef = useRef("");

  function markInvalid(key, ref) {
    setInvalidField(key);
    focusInvalidField(ref.current);
  }

  function clearInvalid(key) { setInvalidField((current) => (current === key ? "" : current)); }

  const selectedTemplate = templates.find((template) => String(template.versionId) === String(templateId));
  const studentsReady = Boolean(item?.students.length);
  const environmentReady = Boolean(item?.course_environment && item?.nodes.length);
  // 手改網址、舊書籤或上一頁回到還沒解鎖的步驟（沒建班就 ?step=2、沒選環境就 ?step=5）時一律拉回
  // 能到的最後一步；否則後面步驟會拿空 id 打 API 而 404，或在沒有環境時卡在容量預檢。
  const furthestStep = furthestReachableStep({ saved: Boolean(item), hasStudents: studentsReady, hasEnvironment: environmentReady });
  const step = Math.min(wantedStep, furthestStep);
  const savedTaskCount = titledWeekCount(item?.weeks);
  // 打勾只看已存到後端的實際進度，不因為「走過了」就打勾
  const doneSteps = [Boolean(item), studentsReady, environmentReady, savedTaskCount > 0, false];
  // 與班級頁的「建機準備 x/2」同一份定義，兩邊不會算出不同數字。
  const provisionReady = [studentsReady, environmentReady].filter(Boolean).length;
  const missingSteps = [!studentsReady && 2, !environmentReady && 3].filter(Boolean);

  // 各步驟要按「儲存並下一步」才會存；還沒存的修改在換步驟、離開頁面前先確認
  const savedForm = useMemo(() => (item ? createClassScheduleForm(item) : initialForm), [item, initialForm]);
  const savedTemplateId = item?.course_version_id ? String(item.course_version_id) : "";
  const stepDirty = [
    JSON.stringify(form) !== JSON.stringify(savedForm),
    emails.trim() !== "",
    templateId !== savedTemplateId,
    weeks.some((week, index) => String(week.title ?? "") !== String(item?.weeks[index]?.title ?? "")),
    false,
  ];
  useUnsavedChangesGuard(stepDirty.some(Boolean));

  useAiScreen("class-setup", {
    "classsetup.current_step": { value: `${step}. ${t(STEPS[step - 1][1])}` },
    "classsetup.saved": { value: loading ? "載入中" : String(Boolean(item)) },
    "classsetup.student_count": { value: loading ? "未知" : String(item?.students.length ?? 0) },
    "classsetup.environment_saved": { value: loading ? "未知" : String(environmentReady) },
    "classsetup.available_environments": { value: templatesLoaded ? String(templates.length) : "未知（尚未成功載入）" },
    "classsetup.step_review": { disabled: busy || !capacity?.ready, disabled_reason: capacity?.issues?.join("；").slice(0, 300) || null },
  });

  function applyClass(result) {
    const normalized = normalizeClass(result);
    loadedClassIdRef.current = normalized.id;
    setItem(normalized);
    setWeeks(normalized.weeks);
    setTemplateId(normalized.course_version_id ? String(normalized.course_version_id) : "");
    setForm(createClassScheduleForm(normalized));
  }

  useEffect(() => {
    let active = true;
    CourseEnvironmentsService.listPublished()
      .then((rows) => {
        if (!active) return;
        setTemplates(rows);
        setTemplatesLoaded(true);
        const created = location.state?.createdTemplateId;
        if (created && rows.some((row) => String(row.id) === String(created))) {
          setTemplateId(String(rows.find((row) => String(row.id) === String(created)).versionId));
          toast.success(t("ClassSetupPage.templatePublishedMsg"));
        }
      })
      .catch((reason) => active && toast.error(reason?.message ?? t("ClassSetupPage.loadTemplatesFailed")));
    return () => { active = false; };
  }, [location.state?.createdTemplateId, t, toast]);

  useEffect(() => {
    if (!classId) { setLoading(false); return undefined; }
    // 第一步剛建好班級時網址才補上 classId，資料已經在手上，不必整頁重新載入
    if (loadedClassIdRef.current === classId) { setLoading(false); return undefined; }
    let active = true;
    setLoading(true);
    setLoadError("");
    TeachingClassesService.get(classId)
      .then((result) => {
        if (!active) return;
        // 送出建機後學生、環境與課表都鎖定了，精靈不再適用，直接回班級頁。
        // 導走前先停掉這次載入：否則 finally 會把載入畫面收掉、畫出空的精靈，
        // 網址校正再把網址改回第一步，反而蓋掉這次導頁
        if (result?.status && result.status !== "planning") {
          active = false;
          navigate(`/class-management/${result.id ?? classId}`, { replace: true });
          return;
        }
        applyClass(result);
      })
      .catch((reason) => {
        if (!active) return;
        setLoadError(reason?.message ?? t("ClassSetupPage.loadDraftFailed"));
        setLoadRetryable(![403, 404].includes(reason?.status));
      })
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [classId, navigate, t, reloadKey]);

  // 網址指到還不能去的步驟時，把網址也改成實際顯示的那一步，上一頁／重新整理才不會跳來跳去
  useEffect(() => {
    if (loading || loadError || step === wantedStep) return;
    setParams(classId ? { classId, step: String(step) } : { step: String(step) }, { replace: true });
  }, [loading, loadError, step, wantedStep, classId, setParams]);

  const classLoaded = Boolean(item);
  useEffect(() => {
    if (step !== 5 || !classId || !classLoaded) return undefined;
    /* 缺學生或缺環境根本不必問後端；直接說缺什麼，不要讓標題一直停在「正在執行容量預檢」 */
    const missing = [];
    if (!item?.students.length) missing.push(t("ClassSetupPage.needAtLeastOneStudent"));
    if (!item?.nodes.length) missing.push(t("ClassWorkspacePage.noEnvSelected"));
    if (missing.length) {
      setCapacity({ ready: false, issues: missing });
      return undefined;
    }
    let active = true;
    setCapacity(null);
    TeachingClassesService.capacityPreview(classId)
      .then((result) => active && setCapacity(result))
      .catch((reason) => active && setCapacity({ ready: false, issues: [reason?.message ?? t("ClassSetupPage.capacityCheckFailed")] }));
    return () => { active = false; };
  }, [step, classId, classLoaded, item?.students.length, item?.nodes.length, t]);

  function updateForm(key, value) { setForm((current) => ({ ...current, [key]: value })); clearInvalid(key); }
  function go(nextStep) { setParams(classId ? { classId, step: String(nextStep) } : { step: String(nextStep) }); window.scrollTo({ top: 0, behavior: "smooth" }); }

  /** 丟掉還沒存的修改，回到後端存的內容 */
  function discardUnsaved() {
    setForm(savedForm);
    setEmails("");
    setTemplateId(savedTemplateId);
    setWeeks(item?.weeks ?? []);
    setInvalidField("");
  }

  // 換步驟不會自動存檔：目前這一步有沒存的修改時先確認，確認後捨棄
  async function switchStep(nextStep) {
    if (nextStep === step) return;
    if (stepDirty[step - 1]) {
      if (!(await confirmLeave())) return;
      discardUnsaved();
    }
    go(nextStep);
  }

  async function leaveTo(path, options) {
    if (!(await confirmLeave())) return;
    navigate(path, options);
  }

  function createTemplate() { navigate(templateBuilderPath(classId)); }
  function pauseSetup() {
    leaveTo("/class-management", {
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

  function rejectedEmails(result) {
    return [...(result.not_found ?? []), ...(result.invalid_role ?? [])];
  }

  function warnRejected(result) {
    const notFound = result.not_found ?? [];
    const invalidRole = result.invalid_role ?? [];
    const problems = [
      notFound.length && t("ClassSetupPage.notFoundList", { list: joinList(notFound) }),
      invalidRole.length && t("ClassSetupPage.invalidRoleList", { list: joinList(invalidRole) }),
    ].filter(Boolean).join("；");
    toast.warning(t("ClassSetupPage.addedStudentsMsg", { added: result.added, problems }));
  }

  async function saveStudents() {
    const parsed = parseStudentEmails(emails);
    if (!item?.students.length && !parsed.length) { markInvalid("emails", emailsRef); return false; }
    if (parsed.length) {
      const result = await TeachingClassesService.addStudents(classId, parsed);
      applyClass(result.class);
      // 找不到或不是學生身分的帳號不能默默略過就進下一步：留在輸入框讓老師修正或刪除
      const rejected = rejectedEmails(result);
      if (rejected.length) {
        setEmails(rejected.join("\n"));
        markInvalid("emails", emailsRef);
        warnRejected(result);
        return false;
      }
      setEmails("");
    }
    return true;
  }

  async function importCsv() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      const result = await TeachingClassesService.importStudents(classId, file);
      applyClass(result.class);
      const rejected = rejectedEmails(result);
      if (rejected.length) {
        // 加不進去的帳號併進輸入框（保留老師原本打到一半的內容），跟手動貼上走同一套修正流程
        setEmails((current) => [...new Set([...parseStudentEmails(current), ...rejected])].join("\n"));
        markInvalid("emails", emailsRef);
        warnRejected(result);
      } else {
        toast.success(t("ClassSetupPage.csvImportedMsg", { count: result.added }));
      }
    } catch (reason) { toast.error(reason?.message ?? t("ClassSetupPage.csvImportFailed")); }
    finally { if (fileRef.current) fileRef.current.value = ""; setBusy(false); }
  }

  async function removeStudent(student) {
    const name = student.full_name || student.email;
    const ok = await confirm({
      title: t("ClassSetupPage.removeStudentConfirmTitle"),
      message: t("ClassSetupPage.removeStudentConfirmMessage", { name }),
      confirmText: t("ClassSetupPage.removeStudentConfirmBtn"),
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try { applyClass(await TeachingClassesService.removeStudent(classId, student.id)); }
    catch (reason) { toast.error(reason?.message ?? t("ClassSetupPage.removeStudentFailed")); }
    finally { setBusy(false); }
  }

  async function saveEnvironment() {
    if (!templateId) { toast.warning(t("ClassSetupPage.selectEnvironmentPrompt")); return false; }
    applyClass(await TeachingClassesService.selectCourse(classId, templateId));
    return true;
  }

  async function saveTasks() {
    applyClass(await TeachingClassesService.replaceWeeks(classId, weekPayload(weeks, { publish: publishWeeks })));
    return true;
  }

  async function next() {
    setBusy(true); setInvalidField("");
    try {
      const saved = step === 1 ? await saveBasic() : step === 2 ? await saveStudents() : step === 3 ? await saveEnvironment() : await saveTasks();
      if (saved && step < 5 && !(step === 1 && !classId)) go(step + 1);
    } catch (reason) { toast.error(reason?.message ?? t("ClassSetupPage.saveFailed")); }
    finally { setBusy(false); }
  }

  async function provision() {
    if (!capacity?.ready) return;
    setBusy(true);
    try {
      const result = await TeachingClassesService.provision(classId);
      navigate(`/class-management/${result.id}`, { replace: true, state: { message: t("ClassSetupPage.provisionSuccessMsg") } });
    } catch (reason) { toast.error(reason?.message ?? t("ClassSetupPage.provisionFailed")); }
    finally { setBusy(false); }
  }

  const taskCount = useMemo(() => titledWeekCount(weeks), [weeks]);
  if (loading) return <LoadingState fullPage text={t("ClassSetupPage.restoringText")} />;

  const backButton = <button type="button" className={styles.backBtn} onClick={() => leaveTo("/class-management")}><MIcon name="arrow_back" size={18} />{t("ClassSetupPage.backToClassManagement")}</button>;

  // 別人的班級（403）、已刪除（404）或網路錯誤：不顯示一份空白表單讓人以為能繼續填
  if (loadError) return <div className={styles.page}>
    <PageHeader title={t("ClassSetupPage.defaultPageTitle")}>{backButton}</PageHeader>
    <section className={styles.card}><EmptyState icon="error_outline" title={t("ClassSetupPage.loadFailedTitle")} description={loadError}
      action={loadRetryable
        ? <button type="button" className={styles.btnSecondary} onClick={() => setReloadKey((key) => key + 1)}><MIcon name="refresh" size={16} />{t("Error.retry", { ns: "common" })}</button>
        : <button type="button" className={styles.btnSecondary} onClick={() => leaveTo("/class-management")}><MIcon name="arrow_back" size={16} />{t("ClassSetupPage.backToClassManagement")}</button>} /></section>
  </div>;

  const stepLabel = (number) => t(STEPS[number - 1][1]);
  const scheduleShort = item ? `${t("ClassSetupPage.weeklyPrefix")}${t(WEEKDAY_SHORT_KEYS[item.weekday])} ${String(item.start_time).slice(0, 5)}` : t("ClassSetupPage.notCreatedYet");
  // 預估機器跟著第三步目前選的環境即時變動，還沒選就用已存的環境
  const estimateNodes = selectedTemplate?.nodes.length ?? item?.nodes.length ?? 0;
  const estimatedMachines = (item?.students.length ?? 0) * estimateNodes;
  const draftRows = [
    { step: 1, value: scheduleShort },
    { step: 2, value: studentsReady ? t("ClassSetupPage.studentsCountLabel", { count: item.students.length }) : t("ClassSetupPage.draftStudentsPending") },
    { step: 3, value: item?.course_environment?.name ?? t("ClassSetupPage.notSelectedYet") },
    { step: 4, value: savedTaskCount ? t("ClassSetupPage.weeksConfiguredCount", { done: savedTaskCount, total: item.weeks.length }) : t("ClassSetupPage.summaryOptional") },
  ];
  const progressText = `${t("ClassSetupPage.progressLabel")} ${t("ClassSetupPage.progressCount", { count: provisionReady })}`;

  // 班級草稿摘要：寬的時候固定在右欄，窄的時候收成卡片上方可展開的一列
  const draftPanel = <aside className={styles.draftPanel} aria-label={t("ClassSetupPage.draftTitle")}>
    <button type="button" className={styles.draftToggle} aria-expanded={draftOpen} onClick={() => setDraftOpen((open) => !open)}>
      <span><strong>{t("ClassSetupPage.draftTitle")}</strong><small>{estimatedMachines ? `${progressText} · ${t("ClassSetupPage.draftEstimateShort", { count: estimatedMachines })}` : progressText}</small></span>
      <MIcon name={draftOpen ? "expand_less" : "expand_more"} size={20} />
    </button>
    <div className={`${styles.draftBody} ${draftOpen ? styles.draftBodyOpen : ""}`}>
      <div className={styles.draftHeading}><strong>{t("ClassSetupPage.draftTitle")}</strong><small>{item?.name || t("ClassSetupPage.notCreatedYet")}</small></div>
      <ul className={styles.draftList}>
        {draftRows.map(({ step: number, value }) => {
          const done = doneSteps[number - 1];
          return <li key={number}><button type="button" className={done ? styles.draftDone : styles.draftPending} aria-current={number === step ? "step" : undefined} disabled={number > furthestStep} onClick={() => switchStep(number)}>
            <span><MIcon name={done ? "check" : "radio_button_unchecked"} size={14} /></span>
            <div><strong>{stepLabel(number)}</strong><small>{value}</small></div>
          </button></li>;
        })}
      </ul>
      <dl className={styles.draftFacts}>
        <div><dt>{t("ClassSetupPage.draftEstimateLabel")}</dt><dd>{estimatedMachines ? t("ClassSetupPage.draftEstimateValue", { count: estimatedMachines }) : "—"}</dd></div>
        <div><dt>{t("ClassSetupPage.progressLabel")}</dt><dd>{t("ClassSetupPage.progressCount", { count: provisionReady })}</dd></div>
      </dl>
      {!estimatedMachines && <p className={styles.draftHint}>{t("ClassSetupPage.draftEstimatePending")}</p>}
    </div>
  </aside>;

  // 每一步都是同一張卡片：標題、內容、底部按鈕列。按鈕列在內容比畫面長時會貼住畫面底部
  const footer = <footer className={`${styles.cardFooter} ${step === 5 ? styles.cardFooterStatic : ""}`}>
    <button type="button" className={`${styles.btnSecondary} ${styles.footerPrev} ${step === 1 ? styles.keepSpace : ""}`} disabled={busy} onClick={() => switchStep(step - 1)}>{t("ClassSetupPage.prevStepBtn")}</button>
    <div className={styles.footerEnd}>
      {step === 3 && !templateId && <em className={styles.footerHint}><MIcon name="info" size={16} />{templates.length === 0 ? t("ClassSetupPage.needCreateTemplateHint") : t("ClassSetupPage.selectTemplateHint")}</em>}
      {step < 5 && <button type="button" className={`${styles.btnPrimary} ${styles.footerNext}`} disabled={busy || (step === 3 && !templateId)} onClick={next}>{busy ? t("ClassSetupPage.savingBtn") : t("ClassSetupPage.saveAndNextBtn")}<MIcon name="arrow_forward" size={16} /></button>}
      {step === 5 && <>
        {missingSteps.length > 0
          ? <button type="button" className={styles.btnSecondary} onClick={() => switchStep(missingSteps[0])}>{t("ClassSetupPage.goToStepBtn", { step: stepLabel(missingSteps[0]) })}</button>
          : <button type="button" className={styles.btnSecondary} onClick={() => leaveTo(`/class-management/${classId}`)}>{t("ClassSetupPage.saveDraftLaterBtn")}</button>}
        <button type="button" className={`${styles.btnPrimary} ${styles.footerNext}`} disabled={!capacity?.ready || busy} onClick={provision}><MIcon name="rocket_launch" size={17} />{busy ? t("ClassSetupPage.submittingBtn") : t("ClassSetupPage.finishAndSubmitBtn")}</button>
      </>}
    </div>
  </footer>;

  return <div className={styles.page}>
    <PageHeader title={item?.name || t("ClassSetupPage.defaultPageTitle")}>{backButton}</PageHeader>

    <section className={styles.stepperBar}>
      <Stepper
        ariaLabel={t("ClassSetupPage.stepperAriaLabel")}
        steps={STEPS.map(([key, labelKey], index) => ({ key, label: t(labelKey), done: doneSteps[index], disabled: index + 1 > furthestStep }))}
        activeKey={STEPS[step - 1]?.[0]}
        onSelect={(key) => switchStep(STEPS.findIndex(([k]) => k === key) + 1)}
      />
    </section>

    {/* 第五步本身就是總覽，不另外放摘要欄 */}
    <div className={`${styles.workspace} ${step < 5 ? styles.workspaceSplit : ""}`}>
      <section className={styles.card}>
        {step === 1 && <>
          <div className={styles.sectionHeader}><div><h2>{t("ClassSetupPage.step1Title")}</h2><p>{t("ClassSetupPage.step1Desc")}</p></div></div>
          <div className={styles.formGrid}>
            <label className={styles.full}><span>{t("ClassSetupPage.fieldClassName")}</span><input ref={nameRef} className={invalidField === "name" ? styles.fieldInvalid : undefined} aria-invalid={invalidField === "name"} value={form.name} onChange={(event) => updateForm("name", event.target.value)} placeholder={t("ClassSetupPage.classNamePlaceholder")} autoFocus /></label>
            <label className={styles.full}><span>{t("ClassSetupPage.fieldLocation")}</span><input value={form.location} onChange={(event) => updateForm("location", event.target.value)} placeholder={t("ClassSetupPage.locationPlaceholder")} /></label>
            <label><span>{t("ClassSetupPage.fieldStartDate")}</span><input type="date" value={form.startDate} onChange={(event) => updateForm("startDate", event.target.value)} /></label>
            <label><span>{t("ClassSetupPage.fieldEndDate")}</span><input type="date" value={form.endDate} onChange={(event) => updateForm("endDate", event.target.value)} /></label>
            <label><span>{t("ClassSetupPage.fieldWeekday")}</span><select value={form.weekday} onChange={(event) => updateForm("weekday", Number(event.target.value))}>{WEEKDAY_FULL_KEYS.map((labelKey, index) => <option key={labelKey} value={index}>{t(labelKey)}</option>)}</select></label>
            <label><span>{t("ClassSetupPage.fieldClassTime")}</span><div className={styles.timePair}><input type="time" value={form.startTime} onChange={(event) => updateForm("startTime", event.target.value)} /><i>{t("ClassSetupPage.timeRangeSeparator")}</i><input type="time" value={form.endTime} onChange={(event) => updateForm("endTime", event.target.value)} /></div></label>
            <details className={styles.advanced}><summary>{t("ClassSetupPage.advancedSettings")}</summary><div className={styles.advancedGrid}><label><span>{t("ClassSetupPage.fieldTerm")}</span><input value={form.term} onChange={(event) => updateForm("term", event.target.value)} /></label><label><span>{t("ClassSetupPage.fieldBootLead")}</span><select value={form.bootLeadMinutes} onChange={(event) => updateForm("bootLeadMinutes", Number(event.target.value))}>{BOOT_LEAD_OPTIONS.map((minutes) => <option key={minutes} value={minutes}>{minutes === 0 ? t("ClassSetupPage.bootLeadOnTime") : t("ClassSetupPage.bootLeadMinutesOption", { minutes })}</option>)}</select></label><label><span>{t("ClassSetupPage.fieldShutdownGrace")}</span><select value={form.shutdownGraceMinutes} onChange={(event) => updateForm("shutdownGraceMinutes", Number(event.target.value))}>{SHUTDOWN_GRACE_OPTIONS.map((minutes) => <option key={minutes} value={minutes}>{minutes === 0 ? t("ClassSetupPage.shutdownGraceImmediate") : t("ClassSetupPage.shutdownGraceMinutesOption", { minutes })}</option>)}</select></label></div></details>
          </div>
        </>}

        {step === 2 && <>
          <div className={styles.sectionHeader}><div><h2>{t("ClassSetupPage.step2Title")}</h2><p>{t("ClassSetupPage.step2Desc")}</p></div></div>
          <div className={styles.studentBody}>
            <label><span className={styles.fieldLabelRow}>{t("ClassSetupPage.fieldStudentEmails")}<small>{t("ClassSetupPage.pendingEmailsCount", { count: parseStudentEmails(emails).length })}</small></span><textarea ref={emailsRef} className={invalidField === "emails" ? styles.fieldInvalid : undefined} aria-invalid={invalidField === "emails"} rows={8} value={emails} onChange={(event) => { setEmails(event.target.value); clearInvalid("emails"); }} placeholder={"student01@example.edu\nstudent02@example.edu"} autoFocus /></label>
            <div className={styles.studentTools}>
              <input ref={fileRef} className={styles.hiddenFileInput} type="file" accept=".csv,text/csv" onChange={importCsv} tabIndex={-1} aria-hidden="true" />
              <button type="button" className={styles.btnGhost} disabled={busy} onClick={() => fileRef.current?.click()}><MIcon name="upload" size={16} />{t("ClassSetupPage.importCsvBtn")}</button>
            </div>
            <div className={styles.roster}>
              <div className={styles.rosterHead}><strong>{t("ClassSetupPage.currentStudentsLabel")}</strong><span>{t("ClassSetupPage.studentsCountLabel", { count: item?.students.length ?? 0 })}</span></div>
              <p className={styles.rosterHint}>{item?.students.length ? t("ClassSetupPage.canAddMoreStudents") : t("ClassSetupPage.needAtLeastOneStudent")}</p>
              {item?.students.length > 0 && <ul className={styles.studentList}>
                {item.students.map((student) => {
                  const name = student.full_name || student.email;
                  return <li key={student.id}>
                    <div><strong>{name}</strong>{student.full_name && <small>{student.email}</small>}</div>
                    <button type="button" className={styles.iconBtnDanger} disabled={busy} aria-label={t("ClassSetupPage.removeStudentAria", { name })} onClick={() => removeStudent(student)}><MIcon name="person_remove" size={17} /></button>
                  </li>;
                })}
              </ul>}
            </div>
          </div>
        </>}

        {step === 3 && <>
          <div className={styles.sectionHeader}><div><h2>{t("ClassSetupPage.step3Title")}</h2><p>{t("ClassSetupPage.step3Desc")}</p></div></div>
          {templates.length > 0 ? (
            <div className={`${shared.envChoices} ${styles.envList}`}>
              {templates.map((candidate) => (
                <EnvironmentChoice
                  key={candidate.versionId}
                  candidate={candidate}
                  selected={String(candidate.versionId) === String(templateId)}
                  disabled={busy}
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

          {/* 找不到合適範本是例外，放在清單後面當出口，不要擋在選擇之前；
              一個模板都沒有時上面的空狀態已經給了建立入口，這張卡就不重複出現 */}
          {templates.length > 0 && <div className={styles.templatePauseCard}>
            <div>
              <strong>{t("ClassSetupPage.noSuitableTemplateTitle")}</strong>
              <p>{t("ClassSetupPage.templatePauseDesc")}</p>
              <small><MIcon name="cloud_done" size={14} />{t("ClassSetupPage.noDataLossHint")}</small>
            </div>
            <div className={styles.templatePauseActions}>
              <button type="button" className={styles.btnSecondary} onClick={createTemplate}>
                <MIcon name="add" size={16} />{t("ClassSetupPage.createNewTemplateBtn")}
              </button>
              <button type="button" className={styles.btnGhost} onClick={pauseSetup}>
                {t("ClassSetupPage.saveForLaterBtn")}
              </button>
            </div>
          </div>}
        </>}

        {step === 4 && <>
          <div className={styles.sectionHeader}><div><h2>{t("ClassSetupPage.step4Title")}</h2><p>{t("ClassSetupPage.step4Desc")}</p></div><em>{t("ClassSetupPage.weeksConfiguredCount", { done: taskCount, total: weeks.length })}</em></div>
          <label className={styles.publishToggle}><input type="checkbox" checked={publishWeeks} onChange={(event) => setPublishWeeks(event.target.checked)} /><div><strong>{t("ClassSetupPage.publishWeeksLabel")}</strong><small>{t("ClassSetupPage.publishWeeksHint")}</small></div></label>
          <div className={styles.weekList}>{weeks.map((week, index) => <label key={week.id ?? week.session_date}>
            <span><strong>{t("ClassSetupPage.weekNumberLabel", { week: week.week_number ?? index + 1 })}</strong><small>{formatClassDay(week.session_date)}</small></span>
            {/* 範例只放第一週，18 列都重複同一句反而像是已經填好的內容 */}
            <input value={week.title ?? ""} onChange={(event) => setWeeks((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, title: event.target.value } : row))} placeholder={index === 0 ? t("ClassSetupPage.weekTitlePlaceholder") : undefined} />
          </label>)}</div>
        </>}

        {step === 5 && <>
          <div className={styles.sectionHeader}><div><h2>{t("ClassSetupPage.step5Title")}</h2><p>{t("ClassSetupPage.step5Desc")}</p></div></div>
          <div className={styles.readiness}>
            <span className={`${styles.readyIcon} ${!missingSteps.length && !capacity ? styles.readyIconBusy : ""}`}><MIcon name={missingSteps.length ? "pending_actions" : capacity?.ready ? "check" : "hourglass_top"} size={22} /></span>
            <div>
              <span>{progressText}</span>
              <h3>{missingSteps.length ? t("ClassSetupPage.capacityMissingTitle") : capacity ? capacity.ready ? t("ClassSetupPage.capacityReady") : t("ClassSetupPage.capacityNotReady") : t("ClassSetupPage.capacityChecking")}</h3>
              <p>{missingSteps.length
                ? t("ClassSetupPage.capacityMissingDetail", { list: joinList(missingSteps.map(stepLabel)) })
                : capacity?.ready ? t("ClassSetupPage.capacitySummary", { machines: capacity.machine_count, ips: capacity.ip_count }) : capacity?.issues?.join("；") ?? t("ClassSetupPage.capacityCheckingDetail")}</p>
            </div>
            {capacity?.ready && <div className={styles.capacityFacts}>
              <div><span>{t("ClassSetupPage.capacityFactMachines")}</span><strong>{capacity.machine_count}</strong></div>
              <div><span>CPU</span><strong>{capacity.cpu_cores}</strong></div>
              <div><span>RAM</span><strong>{Math.round(capacity.memory_mb / 1024)} GB</strong></div>
              <div><span>Disk</span><strong>{capacity.disk_gb} GB</strong></div>
              <div><span>{t("ClassSetupPage.capacityFactIps")}</span><strong>{capacity.ip_count}</strong></div>
            </div>}
          </div>
          <div className={styles.summaryList}>
            <SummaryLine done={Boolean(item)} label={t("ClassSetupPage.summaryLabelSchedule")} value={item ? `${formatClassDay(item.start_date)} ${t("ClassSetupPage.scheduleDateTo")} ${formatClassDay(item.end_date)} · ${scheduleShort}` : t("ClassSetupPage.notCreatedYet")} onOpen={() => switchStep(1)} />
            <SummaryLine done={studentsReady} label={t("ClassSetupPage.summaryLabelStudents")} value={t("ClassSetupPage.studentsCountLabel", { count: item?.students.length ?? 0 })} onOpen={() => switchStep(2)} />
            <SummaryLine done={environmentReady} label={t("ClassSetupPage.summaryLabelEnvironment")} value={item?.course_environment ? t("ClassSetupPage.envSummaryValue", { name: item.course_environment.name, count: item.nodes.length }) : t("ClassSetupPage.notSelectedYet")} onOpen={() => switchStep(3)} />
            <SummaryLine done={savedTaskCount > 0} optional label={t("ClassSetupPage.summaryLabelTasks")} value={t("ClassSetupPage.weeksSetSummary", { done: savedTaskCount, total: item?.weeks.length ?? 0, visible: visibleWeekCount(item?.weeks ?? []) })} onOpen={() => switchStep(4)} />
          </div>
        </>}

        {footer}
      </section>

      {step < 5 && draftPanel}
    </div>
  </div>;
}
