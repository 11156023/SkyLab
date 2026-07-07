import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import { VncScreen } from "react-vnc";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import { AuthStorage } from "../../../services/auth";
import { CoursesService } from "../../../services/courses";
import { ResourcesService } from "../../../services/resources";
import styles from "./CourseRoomPage.module.scss";

const POLL_INTERVAL_MS = 3000;

const DIFFICULTY_LABEL = { easy: "簡單", medium: "中等", hard: "困難" };

/* ── 剩餘時間倒數 ── */
function Countdown({ expiresAt }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);

  const remainMs = new Date(expiresAt).getTime() - now;
  if (remainMs <= 0) return <span>已到期</span>;
  const hours = Math.floor(remainMs / 3600000);
  const mins = Math.floor((remainMs % 3600000) / 60000);
  return <span>剩餘 {hours > 0 ? `${hours} 小時 ` : ""}{mins} 分鐘</span>;
}

/* ── 內嵌 VNC 面板 ── */
function VncPanel({ vmid }) {
  const vncRef = useRef(null);
  const [wsUrl, setWsUrl] = useState("");
  const [vncTicket, setVncTicket] = useState("");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!vmid) return;
    let cancelled = false;
    setWsUrl("");
    setError("");

    ResourcesService.getConsole(vmid)
      .then((data) => {
        if (cancelled) return;
        const ticket = data.ticket ?? "";
        if (!ticket) {
          setError("無法取得 VNC ticket");
          return;
        }
        const apiUrl = new URL(
          import.meta.env.VITE_API_URL ||
            `${window.location.protocol}//${window.location.host}`
        );
        const proto = apiUrl.protocol === "https:" ? "wss:" : "ws:";
        const token = AuthStorage.getAccessToken() ?? "";
        let url = `${proto}//${apiUrl.host}/ws/vnc/${vmid}?token=${encodeURIComponent(token)}&vnc_ticket=${encodeURIComponent(ticket)}`;
        if (data.port) url += `&vnc_port=${encodeURIComponent(data.port)}`;
        setVncTicket(ticket);
        setWsUrl(url);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message ?? "無法取得控制台資訊");
      });

    return () => {
      cancelled = true;
    };
  }, [vmid]);

  if (error) {
    return (
      <div className={styles.vncState}>
        <MIcon name="error_outline" size={20} />
        {error}
      </div>
    );
  }
  if (!wsUrl) {
    return (
      <div className={styles.vncState}>
        <MIcon name="hourglass_empty" size={20} />
        連線控制台中…
      </div>
    );
  }
  return (
    <div className={styles.vncWrap}>
      <div className={styles.vncToolbar}>
        <span
          className={`${styles.statusDot} ${connected ? styles.dot_ok : styles.dot_wait}`}
        />
        <span>{connected ? "已連接" : "連接中"}</span>
        <button
          type="button"
          className={styles.vncBtn}
          title="Ctrl+Alt+Del"
          onClick={() => vncRef.current?.sendCtrlAltDel?.()}
        >
          <MIcon name="keyboard" size={14} />
        </button>
      </div>
      <VncScreen
        ref={vncRef}
        url={wsUrl}
        rfbOptions={{
          credentials: { username: "", password: vncTicket, target: "" },
        }}
        style={{ width: "100%", height: "100%" }}
        onConnect={() => setConnected(true)}
        onDisconnect={() => setConnected(false)}
        scaleViewport
        background="#1e1e1e"
      />
    </div>
  );
}

