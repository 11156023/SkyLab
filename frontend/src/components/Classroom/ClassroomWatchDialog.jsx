import { useEffect, useId, useRef, useState } from "react";
import { VncScreen } from "react-vnc";
import { useTranslation } from "react-i18next";
import styles from "./Classroom.module.scss";
import MIcon from "../MIcon";
import Modal from "../Modal/Modal";
import { AuthStorage } from "../../services/auth";
import { ClassroomService } from "../../services/classroom";
import { wsBaseUrl } from "../../hooks/useClassroomSocket";
import { useToast } from "../../hooks/useToast";

/**
 * 教室觀看視窗：連 /ws/classroom/{session_id}/watch 的原生 RFB 資料面
 * （下游 security=None，不需 VNC ticket）。
 * - canControl：monitor 模式發起者可「接管/釋放」
 */
export default function ClassroomWatchDialog({
  sessionId,
  title,
  canControl = false,
  onClose,
}) {
  const { t } = useTranslation("components");
  const toast = useToast();
  const vncRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [closing, setClosing] = useState(false);
  const titleId = useId();
  const closeTimerRef = useRef(null);

  useEffect(() => () => {
    // 卸載時保險斷線，並清掉離場動畫的計時器
    vncRef.current?.disconnect?.();
    window.clearTimeout(closeTimerRef.current);
  }, []);

  /* 掛載時把連線網址定下來：token 每次 render 都重讀，
     續期後字串一變 VncScreen 就會斷線重連，畫面會閃一下。 */
  const [wsUrl] = useState(() => {
    if (!sessionId) return "";
    const token = AuthStorage.getAccessToken() || "";
    return `${wsBaseUrl()}/ws/classroom/${sessionId}/watch?token=${encodeURIComponent(token)}`;
  });

  const viewOnly = !(canControl && controlling);

  const handleControl = async () => {
    const action = controlling ? "release" : "take";
    setControlBusy(true);
    try {
      await ClassroomService.setControl(sessionId, action);
      setControlling(action === "take");
    } catch (e) {
      toast.error(e?.message ?? t("ClassroomWatchDialog.controlToggleFailed"));
    } finally {
      setControlBusy(false);
    }
  };

  const handleClose = () => {
    if (closing) return;
    // 關閉前先釋放控制權，避免學生端持續被鎖定
    if (canControl && controlling && sessionId) {
      ClassroomService.setControl(sessionId, "release").catch(() => {});
    }
    vncRef.current?.disconnect?.();
    // 先播放離場動畫，再通知父層卸載
    setClosing(true);
    closeTimerRef.current = window.setTimeout(onClose, 150);
  };

  /* 畫面型：蓋過 AI 助手；接管控制時鍵盤要全部交給學生畫面，所以 Esc 不關 */
  return (
    <Modal
      bare
      layer="screen"
      size="xl"
      className={styles.dialog}
      closing={closing}
      onClose={handleClose}
      aria-labelledby={titleId}
    >
      <div className={styles.header}>
        <span className={styles.headerIcon}>
          <MIcon name="cast" size={16} />
        </span>
        <span className={styles.headerTitleGroup}>
          <span id={titleId} className={styles.headerTitle}>{title || t("ClassroomWatchDialog.defaultTitle")}</span>
          <span
            className={`${styles.statusDot} ${connected ? styles.dot_connected : styles.dot_connecting}`}
          />
          <span className={styles.statusText}>
            {connected ? t("ClassroomWatchDialog.statusConnected") : t("ClassroomWatchDialog.statusConnecting")}
            {!viewOnly && t("ClassroomWatchDialog.statusTakenOverSuffix")}
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
            {controlling ? t("ClassroomWatchDialog.releaseControl") : t("ClassroomWatchDialog.takeControl")}
          </button>
        )}
        <button type="button" className={styles.closeBtn} onClick={handleClose} title={t("ClassroomWatchDialog.closeTitle")} aria-label={t("ClassroomWatchDialog.closeTitle")}>
          <MIcon name="close" size={18} />
        </button>
      </div>

      <div className={styles.vncWrap}>
        {!connected && wsUrl && (
          <div className={styles.vncLoading}>
            <MIcon name="hourglass_empty" size={28} spin />
            {t("ClassroomWatchDialog.connecting")}
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
    </Modal>
  );
}
