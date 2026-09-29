import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import EmptyState from "../../../components/EmptyState/EmptyState";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useUnsavedChanges, useUnsavedChangesGuard } from "../../../contexts/UnsavedChangesContext";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { useToast } from "../../../hooks/useToast";
import { CourseAdminService } from "../../../services/courses";
import { focusInvalidField } from "../../../utils/focusField";
import styles from "./CourseCmsPage.module.scss";

/**
 * 課程內容編輯：左邊樹狀導覽（學習路徑 › 房間 › 任務），右邊工作區只編輯選中的那一層。
 * 文字欄位按「儲存」才送出；有未儲存的修改時，換節點、切分頁、跳離頁面都會先確認。
 * 發布／所屬班級／題目這類單一動作按下就生效。
 */

const DIFFICULTIES = [
  { key: "easy", labelKey: "CourseCmsPage.difficultyEasy" },
  { key: "medium", labelKey: "CourseCmsPage.difficultyMedium" },
  { key: "hard", labelKey: "CourseCmsPage.difficultyHard" },
];

/* ══════════════ 共用小元件 ══════════════ */

/* 樹狀列：展開鈕與選取鈕並排（不巢狀）；葉節點用同寬佔位讓文字對齊 */
function TreeRow({ level, expandable, expanded, onToggle, active, onSelect, label, index }) {
  const { t } = useTranslation("teaching");
  return (
    <div className={`${styles.treeRow} ${active ? styles.treeRowActive : ""}`} style={{ "--level": level }}>
      {expandable ? (
        <button
          type="button"
          className={styles.treeToggle}
          aria-expanded={expanded}
          aria-label={t("CourseCmsPage.toggleAria", { title: label })}
          onClick={onToggle}
        >
          <MIcon name={expanded ? "expand_more" : "chevron_right"} size={18} />
        </button>
      ) : (
        <span className={styles.treeToggleSpacer} aria-hidden="true" />
      )}
      <button type="button" className={styles.treeLabel} aria-current={active ? "true" : undefined} onClick={onSelect}>
        {index != null && <span className={styles.itemIndex}>{index}</span>}
        <span className={styles.treeText}>{label}</span>
      </button>
    </div>
  );
}

/* 樹裡的「＋ 新增」列：點了才變成輸入列，Enter 或 ✓ 新增、Esc 取消 */
function TreeAddRow({ level, label, placeholder, onCreate }) {
  const { t } = useTranslation("teaching");
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);

  function cancel() {
    setValue("");
    setEditing(false);
  }

  async function submit(e) {
    e.preventDefault();
    const title = value.trim();
    if (!title || busy) return;
    setBusy(true);
    const ok = await onCreate(title);
    setBusy(false);
    if (ok) cancel();
  }

  if (!editing) {
    return (
      <li>
        <button type="button" className={styles.treeAdd} style={{ "--level": level }} onClick={() => setEditing(true)}>
          <MIcon name="add" size={16} />
          {label}
        </button>
      </li>
    );
  }
  return (
    <li>
      <form
        className={styles.treeAddForm}
        style={{ "--level": level }}
        onSubmit={submit}
        onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget) && !value.trim()) cancel(); }}
      >
        <input
          autoFocus
          value={value}
          disabled={busy}
          placeholder={placeholder}
          aria-label={placeholder}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") cancel(); }}
        />
        <button type="submit" className={styles.treeAddSubmit} disabled={busy || !value.trim()} aria-label={t("CourseCmsPage.addBtnAria")} title={t("CourseCmsPage.addBtnAria")}>
          <MIcon name="check" size={18} />
        </button>
      </form>
    </li>
  );
}

function WorkspaceHeader({ kind, trail, title, onDelete, deleteLabel }) {
  return (
    <div className={styles.wsHeader}>
      <div className={styles.wsHeading}>
        <p className={styles.wsKind}>{[kind, trail].filter(Boolean).join(" · ")}</p>
        <h2>{title}</h2>
      </div>
      <button type="button" className={styles.wsDeleteBtn} onClick={onDelete}>
        <MIcon name="delete" size={16} />
        {deleteLabel}
      </button>
    </div>
  );
}

