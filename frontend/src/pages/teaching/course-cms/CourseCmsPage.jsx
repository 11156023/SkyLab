import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import LoadingState from "../../../components/LoadingState/LoadingState";
import EmptyState from "../../../components/EmptyState/EmptyState";
import MIcon from "../../../components/MIcon";
import { useAuth } from "../../../contexts/AuthContext";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import { AuthStorage } from "../../../services/auth";
import {
  CourseAdminService,
  courseProgressWsUrl,
} from "../../../services/courses";
import { TemplatesService } from "../../../services/templates";
import { TeachingClassesService } from "../../../services/teachingClasses";
import { focusInvalidField } from "../../../utils/focusField";
import styles from "./CourseCmsPage.module.scss";
import PageHeader from "../../../components/PageHeader/PageHeader";

const DIFFICULTIES = [
  { key: "easy", labelKey: "CourseCmsPage.difficultyEasy" },
  { key: "medium", labelKey: "CourseCmsPage.difficultyMedium" },
  { key: "hard", labelKey: "CourseCmsPage.difficultyHard" },
];

/* ══════════════ 路徑欄 ══════════════ */
function PathColumn({ paths, teachingClasses, selectedId, onSelect, onReload }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  const [title, setTitle] = useState("");
  const [classId, setClassId] = useState("");
  const [invalid, setInvalid] = useState({});
  const classSelectRef = useRef(null);
  const titleInputRef = useRef(null);
  const selectedPath = paths.find((path) => path.id === selectedId);
  const linkedClassIds = new Set(paths.map((path) => String(path.teaching_class_id ?? "")).filter(Boolean));

  async function handleCreate(e) {
    e.preventDefault();
    const missing = { classId: !classId, title: !title.trim() };
    if (missing.classId || missing.title) {
      setInvalid(missing);
      focusInvalidField(missing.classId ? classSelectRef.current : titleInputRef.current);
      return;
    }
    try {
      await CourseAdminService.createPath({
        title: title.trim(),
        teaching_class_id: classId,
      });
      setTitle("");
      setClassId("");
      onReload();
      toast.success(t("CourseCmsPage.pathCreatedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.createFailedToast"));
    }
  }

  async function handleClassLink(value) {
    if (!selectedPath) return;
    try {
      await CourseAdminService.updatePath(selectedPath.id, {
        teaching_class_id: value || null,
      });
      onReload();
      toast.success(value ? t("CourseCmsPage.classLinkedToast") : t("CourseCmsPage.classUnlinkedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.linkFailedToast"));
    }
  }

  async function handlePublish(path, e) {
    e.stopPropagation();
    try {
      await CourseAdminService.publishPath(path.id, path.status !== "published");
      onReload();
      toast.success(path.status === "published" ? t("CourseCmsPage.unpublishedToast") : t("CourseCmsPage.publishedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.actionFailedToast"));
    }
  }

  async function handleDelete(path, e) {
    e.stopPropagation();
    const ok = await confirm({
      title: t("CourseCmsPage.deletePathConfirmTitle"),
      message: t("CourseCmsPage.deletePathConfirmMessage", { title: path.title }),
      confirmText: t("CourseCmsPage.deleteLabel"),
      danger: true,
    });
    if (!ok) return;
    try {
      await CourseAdminService.deletePath(path.id);
      onReload();
      toast.success(t("CourseCmsPage.deletedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.deleteFailedToast"));
    }
  }

  return (
    <div className={styles.column}>
      <div className={styles.columnHeader}>{t("CourseCmsPage.pathColumnHeader")}</div>
      <div className={styles.columnBody}>
        {paths.map((path) => (
          <div
            key={path.id}
            className={`${styles.item} ${selectedId === path.id ? styles.itemActive : ""}`}
            onClick={() => onSelect(path.id)}
          >
            <span
              className={`${styles.pubDot} ${path.status === "published" ? styles.pub_on : ""}`}
              title={path.status === "published" ? t("CourseCmsPage.publishedLabel") : t("CourseCmsPage.draftLabel")}
            />
            <span className={styles.itemLabel}>{path.title}</span>
            <span className={styles.itemMeta}>{path.teaching_class_name ?? t("CourseCmsPage.roomCountUnit", { count: path.room_count })}</span>
            <button
              type="button"
              className={styles.iconBtn}
              title={path.status === "published" ? t("CourseCmsPage.unpublishLabel") : t("CourseCmsPage.publishLabel")}
              onClick={(e) => handlePublish(path, e)}
            >
              <MIcon name={path.status === "published" ? "visibility_off" : "publish"} size={15} />
            </button>
            <button
              type="button"
              className={styles.iconBtn}
              title={t("CourseCmsPage.deleteLabel")}
              onClick={(e) => handleDelete(path, e)}
            >
              <MIcon name="delete" size={15} />
            </button>
          </div>
        ))}
        {paths.length === 0 && <EmptyState icon="topic" iconSize={24} title={t("CourseCmsPage.noPathsTitle")} />}
      </div>
      {selectedPath && (
        <label className={styles.pathClassLink}>
          <span>{t("CourseCmsPage.whichClassLabel")}</span>
          <select
            value={selectedPath.teaching_class_id ?? ""}
            onChange={(event) => handleClassLink(event.target.value)}
          >
            <option value="">{t("CourseCmsPage.notLinkedOption")}</option>
            {teachingClasses.map((item) => (
              <option
                key={item.id}
                value={item.id}
                disabled={linkedClassIds.has(String(item.id)) && String(item.id) !== String(selectedPath.teaching_class_id)}
              >
                {item.name} · {item.term}
              </option>
            ))}
          </select>
          <small>{t("CourseCmsPage.linkHint")}</small>
        </label>
      )}
      <form className={styles.addForm} onSubmit={handleCreate}>
        <select
          ref={classSelectRef}
          className={invalid.classId ? styles.fieldInvalid : undefined}
          value={classId}
          onChange={(event) => { setClassId(event.target.value); setInvalid((v) => ({ ...v, classId: false })); }}
          aria-label={t("CourseCmsPage.selectClassAria")}
        >
          <option value="">{t("CourseCmsPage.selectClassFirstOption")}</option>
          {teachingClasses.filter((item) => !linkedClassIds.has(String(item.id))).map((item) => (
            <option key={item.id} value={item.id}>{item.name} · {item.term}</option>
          ))}
        </select>
        <input
          ref={titleInputRef}
          className={`${styles.input} ${invalid.title ? styles.fieldInvalid : ""}`}
          value={title}
          onChange={(e) => { setTitle(e.target.value); setInvalid((v) => ({ ...v, title: false })); }}
          placeholder={t("CourseCmsPage.newPathPlaceholder")}
        />
        <button type="submit" className={styles.addBtn}>
          <MIcon name="add" size={16} />
        </button>
      </form>
    </div>
  );
}

