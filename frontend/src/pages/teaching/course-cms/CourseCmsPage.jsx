import { useCallback, useEffect, useRef, useState } from "react";
import MIcon from "../../../components/MIcon";
import { useAuth } from "../../../contexts/AuthContext";
import { useToast } from "../../../hooks/useToast";
import { AuthStorage } from "../../../services/auth";
import {
  CourseAdminService,
  courseProgressWsUrl,
} from "../../../services/courses";
import { TemplatesService } from "../../../services/templates";
import styles from "./CourseCmsPage.module.scss";

const DIFFICULTIES = [
  { key: "easy", label: "簡單" },
  { key: "medium", label: "中等" },
  { key: "hard", label: "困難" },
];

/* ══════════════ 路徑欄 ══════════════ */
function PathColumn({ paths, selectedId, onSelect, onReload }) {
  const toast = useToast();
  const [title, setTitle] = useState("");

  async function handleCreate(e) {
    e.preventDefault();
    if (!title.trim()) return;
    try {
      await CourseAdminService.createPath({ title: title.trim() });
      setTitle("");
      onReload();
      toast.success("已建立路徑");
    } catch (err) {
      toast.error(err.message ?? "建立失敗");
    }
  }

  async function handlePublish(path, e) {
    e.stopPropagation();
    try {
      await CourseAdminService.publishPath(path.id, path.status !== "published");
      onReload();
      toast.success(path.status === "published" ? "已下架" : "已發布");
    } catch (err) {
      toast.error(err.message ?? "操作失敗");
    }
  }

  async function handleDelete(path, e) {
    e.stopPropagation();
    if (!window.confirm(`確定刪除路徑「${path.title}」？（房間與任務會一併刪除）`)) return;
    try {
      await CourseAdminService.deletePath(path.id);
      onReload();
      toast.success("已刪除");
    } catch (err) {
      toast.error(err.message ?? "刪除失敗");
    }
  }

  return (
    <div className={styles.column}>
      <div className={styles.columnHeader}>學習路徑</div>
      <div className={styles.columnBody}>
        {paths.map((path) => (
          <div
            key={path.id}
            className={`${styles.item} ${selectedId === path.id ? styles.itemActive : ""}`}
            onClick={() => onSelect(path.id)}
          >
            <span
              className={`${styles.pubDot} ${path.status === "published" ? styles.pub_on : ""}`}
              title={path.status === "published" ? "已發布" : "草稿"}
            />
            <span className={styles.itemLabel}>{path.title}</span>
            <span className={styles.itemMeta}>{path.room_count} 房</span>
            <button
              type="button"
              className={styles.iconBtn}
              title={path.status === "published" ? "下架" : "發布"}
              onClick={(e) => handlePublish(path, e)}
            >
              <MIcon name={path.status === "published" ? "visibility_off" : "publish"} size={15} />
            </button>
            <button
              type="button"
              className={styles.iconBtn}
              title="刪除"
              onClick={(e) => handleDelete(path, e)}
            >
              <MIcon name="delete" size={15} />
            </button>
          </div>
        ))}
        {paths.length === 0 && <div className={styles.emptyHint}>尚無路徑</div>}
      </div>
      <form className={styles.addForm} onSubmit={handleCreate}>
        <input
          className={styles.input}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="新路徑標題"
        />
        <button type="submit" className={styles.addBtn} disabled={!title.trim()}>
          <MIcon name="add" size={16} />
        </button>
      </form>
    </div>
  );
}

