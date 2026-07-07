import { useEffect, useRef, useState } from "react";
import { VncScreen } from "react-vnc";
import styles from "./Classroom.module.scss";
import MIcon from "../MIcon";
import { AuthStorage } from "../../services/auth";
import { ClassroomService } from "../../services/classroom";
import { wsBaseUrl } from "../../hooks/useClassroomSocket";
import { useToast } from "../../hooks/useToast";

/**
 * 教室觀看視窗：連 /ws/classroom/{session_id}/watch 的原生 RFB 資料面
 * （下游 security=None，不需 VNC ticket）。
 * - canControl：monitor 模式發起者可「接管/釋放」
 * - pair：協作模式雙方皆可輸入，不走接管流程
 */
export default function ClassroomWatchDialog({
  sessionId,
  title,
  canControl = false,
  pair = false,
  onClose,
}) {
  const toast = useToast();
  const vncRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);

  useEffect(() => () => {
    // 卸載時保險斷線
    vncRef.current?.disconnect?.();
  }, []);

  const token = AuthStorage.getAccessToken() || "";
  const wsUrl = sessionId
    ? `${wsBaseUrl()}/ws/classroom/${sessionId}/watch?token=${encodeURIComponent(token)}`
    : "";

  // pair：雙方輸入都由後端放行；否則老師接管中才允許輸入
  const viewOnly = pair ? false : !(canControl && controlling);

  const handleControl = async () => {
    const action = controlling ? "release" : "take";
    setControlBusy(true);
    try {
      await ClassroomService.setControl(sessionId, action);
      setControlling(action === "take");
    } catch (e) {
      toast.error(e?.message ?? "控制權切換失敗");
    } finally {
      setControlBusy(false);
    }
  };

  const handleClose = () => {
    // 關閉前先釋放控制權，避免學生端持續被鎖定
    if (canControl && controlling && sessionId) {
      ClassroomService.setControl(sessionId, "release").catch(() => {});
    }
    vncRef.current?.disconnect?.();
    onClose();
  };

  return (
    <div className={styles.overlay} onClick={handleClose}>
      <div className={styles.dialog} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <span className={styles.headerIcon}>
            <MIcon name="cast" size={16} />
          </span>
          <span className={styles.headerTitleGroup}>
            <span className={styles.headerTitle}>{title || "教室觀看"}</span>
            <span
              className={`${styles.statusDot} ${connected ? styles.dot_connected : styles.dot_connecting}`}
            />
            <span className={styles.statusText}>
              {connected ? "已連線" : "連線中"}
              {!viewOnly && "・接管中"}
            </span>
          </span>

          {canControl && (
            <button
              type="button"
              className={`${styles.headerBtn} ${controlling ? styles.headerBtnActive : ""}`}
              disabled={!connected || controlBusy}
              onClick={handleControl}
            >
              <MIcon name="back_hand" size={14} />
              {controlling ? "釋放控制" : "接管"}
            </button>
          )}
          <button type="button" className={styles.closeBtn} onClick={handleClose} title="關閉">
            <MIcon name="close" size={18} />
          </button>
        </div>

        <div className={styles.vncWrap}>
          {!connected && wsUrl && (
            <div className={styles.vncLoading}>
              <MIcon name="hourglass_empty" size={28} />
              正在連接畫面…
            </div>
          )}
          {wsUrl && (
            <VncScreen
              ref={vncRef}
              url={wsUrl}
              scaleViewport
              viewOnly={viewOnly}
              onConnect={() => setConnected(true)}
              onDisconnect={() => setConnected(false)}
              style={{ width: "100%", height: "100%", background: "#000" }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