/* ══════════════ 房間欄 ══════════════ */
function RoomColumn({ pathId, rooms, templates, selectedId, onSelect, onReload }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  const [form, setForm] = useState({ title: "", difficulty: "easy", template_id: "" });
  const [titleInvalid, setTitleInvalid] = useState(false);
  const titleInputRef = useRef(null);

  async function handleCreate(e) {
    e.preventDefault();
    if (!form.title.trim()) {
      setTitleInvalid(true);
      focusInvalidField(titleInputRef.current);
      return;
    }
    try {
      await CourseAdminService.createRoom({
        path_id: pathId,
        title: form.title.trim(),
        difficulty: form.difficulty,
        template_id: form.template_id || null,
        order: rooms.length,
      });
      setForm({ title: "", difficulty: "easy", template_id: "" });
      onReload();
      toast.success(t("CourseCmsPage.roomCreatedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.createFailedToast"));
    }
  }

  async function handleDelete(room, e) {
    e.stopPropagation();
    const ok = await confirm({
      title: t("CourseCmsPage.deleteRoomConfirmTitle"),
      message: t("CourseCmsPage.deleteRoomConfirmMessage", { title: room.title }),
      confirmText: t("CourseCmsPage.deleteLabel"),
      danger: true,
    });
    if (!ok) return;
    try {
      await CourseAdminService.deleteRoom(room.id);
      onReload();
      toast.success(t("CourseCmsPage.deletedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.deleteFailedToast"));
    }
  }

  return (
    <div className={styles.column}>
      <div className={styles.columnHeader}>{t("CourseCmsPage.roomColumnHeader")}</div>
      <div className={styles.columnBody}>
        {rooms.map((room) => (
          <div
            key={room.id}
            className={`${styles.item} ${selectedId === room.id ? styles.itemActive : ""}`}
            onClick={() => onSelect(room.id)}
          >
            <MIcon name={room.template_id ? "computer" : "menu_book"} size={15} />
            <span className={styles.itemLabel}>{room.title}</span>
            <span className={styles.itemMeta}>
              {room.template_name ?? t("CourseCmsPage.pureTheoryLabel")} · {t("CourseCmsPage.taskCountUnit", { count: room.task_count })}
            </span>
            <button
              type="button"
              className={styles.iconBtn}
              title={t("CourseCmsPage.deleteLabel")}
              onClick={(e) => handleDelete(room, e)}
            >
              <MIcon name="delete" size={15} />
            </button>
          </div>
        ))}
        {rooms.length === 0 && <EmptyState icon="meeting_room" iconSize={24} title={t("CourseCmsPage.noRoomsTitle")} />}
      </div>
      <form className={styles.addForm} onSubmit={handleCreate}>
        <input
          ref={titleInputRef}
          className={`${styles.input} ${titleInvalid ? styles.fieldInvalid : ""}`}
          value={form.title}
          onChange={(e) => { setForm((f) => ({ ...f, title: e.target.value })); setTitleInvalid(false); }}
          placeholder={t("CourseCmsPage.newRoomPlaceholder")}
        />
        <select
          className={styles.select}
          value={form.difficulty}
          onChange={(e) => setForm((f) => ({ ...f, difficulty: e.target.value }))}
        >
          {DIFFICULTIES.map((d) => (
            <option key={d.key} value={d.key}>{t(d.labelKey)}</option>
          ))}
        </select>
        <select
          className={styles.select}
          value={form.template_id}
          onChange={(e) => setForm((f) => ({ ...f, template_id: e.target.value }))}
        >
          <option value="">{t("CourseCmsPage.noTemplateOption")}</option>
          {templates.map((tpl) => (
            <option key={tpl.id} value={tpl.id}>
              {t("CourseCmsPage.templateOptionLabel", { name: tpl.name, type: tpl.resource_type === "lxc" ? "LXC" : "VM" })}
            </option>
          ))}
        </select>
        <button type="submit" className={styles.addBtn}>
          <MIcon name="add" size={16} />
        </button>
      </form>
    </div>
  );
}

