import { useCallback, useEffect, useId, useRef, useState } from "react";
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
import { useToast } from "../../../hooks/useToast";
import TakeoverOverlay from "../../../components/Classroom/TakeoverOverlay";
import { wsBaseUrl } from "../../../utils/wsUrl";
import styles from "./ConsoleDialog.module.scss";

const CONSOLE_INFO_TIMEOUT_MS = 15000;

/* 瀏覽器剪貼簿只在 https／localhost 且使用者允許時讀得到；讀不到就只留文字框讓使用者自己貼 */
function canReadBrowserClipboard() {
  return typeof navigator !== "undefined" && typeof navigator.clipboard?.readText === "function";
}

export default function VncDialog({ resource, onClose }) {
  const { user } = useAuth();
  const { t } = useTranslation("personal");
  const toast = useToast();
  const vncRef      = useRef(null);
  const dialogRef   = useRef(null);
  const mountedRef  = useRef(true);
  const titleId     = useId();
  const [connected, setConnected]       = useState(false);
  const [wsUrl, setWsUrl]               = useState("");
  const [vncTicket, setVncTicket]       = useState("");
  const [error, setError]               = useState("");
  const [isFullscreen, setIsFullscreen] = useState(false);
  /* 剪貼簿面板：文字經 RFB ClientCutText 送進機器剪貼簿（與 PVE noVNC 同一套做法），
     機器內複製的文字也會回填到同一個文字框 */
  const [clipboardOpen, setClipboardOpen]       = useState(false);
  const [clipboardText, setClipboardText]       = useState("");
  const [clipboardSent, setClipboardSent]       = useState(false);
  const [clipboardEnabled, setClipboardEnabled] = useState(true);
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
    setClipboardOpen(false);
    setClipboardSent(false);

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
        /* 後端由 VM 的 Display 設定（clipboard=vnc）判定；沒開時貼上會無聲無效，面板要提醒 */
        setClipboardEnabled(data.clipboard !== false);
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

  /* noVNC 在連線時就把監聽器抓走，之後不會再更新，所以這個回呼必須是穩定的 */
  const handleGuestClipboard = useCallback((event) => {
    const text = event?.detail?.text;
    if (typeof text !== "string") return;
    setClipboardText(text);
    setClipboardSent(false);
  }, []);

  async function readBrowserClipboard() {
    try {
      const text = await navigator.clipboard.readText();
      setClipboardText(text);
      setClipboardSent(false);
    } catch {
      /* 權限被拒或非安全來源：明講讀不到，讓使用者直接貼進文字框 */
      toast.error(t("VncDialog.clipboardReadFailed"));
    }
  }

  function sendClipboard() {
    if (!clipboardText) return;
    vncRef.current?.clipboardPaste?.(clipboardText);
    setClipboardSent(true);
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
            <button
              type="button"
              data-testid="vnc-clipboard-toggle"
              className={`${styles.headerBtn} ${clipboardOpen ? styles.headerBtnActive : ""}`}
              title={t("VncDialog.clipboard")}
              aria-expanded={clipboardOpen}
              onClick={() => setClipboardOpen((open) => !open)}
            >
              <MIcon name="content_paste" size={16} />
              <span>{t("VncDialog.clipboard")}</span>
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
          {connected && clipboardOpen && (
            <div className={styles.clipboardPanel} role="region" aria-label={t("VncDialog.clipboard")}>
              {!clipboardEnabled && (
                <div className={styles.clipboardWarning}>
                  <MIcon name="warning" size={16} />
                  <span>{t("VncDialog.clipboardDisabled")}</span>
                </div>
              )}
              <textarea
                className={styles.clipboardTextarea}
                value={clipboardText}
                rows={5}
                spellCheck={false}
                placeholder={t("VncDialog.clipboardPlaceholder")}
                onChange={(e) => { setClipboardText(e.target.value); setClipboardSent(false); }}
              />
              <div className={styles.clipboardActions}>
                {canReadBrowserClipboard() && (
                  <button type="button" data-testid="vnc-clipboard-read" className={styles.clipboardBtnSecondary} onClick={readBrowserClipboard}>
                    <MIcon name="content_paste_go" size={16} />
                    {t("VncDialog.clipboardReadBrowser")}
                  </button>
                )}
                <button type="button" data-testid="vnc-clipboard-send" className={styles.clipboardBtnPrimary} disabled={!clipboardText} onClick={sendClipboard}>
                  <MIcon name="send" size={16} />
                  {t("VncDialog.clipboardSend")}
                </button>
              </div>
              <p className={styles.clipboardHint}>
                {clipboardSent ? t("VncDialog.clipboardSent") : t("VncDialog.clipboardHint")}
              </p>
            </div>
          )}
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
            onClipboard={handleGuestClipboard}
            scaleViewport
            background="#1e1e1e"
          />
        </div>
      )}
    </Modal>
  );
}