/* ══════════════ 房間欄 ══════════════ */
function RoomColumn({ pathId, rooms, templates, selectedId, onSelect, onReload }) {
  const toast = useToast();
  const [form, setForm] = useState({ title: "", difficulty: "easy", template_id: "" });

  async function handleCreate(e) {
    e.preventDefault();
    if (!form.title.trim()) return;
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
      toast.success("已建立房間");
    } catch (err) {
      toast.error(err.message ?? "建立失敗");
    }
  }

  async function handleDelete(room, e) {
    e.stopPropagation();
    if (!window.confirm(`確定刪除房間「${room.title}」？`)) return;
    try {
      await CourseAdminService.deleteRoom(room.id);
      onReload();
      toast.success("已刪除");
    } catch (err) {
      toast.error(err.message ?? "刪除失敗");
    }
  }

  return (
    <div className={styles.column}>
      <div className={styles.columnHeader}>房間</div>
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
              {room.template_name ?? "純理論"} · {room.task_count} 任務
            </span>
            <button
              type="button"
              className={styles.iconBtn}
              title="刪除"
              onClick={(e) => handleDelete(room, e)}
            >
              <MIcon name="delete" size={15} />
            </button>
          </div>
        ))}
        {rooms.length === 0 && <div className={styles.emptyHint}>尚無房間</div>}
      </div>
      <form className={styles.addForm} onSubmit={handleCreate}>
        <input
          className={styles.input}
          value={form.title}
          onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
          placeholder="新房間標題"
        />
        <select
          className={styles.select}
          value={form.difficulty}
          onChange={(e) => setForm((f) => ({ ...f, difficulty: e.target.value }))}
        >
          {DIFFICULTIES.map((d) => (
            <option key={d.key} value={d.key}>{d.label}</option>
          ))}
        </select>
        <select
          className={styles.select}
          value={form.template_id}
          onChange={(e) => setForm((f) => ({ ...f, template_id: e.target.value }))}
        >
          <option value="">不綁模板（純理論）</option>
          {templates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}（{t.resource_type === "lxc" ? "LXC" : "VM"}）
            </option>
          ))}
        </select>
        <button type="submit" className={styles.addBtn} disabled={!form.title.trim()}>
          <MIcon name="add" size={16} />
        </button>
      </form>
    </div>
  );
}