/* 儲存列：沒有修改時不能存；有修改時旁邊標「尚未儲存」 */
function SaveRow({ dirty, saving, label }) {
  const { t } = useTranslation("teaching");
  return (
    <div className={styles.editorActions}>
      {dirty && <span className={styles.unsavedHint}>{t("CourseCmsPage.unsavedHint")}</span>}
      <button type="submit" className={styles.saveBtn} disabled={!dirty || saving}>
        <MIcon name={saving ? "hourglass_top" : "save"} size={16} spin={saving} />
        {label}
      </button>
    </div>
  );
}

/* ══════════════ 新增學習路徑（要同時選班級，用對話框） ══════════════ */
function NewPathDialog({ closing, onClose, teachingClasses, linkedClassIds, onCreated }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const [classId, setClassId] = useState("");
  const [title, setTitle] = useState("");
  const [invalid, setInvalid] = useState({});
  const [busy, setBusy] = useState(false);
  const classRef = useRef(null);
  const titleRef = useRef(null);
  const available = teachingClasses.filter((item) => !linkedClassIds.has(String(item.id)));

  async function submit(e) {
    e.preventDefault();
    if (busy) return;
    const missing = { classId: !classId, title: !title.trim() };
    if (missing.classId || missing.title) {
      setInvalid(missing);
      focusInvalidField(missing.classId ? classRef.current : titleRef.current);
      return;
    }
    setBusy(true);
    try {
      const path = await CourseAdminService.createPath({ title: title.trim(), teaching_class_id: classId });
      toast.success(t("CourseCmsPage.pathCreatedToast"));
      onCreated(path);
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.createFailedToast"));
      setBusy(false);
    }
  }

  return (
    <Modal
      as="form"
      onSubmit={submit}
      closing={closing}
      onClose={onClose}
      busy={busy}
      title={t("CourseCmsPage.addPathLabel")}
      actions={<>
        <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={busy}>{t("CourseCmsPage.cancelBtn")}</button>
        <button type="submit" className={styles.btnPrimary} disabled={busy}>{t("CourseCmsPage.createBtn")}</button>
      </>}
    >
      <label className={styles.field}>
        <span>{t("CourseCmsPage.whichClassLabel")}</span>
        <select
          ref={classRef}
          className={invalid.classId ? styles.fieldInvalid : undefined}
          value={classId}
          onChange={(e) => { setClassId(e.target.value); setInvalid((v) => ({ ...v, classId: false })); }}
        >
          <option value="">{t("CourseCmsPage.selectClassFirstOption")}</option>
          {available.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.term}</option>)}
        </select>
        {available.length === 0 && <small className={styles.fieldHint}>{t("CourseCmsPage.noClassAvailableHint")}</small>}
      </label>
      <label className={styles.field}>
        <span>{t("CourseCmsPage.pathTitleLabel")}</span>
        <input
          ref={titleRef}
          className={invalid.title ? styles.fieldInvalid : undefined}
          value={title}
          maxLength={255}
          onChange={(e) => { setTitle(e.target.value); setInvalid((v) => ({ ...v, title: false })); }}
        />
      </label>
    </Modal>
  );
}