/* ── 單一題目列 ── */
function QuestionRow({ question, onSubmit, submitting }) {
  const [answer, setAnswer] = useState("");
  const isFlag = question.question_type === "flag";

  if (question.completed) {
    return (
      <div className={`${styles.question} ${styles.questionDone}`}>
        <MIcon name="check_circle" size={18} />
        <span className={styles.questionPrompt}>{question.prompt}</span>
        <span className={styles.points}>+{question.points}</span>
      </div>
    );
  }

  return (
    <div className={styles.question}>
      <MIcon name={isFlag ? "flag" : "menu_book"} size={18} />
      <span className={styles.questionPrompt}>{question.prompt}</span>
      {isFlag ? (
        <form
          className={styles.answerForm}
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit(question.id, answer);
          }}
        >
          <input
            className={styles.answerInput}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="FLAG{...}"
            disabled={submitting}
          />
          <button
            type="submit"
            className={styles.submitBtn}
            disabled={submitting || !answer.trim()}
          >
            提交
          </button>
        </form>
      ) : (
        <button
          type="button"
          className={styles.submitBtn}
          disabled={submitting}
          onClick={() => onSubmit(question.id, null)}
        >
          標記完成
        </button>
      )}
    </div>
  );
}

export default function CourseRoomPage() {
  const { roomId } = useParams();
  const navigate = useNavigate();
  const toast = useToast();

  const [room, setRoom] = useState(null);
  const [error, setError] = useState("");
  const [activeTaskId, setActiveTaskId] = useState(null);
  const [deployment, setDeployment] = useState(null);
  const [deploying, setDeploying] = useState(false);
  const [submittingId, setSubmittingId] = useState(null);

  const loadRoom = useCallback(() => {
    return CoursesService.getRoom(roomId)
      .then((data) => {
        setRoom(data);
        setDeployment(data.my_deployment);
        setActiveTaskId((cur) => cur ?? data.tasks[0]?.id ?? null);
      })
      .catch((e) => setError(e.message ?? "載入房間失敗"));
  }, [roomId]);

  useEffect(() => {
    loadRoom();
  }, [loadRoom]);

  /* provisioning 輪詢 */
  useEffect(() => {
    if (deployment?.status !== "provisioning") return;
    const id = setInterval(() => {
      CoursesService.getDeployment(deployment.id)
        .then((d) => {
          setDeployment(d);
          if (d.status === "failed") toast.error(d.error ?? "實驗機部署失敗");
          if (d.status === "running") toast.success("實驗機已就緒");
        })
        .catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [deployment?.status, deployment?.id, toast]);

  async function handleDeploy() {
    setDeploying(true);
    try {
      const d = await CoursesService.deployRoom(roomId);
      setDeployment(d);
      toast.info("部署已啟動，克隆實驗機中…");
    } catch (e) {
      toast.error(e.message ?? "部署失敗");
    } finally {
      setDeploying(false);
    }
  }

  async function handleTerminate() {
    if (!deployment) return;
    try {
      const d = await CoursesService.terminateDeployment(deployment.id);
      setDeployment(d);
      toast.info("實驗機已歸還，資源回收中");
    } catch (e) {
      toast.error(e.message ?? "歸還失敗");
    }
  }

  const handleSubmit = useCallback(
    async (questionId, answer) => {
      setSubmittingId(questionId);
      try {
        const result = await CoursesService.submitAnswer(questionId, answer);
        if (!result.correct) {
          toast.error("答案不正確，再試試！");
          return;
        }
        toast.success(
          result.task_completed ? "任務完成！已解鎖下一任務 🎉" : "答對了！"
        );
        setRoom((cur) => {
          if (!cur) return cur;
          const tasks = cur.tasks.map((t) => ({
            ...t,
            questions: t.questions.map((q) =>
              q.id === questionId ? { ...q, completed: true } : q
            ),
          }));
          return { ...cur, tasks };
        });
        if (result.task_completed) {
          setRoom((cur) => {
            if (!cur) return cur;
            const idx = cur.tasks.findIndex((t) =>
              t.questions.some((q) => q.id === questionId)
            );
            const next = cur.tasks[idx + 1];
            if (next) setActiveTaskId(next.id);
            return cur;
          });
        }
      } catch (e) {
        toast.error(e.message ?? "提交失敗");
      } finally {
        setSubmittingId(null);
      }
    },
    [toast]
  );

  const activeTask = useMemo(
    () => room?.tasks.find((t) => t.id === activeTaskId) ?? null,
    [room, activeTaskId]
  );

  if (error) return <div className={styles.stateText}>{error}</div>;
  if (!room) return <div className={styles.stateText}>載入中…</div>;

  const showLab = room.has_lab;
  const running = deployment?.status === "running" && deployment?.vmid;

  return (
    <div className={styles.page}>
      {/* ── 頂部列 ── */}
      <div className={styles.topBar}>
        <button
          type="button"
          className={styles.backBtn}
          onClick={() => navigate("/courses")}
        >
          <MIcon name="arrow_back" size={18} />
        </button>
        <div className={styles.topHeading}>
          <span className={styles.roomTitle}>{room.title}</span>
          <span className={styles.roomMeta}>
            {DIFFICULTY_LABEL[room.difficulty] ?? room.difficulty}
            {room.category ? ` · ${room.category}` : ""}
          </span>
        </div>

        {showLab && (
          <div className={styles.labControls}>
            {deployment && deployment.status !== "expired" && deployment.status !== "failed" ? (
              <>
                <span className={styles.labStatus}>
                  {deployment.status === "provisioning" && (
                    <>
                      <MIcon name="hourglass_top" size={16} />
                      部署中…
                    </>
                  )}
                  {deployment.status === "running" && (
                    <>
                      <MIcon name="check_circle" size={16} />
                      <Countdown expiresAt={deployment.expires_at} />
                    </>
                  )}
                </span>
                <button
                  type="button"
                  className={styles.dangerBtn}
                  onClick={handleTerminate}
                >
                  <MIcon name="power_settings_new" size={16} />
                  歸還
                </button>
              </>
            ) : (
              <button
                type="button"
                className={styles.primaryBtn}
                onClick={handleDeploy}
                disabled={deploying}
              >
                <MIcon name="rocket_launch" size={16} />
                {deploying ? "啟動中…" : "啟動實驗機"}
              </button>
            )}
          </div>
        )}
      </div>

      {deployment?.status === "failed" && (
        <div className={styles.failedBanner}>
          <MIcon name="error_outline" size={16} />
          部署失敗：{deployment.error ?? "未知錯誤"}，可重新啟動實驗機。
        </div>
      )}

      {/* ── 三欄工作區 ── */}
      <div className={`${styles.workspace} ${showLab ? "" : styles.noLab}`}>
        {/* 左：任務導航 */}
        <aside className={styles.taskNav}>
          {room.tasks.map((task, i) => {
            const done =
              task.questions.length > 0 &&
              task.questions.every((q) => q.completed);
            return (
              <button
                key={task.id}
                type="button"
                className={`${styles.taskNavItem} ${activeTaskId === task.id ? styles.taskNavActive : ""}`}
                onClick={() => setActiveTaskId(task.id)}
              >
                <MIcon
                  name={done ? "check_circle" : "radio_button_unchecked"}
                  size={16}
                />
                <span className={styles.taskNavLabel}>
                  {i + 1}. {task.title}
                </span>
              </button>
            );
          })}
          {room.tasks.length === 0 && (
            <div className={styles.stateText}>尚無任務</div>
          )}
        </aside>

        {/* 中：教學內容 + 題目 */}
        <section className={styles.content}>
          {activeTask ? (
            <>
              <h2 className={styles.taskTitle}>{activeTask.title}</h2>
              <div className={styles.markdown}>
                <ReactMarkdown rehypePlugins={[rehypeSanitize]}>
                  {activeTask.content}
                </ReactMarkdown>
              </div>
              <div className={styles.questions}>
                {activeTask.questions.map((q) => (
                  <QuestionRow
                    key={q.id}
                    question={q}
                    onSubmit={handleSubmit}
                    submitting={submittingId === q.id}
                  />
                ))}
              </div>
            </>
          ) : (
            <div className={styles.stateText}>選擇左側任務開始</div>
          )}
        </section>

        {/* 右：VNC */}
        {showLab && (
          <section className={styles.vncPane}>
            {running ? (
              <VncPanel vmid={deployment.vmid} />
            ) : (
              <div className={styles.vncState}>
                <MIcon name="desktop_windows" size={28} />
                {deployment?.status === "provisioning"
                  ? "實驗機克隆中，就緒後自動連線…"
                  : "啟動實驗機後，控制台會顯示在這裡"}
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