/* ══════════════ 題目編輯 ══════════════ */
function QuestionEditor({ taskId }) {
  const toast = useToast();
  const [questions, setQuestions] = useState([]);
  const [form, setForm] = useState({ prompt: "", question_type: "flag", flag: "", points: 10 });

  const reload = useCallback(() => {
    CourseAdminService.listQuestions(taskId).then(setQuestions).catch(() => {});
  }, [taskId]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleCreate(e) {
    e.preventDefault();
    if (!form.prompt.trim()) return;
    if (form.question_type === "flag" && !form.flag.trim()) {
      toast.error("Flag 題必須填答案");
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
      toast.success("已新增題目");
    } catch (err) {
      toast.error(err.message ?? "新增失敗");
    }
  }

  async function handleDelete(q) {
    if (!window.confirm("確定刪除此題？學生完成記錄會一併刪除")) return;
    try {
      await CourseAdminService.deleteQuestion(q.id);
      reload();
      toast.success("已刪除");
    } catch (err) {
      toast.error(err.message ?? "刪除失敗");
    }
  }

  return (
    <div className={styles.questionBlock}>
      <div className={styles.questionHeader}>題目（{questions.length}）</div>
      {questions.map((q) => (
        <div key={q.id} className={styles.questionRow}>
          <MIcon name={q.question_type === "flag" ? "flag" : "menu_book"} size={14} />
          <span className={styles.itemLabel}>{q.prompt}</span>
          <span className={styles.itemMeta}>{q.points} 分</span>
          <button type="button" className={styles.iconBtn} onClick={() => handleDelete(q)}>
            <MIcon name="delete" size={14} />
          </button>
        </div>
      ))}
      <form className={styles.questionForm} onSubmit={handleCreate}>
        <input
          className={styles.input}
          value={form.prompt}
          onChange={(e) => setForm((f) => ({ ...f, prompt: e.target.value }))}
          placeholder="題目描述"
        />
        <select
          className={styles.select}
          value={form.question_type}
          onChange={(e) => setForm((f) => ({ ...f, question_type: e.target.value }))}
        >
          <option value="flag">Flag 題</option>
          <option value="no_answer">閱讀題（免作答）</option>
        </select>
        {form.question_type === "flag" && (
          <input
            className={styles.input}
            value={form.flag}
            onChange={(e) => setForm((f) => ({ ...f, flag: e.target.value }))}
            placeholder="FLAG{答案}"
          />
        )}
        <input
          className={`${styles.input} ${styles.inputNarrow}`}
          type="number"
          min="0"
          value={form.points}
          onChange={(e) => setForm((f) => ({ ...f, points: e.target.value }))}
          title="分數"
        />
        <button type="submit" className={styles.addBtn} disabled={!form.prompt.trim()}>
          <MIcon name="add" size={16} />
        </button>
      </form>
    </div>
  );
}

/* ══════════════ 任務欄（含內容編輯與題目） ══════════════ */
function TaskColumn({ roomId }) {
  const toast = useToast();
  const [tasks, setTasks] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState({ title: "", content: "" });
  const [newTitle, setNewTitle] = useState("");

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
    if (!newTitle.trim()) return;
    try {
      await CourseAdminService.createTask({
        room_id: roomId,
        title: newTitle.trim(),
        content: "",
        order: tasks.length,
      });
      setNewTitle("");
      reload();
      toast.success("已新增任務");
    } catch (err) {
      toast.error(err.message ?? "新增失敗");
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
      toast.success("已儲存任務");
    } catch (err) {
      toast.error(err.message ?? "儲存失敗");
    }
  }

  async function handleDelete(task, e) {
    e.stopPropagation();
    if (!window.confirm(`確定刪除任務「${task.title}」？`)) return;
    try {
      await CourseAdminService.deleteTask(task.id);
      reload();
      toast.success("已刪除");
    } catch (err) {
      toast.error(err.message ?? "刪除失敗");
    }
  }

  return (
    <div className={`${styles.column} ${styles.columnWide}`}>
      <div className={styles.columnHeader}>任務與題目</div>
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
          {tasks.length === 0 && <div className={styles.emptyHint}>尚無任務</div>}
          <form className={styles.addForm} onSubmit={handleCreate}>
            <input
              className={styles.input}
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              placeholder="新任務標題"
            />
            <button type="submit" className={styles.addBtn} disabled={!newTitle.trim()}>
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
              placeholder="教學內容（Markdown）"
              rows={10}
            />
            <div className={styles.editorActions}>
              <button type="button" className={styles.saveBtn} onClick={handleSave}>
                <MIcon name="save" size={15} />
                儲存任務
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
function ProgressPanel({ paths }) {
  const [pathId, setPathId] = useState("");
  const [report, setReport] = useState(null);
  const [live, setLive] = useState(false);
  const wsRef = useRef(null);
  const refetchTimer = useRef(null);

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
          <option value="">選擇學習路徑…</option>
          {paths.map((p) => (
            <option key={p.id} value={p.id}>{p.title}</option>
          ))}
        </select>
        {pathId && (
          <span className={`${styles.liveBadge} ${live ? styles.liveOn : ""}`}>
            <span className={styles.liveDot} />
            {live ? "即時更新中" : "連線中斷"}
          </span>
        )}
      </div>

      {report && (
        <div className={styles.progressTableWrap}>
          <table className={styles.progressTable}>
            <thead>
              <tr>
                <th>學生</th>
                <th>總進度</th>
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
                    尚無學生答題記錄
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
  const { user } = useAuth();
  const [tab, setTab] = useState("editor");
  const [paths, setPaths] = useState([]);
  const [rooms, setRooms] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [selectedPathId, setSelectedPathId] = useState(null);
  const [selectedRoomId, setSelectedRoomId] = useState(null);

  const canManage =
    user?.role === "admin" || user?.role === "teacher" || user?.is_superuser === true;

  const reloadPaths = useCallback(() => {
    CourseAdminService.listPaths().then(setPaths).catch(() => {});
  }, []);

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
  }, [canManage, reloadPaths]);

  useEffect(() => {
    reloadRooms();
  }, [reloadRooms]);

  if (!canManage) {
    return <div className={styles.stateText}>僅老師與管理員可使用課程管理</div>;
  }

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>課程管理</h1>
          <p className={styles.pageSubtitle}>
            建立學習路徑 → 房間（綁定實驗模板）→ 任務與 Flag 題目，發布後學生即可學習
          </p>
        </div>
        <div className={styles.tabs}>
          <button
            type="button"
            className={`${styles.tabBtn} ${tab === "editor" ? styles.tabActive : ""}`}
            onClick={() => setTab("editor")}
          >
            <MIcon name="edit_note" size={16} />
            內容編輯
          </button>
          <button
            type="button"
            className={`${styles.tabBtn} ${tab === "progress" ? styles.tabActive : ""}`}
            onClick={() => setTab("progress")}
          >
            <MIcon name="insights" size={16} />
            學生進度
          </button>
        </div>
      </div>

      {tab === "editor" ? (
        <div className={styles.editorLayout}>
          <PathColumn
            paths={paths}
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
        <ProgressPanel paths={paths} />
      )}
    </div>
  );
}