/* ══════════════ 題目編輯 ══════════════ */
function QuestionEditor({ taskId }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  const [questions, setQuestions] = useState([]);
  const [form, setForm] = useState({ prompt: "", question_type: "flag", flag: "", points: 10 });
  const [invalid, setInvalid] = useState({});
  const promptInputRef = useRef(null);
  const flagInputRef = useRef(null);

  const reload = useCallback(() => {
    CourseAdminService.listQuestions(taskId).then(setQuestions).catch(() => {});
  }, [taskId]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleCreate(e) {
    e.preventDefault();
    const missing = {
      prompt: !form.prompt.trim(),
      flag: form.question_type === "flag" && !form.flag.trim(),
    };
    if (missing.prompt || missing.flag) {
      setInvalid(missing);
      focusInvalidField(missing.prompt ? promptInputRef.current : flagInputRef.current);
      return;
    }
    try {
      await CourseAdminService.createQuestion({
        task_id: taskId,
        prompt: form.prompt.trim(),
        question_type: form.question_type,
        flag: form.question_type === "flag" ? form.flag : null,
        points: Number(form.points) || 0,
        order: questions.length,
      });
      setForm({ prompt: "", question_type: "flag", flag: "", points: 10 });
      reload();
      toast.success(t("CourseCmsPage.questionAddedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.addFailedToast"));
    }
  }

  async function handleDelete(q) {
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
    <div className={styles.questionBlock}>
      <div className={styles.questionHeader}>{t("CourseCmsPage.questionHeaderCount", { count: questions.length })}</div>
      {questions.map((q) => (
        <div key={q.id} className={styles.questionRow}>
          <MIcon name={q.question_type === "flag" ? "flag" : "menu_book"} size={14} />
          <span className={styles.itemLabel}>{q.prompt}</span>
          <span className={styles.itemMeta}>{t("CourseCmsPage.pointsUnit", { points: q.points })}</span>
          <button type="button" className={styles.iconBtn} onClick={() => handleDelete(q)}>
            <MIcon name="delete" size={14} />
          </button>
        </div>
      ))}
      <form className={styles.questionForm} onSubmit={handleCreate}>
        <input
          ref={promptInputRef}
          className={`${styles.input} ${invalid.prompt ? styles.fieldInvalid : ""}`}
          value={form.prompt}
          onChange={(e) => { setForm((f) => ({ ...f, prompt: e.target.value })); setInvalid((v) => ({ ...v, prompt: false })); }}
          placeholder={t("CourseCmsPage.questionPromptPlaceholder")}
        />
        <select
          className={styles.select}
          value={form.question_type}
          onChange={(e) => setForm((f) => ({ ...f, question_type: e.target.value }))}
        >
          <option value="flag">{t("CourseCmsPage.flagQuestionOption")}</option>
          <option value="no_answer">{t("CourseCmsPage.readingQuestionOption")}</option>
        </select>
        {form.question_type === "flag" && (
          <input
            ref={flagInputRef}
            className={`${styles.input} ${invalid.flag ? styles.fieldInvalid : ""}`}
            value={form.flag}
            onChange={(e) => { setForm((f) => ({ ...f, flag: e.target.value })); setInvalid((v) => ({ ...v, flag: false })); }}
            placeholder={t("CourseCmsPage.flagAnswerPlaceholder")}
          />
        )}
        <input
          className={`${styles.input} ${styles.inputNarrow}`}
          type="number"
          min="0"
          value={form.points}
          onChange={(e) => setForm((f) => ({ ...f, points: e.target.value }))}
          title={t("CourseCmsPage.pointsFieldTitle")}
        />
        <button type="submit" className={styles.addBtn}>
          <MIcon name="add" size={16} />
        </button>
      </form>
    </div>
  );
}

/* ══════════════ 任務欄（含內容編輯與題目） ══════════════ */
function TaskColumn({ roomId }) {
  const { t } = useTranslation("teaching");
  const toast = useToast();
  const confirm = useConfirm();
  const [tasks, setTasks] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState({ title: "", content: "" });
  const [newTitle, setNewTitle] = useState("");
  const [newTitleInvalid, setNewTitleInvalid] = useState(false);
  const newTitleInputRef = useRef(null);

  const reload = useCallback(() => {
    CourseAdminService.listTasks(roomId)
      .then((rows) => {
        setTasks(rows);
        setSelectedId((cur) => (rows.some((t) => t.id === cur) ? cur : rows[0]?.id ?? null));
      })
      .catch(() => {});
  }, [roomId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const selected = tasks.find((t) => t.id === selectedId) ?? null;

  useEffect(() => {
    if (selected) setDraft({ title: selected.title, content: selected.content });
  }, [selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleCreate(e) {
    e.preventDefault();
    if (!newTitle.trim()) {
      setNewTitleInvalid(true);
      focusInvalidField(newTitleInputRef.current);
      return;
    }
    try {
      await CourseAdminService.createTask({
        room_id: roomId,
        title: newTitle.trim(),
        content: "",
        order: tasks.length,
      });
      setNewTitle("");
      reload();
      toast.success(t("CourseCmsPage.taskAddedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.addFailedToast"));
    }
  }

  async function handleSave() {
    if (!selected) return;
    try {
      await CourseAdminService.updateTask(selected.id, {
        title: draft.title,
        content: draft.content,
      });
      reload();
      toast.success(t("CourseCmsPage.taskSavedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.saveFailedToast"));
    }
  }

  async function handleDelete(task, e) {
    e.stopPropagation();
    const ok = await confirm({
      title: t("CourseCmsPage.deleteTaskConfirmTitle"),
      message: t("CourseCmsPage.deleteTaskConfirmMessage", { title: task.title }),
      confirmText: t("CourseCmsPage.deleteLabel"),
      danger: true,
    });
    if (!ok) return;
    try {
      await CourseAdminService.deleteTask(task.id);
      reload();
      toast.success(t("CourseCmsPage.deletedToast"));
    } catch (err) {
      toast.error(err.message ?? t("CourseCmsPage.deleteFailedToast"));
    }
  }

  return (
    <div className={`${styles.column} ${styles.columnWide}`}>
      <div className={styles.columnHeader}>{t("CourseCmsPage.taskColumnHeader")}</div>
      <div className={styles.taskLayout}>
        <div className={styles.taskList}>
          {tasks.map((task, i) => (
            <div
              key={task.id}
              className={`${styles.item} ${selectedId === task.id ? styles.itemActive : ""}`}
              onClick={() => setSelectedId(task.id)}
            >
              <span className={styles.itemLabel}>{i + 1}. {task.title}</span>
              <button
                type="button"
                className={styles.iconBtn}
                onClick={(e) => handleDelete(task, e)}
              >
                <MIcon name="delete" size={14} />
              </button>
            </div>
          ))}
          {tasks.length === 0 && <EmptyState icon="playlist_add_check" iconSize={24} title={t("CourseCmsPage.noTasksTitle")} />}
          <form className={styles.addForm} onSubmit={handleCreate}>
            <input
              ref={newTitleInputRef}
              className={`${styles.input} ${newTitleInvalid ? styles.fieldInvalid : ""}`}
              value={newTitle}
              onChange={(e) => { setNewTitle(e.target.value); setNewTitleInvalid(false); }}
              placeholder={t("CourseCmsPage.newTaskPlaceholder")}
            />
            <button type="submit" className={styles.addBtn}>
              <MIcon name="add" size={16} />
            </button>
          </form>
        </div>

        {selected && (
          <div className={styles.taskEditor}>
            <input
              className={styles.input}
              value={draft.title}
              onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
            />
            <textarea
              className={styles.textarea}
              value={draft.content}
              onChange={(e) => setDraft((d) => ({ ...d, content: e.target.value }))}
              placeholder={t("CourseCmsPage.taskContentPlaceholder")}
              rows={10}
            />
            <div className={styles.editorActions}>
              <button type="button" className={styles.saveBtn} onClick={handleSave}>
                <MIcon name="save" size={15} />
                {t("CourseCmsPage.saveTaskBtn")}
              </button>
            </div>
            <QuestionEditor taskId={selected.id} />
          </div>
        )}
      </div>
    </div>
  );
}

/* ══════════════ 進度監控 ══════════════ */
function ProgressPanel({ paths, initialPathId = "" }) {
  const { t } = useTranslation("teaching");
  const [pathId, setPathId] = useState(initialPathId);
  const [report, setReport] = useState(null);
  const [live, setLive] = useState(false);
  const wsRef = useRef(null);
  const refetchTimer = useRef(null);

  useEffect(() => {
    if (initialPathId) setPathId(initialPathId);
  }, [initialPathId]);

  const fetchReport = useCallback((id) => {
    CourseAdminService.getPathProgress(id).then(setReport).catch(() => {});
  }, []);

  useEffect(() => {
    if (!pathId) {
      setReport(null);
      return undefined;
    }
    fetchReport(pathId);

    // WS 即時推播：收到事件後 debounce 重拉快照
    const token = AuthStorage.getAccessToken() ?? "";
    const ws = new WebSocket(courseProgressWsUrl(pathId, token));
    wsRef.current = ws;
    ws.onopen = () => setLive(true);
    ws.onmessage = () => {
      clearTimeout(refetchTimer.current);
      refetchTimer.current = setTimeout(() => fetchReport(pathId), 800);
    };
    ws.onclose = () => setLive(false);
    ws.onerror = () => setLive(false);

    return () => {
      clearTimeout(refetchTimer.current);
      ws.close();
      wsRef.current = null;
    };
  }, [pathId, fetchReport]);

  return (
    <div className={styles.progressPanel}>
      <div className={styles.progressToolbar}>
        <select
          className={styles.select}
          value={pathId}
          onChange={(e) => setPathId(e.target.value)}
        >
          <option value="">{t("CourseCmsPage.selectPathOption")}</option>
          {paths.map((p) => (
            <option key={p.id} value={p.id}>{p.title}</option>
          ))}
        </select>
        {pathId && (
          <span className={`${styles.liveBadge} ${live ? styles.liveOn : ""}`}>
            <span className={styles.liveDot} />
            {live ? t("CourseCmsPage.liveUpdating") : t("CourseCmsPage.connectionLost")}
          </span>
        )}
      </div>

      {report && (
        <div className={styles.progressTableWrap}>
          <table className={styles.progressTable}>
            <thead>
              <tr>
                <th>{t("CourseCmsPage.thStudent")}</th>
                <th>{t("CourseCmsPage.thTotalProgress")}</th>
                {report.students[0]?.rooms.map((r) => (
                  <th key={r.room_id}>{r.room_title}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {report.students.map((s) => (
                <tr key={s.user_id}>
                  <td>
                    <div className={styles.studentCell}>
                      <span>{s.user_name ?? s.user_email}</span>
                      <span className={styles.studentEmail}>{s.user_email}</span>
                    </div>
                  </td>
                  <td>
                    <div className={styles.cellProgress}>
                      <div className={styles.progressBarSm}>
                        <div
                          className={styles.progressFillSm}
                          style={{ width: `${s.progress_percent}%` }}
                        />
                      </div>
                      {s.progress_percent}%
                    </div>
                  </td>
                  {s.rooms.map((r) => (
                    <td key={r.room_id}>
                      {r.completed_questions}/{r.total_questions}
                    </td>
                  ))}
                </tr>
              ))}
              {report.students.length === 0 && (
                <tr>
                  <td colSpan={99} className={styles.emptyHint}>
                    {t("CourseCmsPage.noStudentRecordsText")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ══════════════ 主頁 ══════════════ */
export default function CourseCmsPage() {
  const { t } = useTranslation("teaching");
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState(searchParams.get("tab") === "progress" ? "progress" : "editor");
  const [pathsLoading, setPathsLoading] = useState(true);
  const [paths, setPaths] = useState([]);
  const [rooms, setRooms] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [teachingClasses, setTeachingClasses] = useState([]);
  const [selectedPathId, setSelectedPathId] = useState(searchParams.get("pathId"));
  const [selectedRoomId, setSelectedRoomId] = useState(null);

  const canManage =
    user?.role === "admin" || user?.role === "teacher" || user?.is_superuser === true;

  const reloadPaths = useCallback(() => {
    CourseAdminService.listPaths()
      .then(setPaths)
      .catch(() => {})
      .finally(() => setPathsLoading(false));
  }, []);

  function changeTab(nextTab) {
    setTab(nextTab);
    const next = new URLSearchParams(searchParams);
    next.set("tab", nextTab);
    if (nextTab !== "progress") next.delete("pathId");
    setSearchParams(next, { replace: true });
  }

  const reloadRooms = useCallback(() => {
    if (!selectedPathId) {
      setRooms([]);
      return;
    }
    CourseAdminService.listRooms(selectedPathId)
      .then((rows) => {
        setRooms(rows);
        setSelectedRoomId((cur) =>
          rows.some((r) => r.id === cur) ? cur : null
        );
      })
      .catch(() => {});
  }, [selectedPathId]);

  useEffect(() => {
    if (!canManage) return;
    reloadPaths();
    TemplatesService.list()
      .then((rows) => setTemplates(rows.filter((t) => t.status === "ready")))
      .catch(() => {});
    TeachingClassesService.list().then(setTeachingClasses).catch(() => {});
  }, [canManage, reloadPaths]);

  useEffect(() => {
    reloadRooms();
  }, [reloadRooms]);

  if (!canManage) {
    return <div className={styles.stateText}>{t("CourseCmsPage.teacherOnlyText")}</div>;
  }

  return (
    <div className={styles.page}>
      <PageHeader title={t("CourseCmsPage.pageTitle")} subtitle={t("CourseCmsPage.pageSubtitle")}>
        <div className={styles.tabs}>
          <button
            type="button"
            className={`${styles.tabBtn} ${tab === "editor" ? styles.tabActive : ""}`}
            onClick={() => changeTab("editor")}
          >
            <MIcon name="edit_note" size={16} />
            {t("CourseCmsPage.tabContentEditor")}
          </button>
          <button
            type="button"
            className={`${styles.tabBtn} ${tab === "progress" ? styles.tabActive : ""}`}
            onClick={() => changeTab("progress")}
          >
            <MIcon name="insights" size={16} />
            {t("CourseCmsPage.tabStudentProgress")}
          </button>
        </div>
      </PageHeader>

      {pathsLoading ? (
        <LoadingState fullPage />
      ) : tab === "editor" ? (
        <div className={styles.editorLayout}>
          <PathColumn
            paths={paths}
            teachingClasses={teachingClasses}
            selectedId={selectedPathId}
            onSelect={(id) => {
              setSelectedPathId(id);
              setSelectedRoomId(null);
            }}
            onReload={reloadPaths}
          />
          {selectedPathId && (
            <RoomColumn
              pathId={selectedPathId}
              rooms={rooms}
              templates={templates}
              selectedId={selectedRoomId}
              onSelect={setSelectedRoomId}
              onReload={() => {
                reloadRooms();
                reloadPaths();
              }}
            />
          )}
          {selectedRoomId && <TaskColumn roomId={selectedRoomId} />}
        </div>
      ) : (
        <ProgressPanel paths={paths} initialPathId={searchParams.get("pathId") ?? ""} />
      )}
    </div>
  );
}
