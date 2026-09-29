import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { VncScreen } from "react-vnc";
import { AuthStorage } from "../../../services/auth";
import { useAuth } from "../../../contexts/AuthContext";
import { recordMachineUse } from "../../../services/recentMachines";
import { ResourcesService } from "../../../services/resources";
import MIcon from "../../../components/MIcon";
import Modal from "../../../components/Modal/Modal";
import { useClassroomTakeover } from "../../../components/Classroom/ClassroomStudentLayer";
import useDialogPresence from "../../../hooks/useDialogPresence";
import TakeoverOverlay from "../../../components/Classroom/TakeoverOverlay";
import { wsBaseUrl } from "../../../utils/wsUrl";
import styles from "./ConsoleDialog.module.scss";

const CONSOLE_INFO_TIMEOUT_MS = 15000;

export default function VncDialog({ resource, onClose }) {
  const { user } = useAuth();
  const { t } = useTranslation("personal");
  const vncRef      = useRef(null);
  const dialogRef   = useRef(null);
  const mountedRef  = useRef(true);
  const titleId     = useId();
  const [connected, setConnected]       = useState(false);
  const [wsUrl, setWsUrl]               = useState("");
  const [vncTicket, setVncTicket]       = useState("");
  const [error, setError]               = useState("");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const underTakeover = useClassroomTakeover(resource?.vmid);
  // 接管覆蓋層的進出場
  const takeover = useDialogPresence(underTakeover);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", handler);
    return () => document.removeEventListener("fullscreenchange", handler);
  }, []);

  useEffect(() => {
    if (!resource?.vmid) return;
    /* vmid 換掉或卸載都會先跑 cleanup 把 cancelled 設為 true，舊請求的回呼就此作廢 */
    let cancelled = false;

    setConnected(false);
    setWsUrl("");
    setVncTicket("");
    setError("");

    const timeoutId = window.setTimeout(() => {
      if (cancelled) return;
      setError(t("VncDialog.timeoutError"));
    }, CONSOLE_INFO_TIMEOUT_MS);

    ResourcesService.getConsole(resource.vmid)
      .then((data) => {
        if (cancelled) return;
        window.clearTimeout(timeoutId);
        const token  = AuthStorage.getAccessToken() ?? "";
        const ticket = data.ticket ?? "";
        const port   = data.port   ?? "";
        if (!ticket) {
          setError(t("VncDialog.connectFailed"));
          return;
        }
        let url = `${wsBaseUrl()}/ws/vnc/${resource.vmid}?token=${encodeURIComponent(token)}&vnc_ticket=${encodeURIComponent(ticket)}`;
        if (port) url += `&vnc_port=${encodeURIComponent(port)}`;
        /* 慢的請求可能在逾時提示出現後才成功：拿到連線資訊就清掉提示，不讓紅字壓在可用的畫面上 */
        setError("");
        setVncTicket(ticket);
        setWsUrl(url);
      })
      .catch((e) => {
        if (cancelled) return;
        window.clearTimeout(timeoutId);
        setError(e.message ?? t("VncDialog.fetchInfoFailed"));
      });

    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [resource?.vmid]);

  const [closing, setClosing] = useState(false);
  const closeTimerRef = useRef(null);

  /* 卸載時清掉離場動畫的計時器，不讓已消失的元件回頭呼叫 onClose */
  useEffect(() => () => window.clearTimeout(closeTimerRef.current), []);

  function handleClose() {
    // 先播放離場動畫，再通知父層卸載
    if (closing) return;
    setClosing(true);
    closeTimerRef.current = window.setTimeout(onClose, 150);
  }

  async function handleClipboard() {
    try {
      const text = await navigator.clipboard.readText();
      vncRef.current?.clipboardPaste?.(text);
    } catch {}
  }

  function toggleFullscreen(containerEl) {
    if (!document.fullscreenElement) containerEl?.requestFullscreen?.();
    else document.exitFullscreen?.();
  }

  return (
    /* 畫面型：蓋過 AI 助手，鍵盤全部交給遠端桌面，所以 Esc 不關 */
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
        <span className={styles.headerIcon}><MIcon name="desktop_windows" size={18} /></span>
        <span className={styles.headerTitleGroup}>
          <span id={titleId} className={styles.headerTitle}>{t("VncDialog.titlePrefix", { name: resource.name })}</span>
          <span className={`${styles.statusDot} ${connected ? styles.dot_connected : styles.dot_connecting}`} />
          <span className={styles.statusText}>{connected ? t("VncDialog.statusConnected") : t("VncDialog.statusConnecting")}</span>
        </span>
        {connected && (
          <>
            <button type="button" className={styles.headerBtn} title="Ctrl+Alt+Del" onClick={() => vncRef.current?.sendCtrlAltDel?.()}>
              <MIcon name="keyboard" size={16} />
              <span style={{ fontSize: 11 }}>Ctrl+Alt+Del</span>
            </button>
            <button type="button" className={styles.headerBtn} title={t("VncDialog.pasteClipboard")} onClick={handleClipboard}>
              <MIcon name="content_paste" size={16} />
            </button>
          </>
        )}
        <button type="button" className={styles.headerBtn} title={isFullscreen ? t("VncDialog.exitFullscreen") : t("VncDialog.fullscreen")} onClick={() => toggleFullscreen(dialogRef.current)}>
          <MIcon name={isFullscreen ? "fullscreen_exit" : "fullscreen"} size={16} />
        </button>
        <button type="button" className={styles.closeBtn} onClick={handleClose} aria-label={t("Modal.close", { ns: "common" })}>
          <MIcon name="close" size={18} />
        </button>
      </div>

      {error && (
        <div className={styles.statusBanner}>
          <MIcon name="error_outline" size={16} />{error}
        </div>
      )}

      {!error && !wsUrl && (
        <div className={styles.statusBanner}>
          <MIcon name="hourglass_empty" size={16} spin />{t("VncDialog.fetchingInfo")}
        </div>
      )}

      {wsUrl && (
        <div className={styles.vncWrap}>
          {takeover.open && <TakeoverOverlay closing={takeover.closing} />}
          <VncScreen
            ref={vncRef}
            url={wsUrl}
            rfbOptions={{
              credentials: {
                username: "",
                password: vncTicket,
                target: "",
              },
            }}
            style={{ width: "100%", height: "100%" }}
            onConnect={() => {
              if (!mountedRef.current) return;
              setConnected(true);
              recordMachineUse(user?.id, resource.vmid);
            }}
            onDisconnect={() => mountedRef.current && setConnected(false)}
            scaleViewport
            background="#1e1e1e"
          />
        </div>
      )}
    </Modal>
  );
}
