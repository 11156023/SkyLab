import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./Classroom.module.scss";
import MIcon from "../MIcon";
import Modal from "../Modal/Modal";
import NoVncScreen from "../NoVncScreen/NoVncScreen";
import { AuthStorage } from "../../services/auth";
import { ClassroomService } from "../../services/classroom";
import { wsBaseUrl } from "../../utils/wsUrl";
import { useToast } from "../../hooks/useToast";
import useConsoleKeyboard, { isKeyboardLockSupported } from "../../hooks/useConsoleKeyboard";

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
  const dialogRef = useRef(null);
  const surfaceRef = useRef(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [connected, setConnected] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [closing, setClosing] = useState(false);
  const titleId = useId();
  const closeTimerRef = useRef(null);
  /* 卸載清理只在掛載時綁一次，拿不到最新 state；用 ref 記住目前是否握有控制權、
     以及 handleClose 是否已經釋放過，避免重複送 release。 */
  const controllingRef = useRef(false);
  const releasedRef = useRef(false);
  const releaseArgsRef = useRef({ canControl, sessionId });
  releaseArgsRef.current = { canControl, sessionId };

  useEffect(() => () => {
    /* 父層直接卸載（瀏覽器上一頁、站內導頁）不會經過 handleClose；
       仍握有控制權就在這裡釋放，否則學生的主控台會一直被鎖住。 */
    const { canControl: ctl, sessionId: sid } = releaseArgsRef.current;
    if (ctl && sid && controllingRef.current && !releasedRef.current) {
      releasedRef.current = true;
      ClassroomService.setControl(sid, "release").catch(() => {});
    }
    // 卸載時保險斷線，並清掉離場動畫的計時器
    vncRef.current?.disconnect?.();
    window.clearTimeout(closeTimerRef.current);
  }, []);

  /* 掛載時把連線網址定下來：token 每次 render 都重讀，
     續期後字串一變 NoVncScreen 就會斷線重連，畫面會閃一下。 */
  const [wsUrl] = useState(() => {
    if (!sessionId) return "";
    const token = AuthStorage.getAccessToken() || "";
    return `${wsBaseUrl()}/ws/classroom/${sessionId}/watch?token=${encodeURIComponent(token)}`;
  });

  const viewOnly = !(canControl && controlling);

  // 接管時鍵盤要全部交給學生畫面；只看畫面時按鍵本來就不送出
  useConsoleKeyboard(surfaceRef, { active: connected && !viewOnly });

  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", handler);
    return () => document.removeEventListener("fullscreenchange", handler);
  }, []);

  function toggleFullscreen() {
    if (!document.fullscreenElement) dialogRef.current?.requestFullscreen?.();
    else document.exitFullscreen?.();
  }

  /* 工具列按鈕用滑鼠按時不搶走學生畫面的鍵盤焦點 */
  function keepScreenFocus(event) {
    event.preventDefault();
  }

  const handleControl = async () => {
    const action = controlling ? "release" : "take";
    setControlBusy(true);
    try {
      await ClassroomService.setControl(sessionId, action);
      controllingRef.current = action === "take";
      setControlling(action === "take");
      // 接管後按鍵要立刻進學生畫面，不必再把滑鼠移出移入
      if (action === "take") vncRef.current?.focus?.();
    } catch (e) {
      toast.error(e?.message ?? t("ClassroomWatchDialog.controlToggleFailed"));
    } finally {
      setControlBusy(false);
    }
  };

  const handleClose = () => {
    if (closing) return;
    // 關閉前先釋放控制權，避免學生端持續被鎖定
    if (canControl && controlling && sessionId && !releasedRef.current) {
      releasedRef.current = true;
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
      ref={dialogRef}
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
            onMouseDown={keepScreenFocus}
            onClick={handleControl}
          >
            <MIcon name="back_hand" size={14} />
            {controlling ? t("ClassroomWatchDialog.releaseControl") : t("ClassroomWatchDialog.takeControl")}
          </button>
        )}
        <button
          type="button"
          className={styles.headerBtn}
          title={isFullscreen
            ? t("ClassroomWatchDialog.exitFullscreen")
            : t(isKeyboardLockSupported() ? "ClassroomWatchDialog.fullscreenKeyboardHint" : "ClassroomWatchDialog.fullscreen")}
          aria-label={isFullscreen ? t("ClassroomWatchDialog.exitFullscreen") : t("ClassroomWatchDialog.fullscreen")}
          onMouseDown={keepScreenFocus}
          onClick={toggleFullscreen}
        >
          <MIcon name={isFullscreen ? "fullscreen_exit" : "fullscreen"} size={14} />
        </button>
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
          <div ref={surfaceRef} className={styles.vncSurface}>
            {/* viewOnly 隨接管狀態切換，NoVncScreen 會即時套到連線上 */}
            <NoVncScreen
              ref={vncRef}
              url={wsUrl}
              scaleViewport
              viewOnly={viewOnly}
              onConnect={() => setConnected(true)}
              onDisconnect={() => setConnected(false)}
              style={{ width: "100%", height: "100%", background: "#000" }}
            />
          </div>
        )}
      </div>
    </Modal>
  );
}