/* ══════════════ 工作區：學習路徑 ══════════════ */
function PathWorkspace({ path, teachingClasses, linkedClassIds, onChanged, onDeleted }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  const [title, setTitle] = useState(path.title);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const published = path.status === "published";
  const dirty = title !== path.title;
  useUnsavedChangesGuard(dirty);

  async function save(e) {
    e.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    try {
      await CourseAdminService.updatePath(path.id, { title: title.trim() });
      setTitle(title.trim());
      await onChanged();
      toast.success(t("CourseCmsPage.pathSavedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.saveFailedToast"));
    } finally {
      setSaving(false);
    }
  }

  async function togglePublish() {
    setPublishing(true);
    try {
      await CourseAdminService.publishPath(path.id, !published);
      await onChanged();
      toast.success(published ? t("CourseCmsPage.unpublishedToast") : t("CourseCmsPage.publishedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.actionFailedToast"));
    } finally {
      setPublishing(false);
    }
  }

  async function linkClass(value) {
    try {
      await CourseAdminService.updatePath(path.id, { teaching_class_id: value || null });
      await onChanged();
      toast.success(value ? t("CourseCmsPage.classLinkedToast") : t("CourseCmsPage.classUnlinkedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.linkFailedToast"));
    }
  }

  async function remove() {
    const ok = await confirm({
      title: t("CourseCmsPage.deletePathConfirmTitle"),
      message: t("CourseCmsPage.deletePathConfirmMessage", { title: path.title }),
      confirmText: t("CourseCmsPage.deleteLabel"),
      danger: true,
    });
    if (!ok) return;
    try {
      await CourseAdminService.deletePath(path.id);
      toast.success(t("CourseCmsPage.deletedToast"));
      onDeleted();
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.deleteFailedToast"));
    }
  }

  return (
    <>
      <WorkspaceHeader kind={t("CourseCmsPage.kindPath")} title={path.title} onDelete={remove} deleteLabel={t("CourseCmsPage.deletePathBtn")} />
      <div className={styles.wsBody}>
        <form className={styles.wsSection} onSubmit={save}>
          <label className={styles.field}>
            <span>{t("CourseCmsPage.pathTitleLabel")}</span>
            <input value={title} maxLength={255} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <SaveRow dirty={dirty && Boolean(title.trim())} saving={saving} label={t("CourseCmsPage.saveBtn")} />
        </form>

        {/* 發布狀態與動作寫成文字放在一起：圖示鈕分不出是「狀態」還是「動作」 */}
        <section className={styles.wsSection}>
          <div className={styles.publishRow}>
            <div className={styles.publishInfo}>
              <span className={styles.formLabel}>{t("CourseCmsPage.publishStatusLabel")}</span>
              <span className={styles.publishState}>
                <span className={`${styles.statusDot} ${published ? styles.statusDotOn : ""}`} aria-hidden="true" />
                {published ? t("CourseCmsPage.publishedHint") : t("CourseCmsPage.draftHint")}
              </span>
            </div>
            <button type="button" className={styles.btnSecondary} disabled={publishing} onClick={togglePublish}>
              {published ? t("CourseCmsPage.unpublishLabel") : t("CourseCmsPage.publishLabel")}
            </button>
          </div>
        </section>

        <section className={styles.wsSection}>
          <label className={styles.field}>
            <span>{t("CourseCmsPage.whichClassLabel")}</span>
            <select value={path.teaching_class_id ?? ""} onChange={(e) => linkClass(e.target.value)}>
              <option value="">{t("CourseCmsPage.notLinkedOption")}</option>
              {teachingClasses.map((item) => (
                <option
                  key={item.id}
                  value={item.id}
                  disabled={linkedClassIds.has(String(item.id)) && String(item.id) !== String(path.teaching_class_id)}
                >
                  {item.name} · {item.term}
                </option>
              ))}
            </select>
            <small className={styles.fieldHint}>{t("CourseCmsPage.linkHint")}</small>
          </label>
        </section>
      </div>
    </>
  );
}

