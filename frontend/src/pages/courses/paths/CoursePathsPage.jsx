import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import MIcon from "../../../components/MIcon";
import { useToast } from "../../../hooks/useToast";
import { CoursesService } from "../../../services/courses";
import styles from "./CoursePathsPage.module.scss";

const DIFFICULTY_META = {
  easy:   { label: "簡單", cls: "diff_easy" },
  medium: { label: "中等", cls: "diff_medium" },
  hard:   { label: "困難", cls: "diff_hard" },
};

function ProgressBar({ percent }) {
  return (
    <div className={styles.progressBar}>
      <div
        className={styles.progressFill}
        style={{ width: `${Math.min(100, percent)}%` }}
      />
    </div>
  );
}

function PathCard({ path, expanded, onToggle }) {
  const navigate = useNavigate();
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const toast = useToast();

  useEffect(() => {
    if (!expanded || detail) return;
    setLoading(true);
    CoursesService.getPath(path.id)
      .then(setDetail)
      .catch((e) => toast.error(e.message ?? "載入房間失敗"))
      .finally(() => setLoading(false));
  }, [expanded, detail, path.id, toast]);

  return (
    <div className={styles.card}>
      <button type="button" className={styles.cardHeader} onClick={onToggle}>
        <span className={styles.cardIcon}>
          <MIcon name="flag" size={22} />
        </span>
        <span className={styles.cardHeading}>
          <span className={styles.cardTitle}>{path.title}</span>
          {path.description && (
            <span className={styles.cardDesc}>{path.description}</span>
          )}
        </span>
        <span className={styles.cardMeta}>
          <span className={styles.metaText}>
            {path.room_count} 個房間 · {path.completed_questions}/
            {path.total_questions} 題
          </span>
          <span className={styles.percentText}>{path.progress_percent}%</span>
        </span>
        <MIcon name={expanded ? "expand_less" : "expand_more"} size={20} />
      </button>
      <ProgressBar percent={path.progress_percent} />

      {expanded && (
        <div className={styles.roomList}>
          {loading && <div className={styles.stateText}>載入中…</div>}
          {!loading &&
            detail?.rooms?.map((room) => {
              const diff = DIFFICULTY_META[room.difficulty] ?? DIFFICULTY_META.easy;
              return (
                <button
                  key={room.id}
                  type="button"
                  className={styles.roomRow}
                  onClick={() => navigate(`/courses/rooms/${room.id}`)}
                >
                  <MIcon
                    name={
                      room.progress_percent >= 100
                        ? "check_circle"
                        : room.has_lab
                          ? "computer"
                          : "menu_book"
                    }
                    size={18}
                  />
                  <span className={styles.roomTitle}>{room.title}</span>
                  {room.category && (
                    <span className={styles.roomCategory}>{room.category}</span>
                  )}
                  <span className={`${styles.diffBadge} ${styles[diff.cls]}`}>
                    {diff.label}
                  </span>
                  <span className={styles.roomProgress}>
                    {room.completed_questions}/{room.total_questions} 題 ·{" "}
                    {room.progress_percent}%
                  </span>
                </button>
              );
            })}
          {!loading && detail && detail.rooms.length === 0 && (
            <div className={styles.stateText}>此路徑尚無房間</div>
          )}
        </div>
      )}
    </div>
  );
}

export default function CoursePathsPage() {
  const [paths, setPaths] = useState(null);
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState(null);

  useEffect(() => {
    CoursesService.listPaths()
      .then(setPaths)
      .catch((e) => setError(e.message ?? "載入失敗"));
  }, []);

  return (
    <div className={styles.page}>
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>課程學習</h1>
          <p className={styles.pageSubtitle}>
            選擇學習路徑，進入房間後啟動實驗機並提交 Flag 完成任務
          </p>
        </div>
      </div>

      {error && <div className={styles.stateText}>{error}</div>}
      {!error && paths === null && <div className={styles.stateText}>載入中…</div>}
      {!error && paths?.length === 0 && (
        <div className={styles.empty}>
          <MIcon name="school" size={32} />
          <span>目前沒有已發布的學習路徑</span>
        </div>
      )}

      {paths?.map((path) => (
        <PathCard
          key={path.id}
          path={path}
          expanded={expandedId === path.id}
          onToggle={() =>
            setExpandedId((cur) => (cur === path.id ? null : path.id))
          }
        />
      ))}
    </div>
  );
}