/* ══════════════ 工作區：房間 ══════════════ */
function RoomWorkspace({ path, room, onChanged, onDeleted }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  const [title, setTitle] = useState(room.title);
  const [difficulty, setDifficulty] = useState(room.difficulty);
  const [saving, setSaving] = useState(false);
  const dirty = title !== room.title || difficulty !== room.difficulty;
  useUnsavedChangesGuard(dirty);

  async function save(e) {
    e.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    try {
      await CourseAdminService.updateRoom(room.id, { title: title.trim(), difficulty });
      setTitle(title.trim());
      await onChanged();
      toast.success(t("CourseCmsPage.roomSavedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.saveFailedToast"));
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    const ok = await confirm({
      title: t("CourseCmsPage.deleteRoomConfirmTitle"),
      message: t("CourseCmsPage.deleteRoomConfirmMessage", { title: room.title }),
      confirmText: t("CourseCmsPage.deleteLabel"),
      danger: true,
    });
    if (!ok) return;
    try {
      await CourseAdminService.deleteRoom(room.id);
      toast.success(t("CourseCmsPage.deletedToast"));
      onDeleted();
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.deleteFailedToast"));
    }
  }

  return (
    <>
      <WorkspaceHeader kind={t("CourseCmsPage.kindRoom")} trail={path.title} title={room.title} onDelete={remove} deleteLabel={t("CourseCmsPage.deleteRoomBtn")} />
      <div className={styles.wsBody}>
        <form className={styles.wsSection} onSubmit={save}>
          <label className={styles.field}>
            <span>{t("CourseCmsPage.roomTitleLabel")}</span>
            <input value={title} maxLength={255} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <div className={styles.field}>
            <span>{t("CourseCmsPage.difficultyLabel")}</span>
            <SegmentedControl
              className={styles.segmentFit}
              options={DIFFICULTIES.map((d) => ({ value: d.key, label: t(d.labelKey) }))}
              value={difficulty}
              onChange={setDifficulty}
              ariaLabel={t("CourseCmsPage.difficultyLabel")}
            />
          </div>
          <SaveRow dirty={dirty && Boolean(title.trim())} saving={saving} label={t("CourseCmsPage.saveBtn")} />
        </form>
      </div>
    </>
  );
}

/* ══════════════ 題目對話框（新增／編輯共用） ══════════════ */
function QuestionDialog({ question, taskId, order, closing, onClose, onSaved }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const editing = Boolean(question);
  const [type, setType] = useState(question?.question_type ?? "flag");
  const [prompt, setPrompt] = useState(question?.prompt ?? "");
  const [answer, setAnswer] = useState("");
  const [points, setPoints] = useState(String(question?.points ?? 10));
  const [invalid, setInvalid] = useState({});
  const [busy, setBusy] = useState(false);
  const promptRef = useRef(null);
  const answerRef = useRef(null);
  /* 答案存的是雜湊、讀不回原文：編輯既有的答案題時可以留空沿用；新題目或從閱讀題改成答案題就一定要填 */
  const answerRequired = type === "flag" && (!editing || question.question_type !== "flag");

  async function submit(e) {
    e.preventDefault();
    if (busy) return;
    const missing = { prompt: !prompt.trim(), answer: answerRequired && !answer.trim() };
    if (missing.prompt || missing.answer) {
      setInvalid(missing);
      focusInvalidField(missing.prompt ? promptRef.current : answerRef.current);
      return;
    }
    const body = {
      prompt: prompt.trim(),
      question_type: type,
      points: Math.max(0, Number(points) || 0),
      ...(type === "flag" && answer.trim() ? { flag: answer } : {}),
    };
    setBusy(true);
    try {
      if (editing) {
        await CourseAdminService.updateQuestion(question.id, body);
        toast.success(t("CourseCmsPage.questionUpdatedToast"));
      } else {
        await CourseAdminService.createQuestion({ ...body, task_id: taskId, order });
        toast.success(t("CourseCmsPage.questionAddedToast"));
      }
      onSaved();
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.saveFailedToast"));
      setBusy(false);
    }
  }

  return (
    <Modal
      as="form"
      onSubmit={submit}
      closing={closing}
      onClose={onClose}
      busy={busy}
      size="log"
      title={editing ? t("CourseCmsPage.editQuestionTitle") : t("CourseCmsPage.addQuestionLabel")}
      actions={<>
        <button type="button" className={styles.btnSecondary} onClick={onClose} disabled={busy}>{t("CourseCmsPage.cancelBtn")}</button>
        <button type="submit" className={styles.btnPrimary} disabled={busy}>{editing ? t("CourseCmsPage.saveBtn") : t("CourseCmsPage.createBtn")}</button>
      </>}
    >
      <div className={styles.field}>
        <span>{t("CourseCmsPage.questionTypeLabel")}</span>
        <SegmentedControl
          className={styles.segmentFit}
          options={[
            { value: "flag", label: t("CourseCmsPage.flagQuestionOption") },
            { value: "no_answer", label: t("CourseCmsPage.readingQuestionShort") },
          ]}
          value={type}
          onChange={(value) => { setType(value); setInvalid((v) => ({ ...v, answer: false })); }}
          ariaLabel={t("CourseCmsPage.questionTypeLabel")}
        />
        <small className={styles.fieldHint}>{type === "flag" ? t("CourseCmsPage.flagQuestionHint") : t("CourseCmsPage.readingQuestionHint")}</small>
      </div>
      <label className={styles.field}>
        <span>{t("CourseCmsPage.questionPromptPlaceholder")}</span>
        <textarea
          ref={promptRef}
          rows={3}
          maxLength={1000}
          className={invalid.prompt ? styles.fieldInvalid : undefined}
          value={prompt}
          onChange={(e) => { setPrompt(e.target.value); setInvalid((v) => ({ ...v, prompt: false })); }}
        />
      </label>
      {type === "flag" && (
        <label className={styles.field}>
          <span>{t("CourseCmsPage.flagAnswerLabel")}</span>
          <input
            ref={answerRef}
            maxLength={500}
            className={invalid.answer ? styles.fieldInvalid : undefined}
            value={answer}
            placeholder={answerRequired ? undefined : t("CourseCmsPage.flagAnswerKeepHint")}
            onChange={(e) => { setAnswer(e.target.value); setInvalid((v) => ({ ...v, answer: false })); }}
          />
          <small className={styles.fieldHint}>{t("CourseCmsPage.flagAnswerRuleHint")}</small>
        </label>
      )}
      <label className={styles.field}>
        <span>{t("CourseCmsPage.pointsFieldTitle")}</span>
        <span className={styles.pointsField}>
          <input type="number" min="0" max="1000" value={points} onChange={(e) => setPoints(e.target.value)} />
          <span>{t("CourseCmsPage.pointsSuffix")}</span>
        </span>
      </label>
    </Modal>
  );
}

/* ══════════════ 題目區 ══════════════ */
function QuestionSection({ taskId }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  const [questions, setQuestions] = useState([]);
  /* 對話框目標：null＝關閉、"new"＝新增、題目物件＝編輯 */
  const [dialogTarget, setDialogTarget] = useState(null);
  const dialog = useDialogPresence(dialogTarget);

  const reload = useCallback(() => {
    CourseAdminService.listQuestions(taskId).then(setQuestions).catch(() => {});
  }, [taskId]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function remove(q) {
    const ok = await confirm({
      title: t("CourseCmsPage.deleteQuestionConfirmTitle"),
      message: t("CourseCmsPage.deleteQuestionConfirmMessage"),
      confirmText: t("CourseCmsPage.deleteLabel"),
      danger: true,
    });
    if (!ok) return;
    try {
      await CourseAdminService.deleteQuestion(q.id);
      reload();
      toast.success(t("CourseCmsPage.deletedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.deleteFailedToast"));
    }
  }

  return (
    <section className={styles.wsSection}>
      <div className={styles.questionsHeader}>
        <h3 className={styles.wsSectionTitle}>
          {t("CourseCmsPage.questionsTitle")}
          <span className={styles.countBadge}>{questions.length}</span>
        </h3>
        <button type="button" className={styles.btnSecondary} onClick={() => setDialogTarget("new")}>
          <MIcon name="add" size={16} />
          {t("CourseCmsPage.addQuestionLabel")}
        </button>
      </div>
      {questions.length === 0 ? (
        <p className={styles.noQuestions}>{t("CourseCmsPage.noQuestionsText")}</p>
      ) : (
        <ul className={styles.questionList}>
          {questions.map((q) => (
            <li key={q.id} className={styles.questionRow}>
              <button type="button" className={styles.questionMain} onClick={() => setDialogTarget(q)} aria-label={t("CourseCmsPage.editQuestionAria", { prompt: q.prompt })}>
                <MIcon name={q.question_type === "flag" ? "quiz" : "menu_book"} size={16} />
                <span className={styles.questionPrompt}>{q.prompt}</span>
                <span className={styles.questionMeta}>
                  {q.question_type === "flag"
                    ? `${t("CourseCmsPage.flagQuestionOption")} · ${t("CourseCmsPage.pointsUnit", { points: q.points })}`
                    : t("CourseCmsPage.readingQuestionShort")}
                </span>
              </button>
              <button
                type="button"
                className={styles.questionDelete}
                aria-label={t("CourseCmsPage.deleteLabel")}
                title={t("CourseCmsPage.deleteLabel")}
                onClick={() => remove(q)}
              >
                <MIcon name="delete" size={16} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {dialog.item && (
        <QuestionDialog
          key={dialog.item === "new" ? "new" : dialog.item.id}
          question={dialog.item === "new" ? null : dialog.item}
          taskId={taskId}
          order={questions.length}
          closing={dialog.closing}
          onClose={() => setDialogTarget(null)}
          onSaved={() => { setDialogTarget(null); reload(); }}
        />
      )}
    </section>
  );
}

/* ══════════════ 工作區：任務 ══════════════ */
function TaskWorkspace({ path, room, task, onChanged, onDeleted }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  const [title, setTitle] = useState(task.title);
  const [content, setContent] = useState(task.content ?? "");
  const [saving, setSaving] = useState(false);
  const dirty = title !== task.title || content !== (task.content ?? "");
  /* 不自動儲存：有未儲存的修改時，換節點、切分頁、跳離頁面都會先確認 */
  useUnsavedChangesGuard(dirty);

  async function save(e) {
    e.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    try {
      await CourseAdminService.updateTask(task.id, { title: title.trim(), content });
      setTitle(title.trim());
      await onChanged();
      toast.success(t("CourseCmsPage.taskSavedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.saveFailedToast"));
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    const ok = await confirm({
      title: t("CourseCmsPage.deleteTaskConfirmTitle"),
      message: t("CourseCmsPage.deleteTaskConfirmMessage", { title: task.title }),
      confirmText: t("CourseCmsPage.deleteLabel"),
      danger: true,
    });
    if (!ok) return;
    try {
      await CourseAdminService.deleteTask(task.id);
      toast.success(t("CourseCmsPage.deletedToast"));
      onDeleted();
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.deleteFailedToast"));
    }
  }

  return (
    <>
      <WorkspaceHeader kind={t("CourseCmsPage.kindTask")} trail={`${path.title} › ${room.title}`} title={task.title} onDelete={remove} deleteLabel={t("CourseCmsPage.deleteTaskBtn")} />
      <div className={styles.wsBody}>
        <form className={styles.wsSection} onSubmit={save}>
          <label className={styles.field}>
            <span>{t("CourseCmsPage.taskTitleLabel")}</span>
            <input value={title} maxLength={255} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className={styles.field}>
            <span>{t("CourseCmsPage.taskContentLabel")}</span>
            <textarea className={styles.contentInput} value={content} onChange={(e) => setContent(e.target.value)} />
          </label>
          <SaveRow dirty={dirty && Boolean(title.trim())} saving={saving} label={t("CourseCmsPage.saveTaskBtn")} />
        </form>
        <QuestionSection taskId={task.id} />
      </div>
    </>
  );
}

/* ══════════════ 主元件 ══════════════ */
export default function ContentEditor({ paths, teachingClasses, initialPathId, onReloadPaths }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const { confirmLeave } = useUnsavedChanges();
  const [roomsByPath, setRoomsByPath] = useState({});
  const [tasksByRoom, setTasksByRoom] = useState({});
  const [openPaths, setOpenPaths] = useState(() => new Set(initialPathId ? [initialPathId] : []));
  const [openRooms, setOpenRooms] = useState(() => new Set());
  /* 選中的節點：{ type: "path"|"room"|"task", id, pathId?, roomId? } */
  const [selection, setSelection] = useState(() => (initialPathId ? { type: "path", id: initialPathId } : null));
  const [newPathOpen, setNewPathOpen] = useState(false);
  const newPathDialog = useDialogPresence(newPathOpen);
  const linkedClassIds = new Set(paths.map((path) => String(path.teaching_class_id ?? "")).filter(Boolean));

  const loadRooms = useCallback(async (pathId) => {
    try {
      const rows = await CourseAdminService.listRooms(pathId);
      setRoomsByPath((map) => ({ ...map, [pathId]: rows }));
    } catch { /* 讀不到就維持原樣，下次展開再試 */ }
  }, []);

  const loadTasks = useCallback(async (roomId) => {
    try {
      const rows = await CourseAdminService.listTasks(roomId);
      setTasksByRoom((map) => ({ ...map, [roomId]: rows }));
    } catch { /* 同上 */ }
  }, []);

  /* 展開時才載入子節點 */
  useEffect(() => {
    openPaths.forEach((id) => { if (!(id in roomsByPath)) loadRooms(id); });
  }, [openPaths]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    openRooms.forEach((id) => { if (!(id in tasksByRoom)) loadTasks(id); });
  }, [openRooms]); // eslint-disable-line react-hooks/exhaustive-deps

  function toggle(setter, id) {
    setter((set) => {
      const next = new Set(set);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function expand(setter, id) {
    setter((set) => (set.has(id) ? set : new Set(set).add(id)));
  }

  const isSelected = (type, id) => selection?.type === type && selection.id === id;

  async function select(next) {
    if (isSelected(next.type, next.id)) return;
    if (!(await confirmLeave())) return;
    setSelection(next);
    expand(setOpenPaths, next.type === "path" ? next.id : next.pathId);
    if (next.type !== "path") expand(setOpenRooms, next.type === "room" ? next.id : next.roomId);
  }

  async function createRoom(path, title) {
    try {
      const room = await CourseAdminService.createRoom({
        path_id: path.id,
        title,
        difficulty: "easy",
        order: (roomsByPath[path.id] ?? []).length,
      });
      await loadRooms(path.id);
      onReloadPaths();
      toast.success(t("CourseCmsPage.roomCreatedToast"));
      /* 建好直接選中，接著就能設難度 */
      if (room?.id) select({ type: "room", id: room.id, pathId: path.id });
      return true;
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.createFailedToast"));
      return false;
    }
  }

  async function createTask(path, room, title) {
    try {
      const task = await CourseAdminService.createTask({
        room_id: room.id,
        title,
        content: "",
        order: (tasksByRoom[room.id] ?? []).length,
      });
      await loadTasks(room.id);
      loadRooms(path.id);
      toast.success(t("CourseCmsPage.taskAddedToast"));
      /* 建好直接選中，接著就能寫內容、加題目 */
      if (task?.id) select({ type: "task", id: task.id, roomId: room.id, pathId: path.id });
      return true;
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.addFailedToast"));
      return false;
    }
  }

  const selectedPathId = selection?.type === "path" ? selection?.id : selection?.pathId;
  const selectedPath = paths.find((path) => path.id === selectedPathId);
  const selectedRoomId = selection?.type === "room" ? selection.id : selection?.roomId;
  const selectedRoom = selectedPathId ? roomsByPath[selectedPathId]?.find((room) => room.id === selectedRoomId) : null;
  const selectedTask = selection?.type === "task" ? tasksByRoom[selection.roomId]?.find((task) => task.id === selection.id) : null;

  let workspace = <EmptyState icon="account_tree" iconSize={32} title={t("CourseCmsPage.selectNodeHint")} />;
  if (selection?.type === "path" && selectedPath) {
    workspace = (
      <PathWorkspace
        key={selectedPath.id}
        path={selectedPath}
        teachingClasses={teachingClasses}
        linkedClassIds={linkedClassIds}
        onChanged={onReloadPaths}
        onDeleted={() => { setSelection(null); onReloadPaths(); }}
      />
    );
  } else if (selection?.type === "room" && selectedPath && selectedRoom) {
    workspace = (
      <RoomWorkspace
        key={selectedRoom.id}
        path={selectedPath}
        room={selectedRoom}
        onChanged={() => loadRooms(selectedPath.id)}
        onDeleted={() => { setSelection({ type: "path", id: selectedPath.id }); loadRooms(selectedPath.id); onReloadPaths(); }}
      />
    );
  } else if (selection?.type === "task" && selectedPath && selectedRoom && selectedTask) {
    workspace = (
      <TaskWorkspace
        key={selectedTask.id}
        path={selectedPath}
        room={selectedRoom}
        task={selectedTask}
        onChanged={() => loadTasks(selectedRoom.id)}
        onDeleted={() => {
          setSelection({ type: "room", id: selectedRoom.id, pathId: selectedPath.id });
          loadTasks(selectedRoom.id);
          loadRooms(selectedPath.id);
        }}
      />
    );
  }

  return (
    <div className={styles.editorLayout}>
      <nav className={styles.navPanel} aria-label={t("CourseCmsPage.contentTreeTitle")}>
        <div className={styles.navHeader}>
          <h2>{t("CourseCmsPage.contentTreeTitle")}</h2>
          <button
            type="button"
            className={styles.navAddBtn}
            aria-label={t("CourseCmsPage.addPathLabel")}
            title={t("CourseCmsPage.addPathLabel")}
            onClick={() => setNewPathOpen(true)}
          >
            <MIcon name="add" size={18} />
          </button>
        </div>
        <div className={styles.navBody}>
          {paths.length === 0 ? (
            <EmptyState icon="topic" iconSize={24} title={t("CourseCmsPage.noPathsTitle")}
              action={<button type="button" className={styles.btnPrimary} onClick={() => setNewPathOpen(true)}><MIcon name="add" size={16} />{t("CourseCmsPage.addPathLabel")}</button>} />
          ) : (
            <ul className={styles.tree}>
              {paths.map((path) => {
                const pathOpen = openPaths.has(path.id);
                const rooms = roomsByPath[path.id];
                return (
                  <li key={path.id}>
                    <TreeRow
                      level={0}
                      expandable
                      expanded={pathOpen}
                      onToggle={() => toggle(setOpenPaths, path.id)}
                      active={isSelected("path", path.id)}
                      onSelect={() => select({ type: "path", id: path.id })}
                      label={path.title}
                    />
                    {pathOpen && (
                      <ul>
                        {rooms === undefined && <li className={styles.treeLoading} style={{ "--level": 1 }}>{t("CourseCmsPage.treeLoading")}</li>}
                        {rooms?.map((room) => {
                          const roomOpen = openRooms.has(room.id);
                          const tasks = tasksByRoom[room.id];
                          return (
                            <li key={room.id}>
                              <TreeRow
                                level={1}
                                expandable
                                expanded={roomOpen}
                                onToggle={() => toggle(setOpenRooms, room.id)}
                                active={isSelected("room", room.id)}
                                onSelect={() => select({ type: "room", id: room.id, pathId: path.id })}
                                label={room.title}
                              />
                              {roomOpen && (
                                <ul>
                                  {tasks === undefined && <li className={styles.treeLoading} style={{ "--level": 2 }}>{t("CourseCmsPage.treeLoading")}</li>}
                                  {tasks?.map((task, i) => (
                                    <li key={task.id}>
                                      <TreeRow
                                        level={2}
                                        index={i + 1}
                                        active={isSelected("task", task.id)}
                                        onSelect={() => select({ type: "task", id: task.id, roomId: room.id, pathId: path.id })}
                                        label={task.title}
                                      />
                                    </li>
                                  ))}
                                  {tasks && (
                                    <TreeAddRow
                                      level={2}
                                      label={t("CourseCmsPage.addTaskLabel")}
                                      placeholder={t("CourseCmsPage.newTaskPlaceholder")}
                                      onCreate={(title) => createTask(path, room, title)}
                                    />
                                  )}
                                </ul>
                              )}
                            </li>
                          );
                        })}
                        {rooms && (
                          <TreeAddRow
                            level={1}
                            label={t("CourseCmsPage.addRoomLabel")}
                            placeholder={t("CourseCmsPage.newRoomPlaceholder")}
                            onCreate={(title) => createRoom(path, title)}
                          />
                        )}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </nav>

      <section className={styles.workspace}>{workspace}</section>

      {newPathDialog.open && (
        <NewPathDialog
          closing={newPathDialog.closing}
          onClose={() => setNewPathOpen(false)}
          teachingClasses={teachingClasses}
          linkedClassIds={linkedClassIds}
          onCreated={async (path) => {
            setNewPathOpen(false);
            await onReloadPaths();
            if (path?.id) select({ type: "path", id: path.id });
          }}
        />
      )}
    </div>
  );
}
