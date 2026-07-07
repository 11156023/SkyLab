import { useCallback, useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { AuthStorage } from "../../../services/auth";
import MIcon from "../../../components/MIcon";
import styles from "./ConsoleDialog.module.scss";

export default function TerminalDialog({ resource, onClose }) {
  const [status, setStatus]       = useState("connecting");
  const [error, setError]         = useState("");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const termDivRef  = useRef(null);
  const termRef     = useRef(null);
  const wsRef       = useRef(null);
  const fitAddonRef = useRef(null);
  const dialogRef   = useRef(null);

  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", handler);
    return () => document.removeEventListener("fullscreenchange", handler);
  }, []);

  function toggleFullscreen() {
    if (!document.fullscreenElement) dialogRef.current?.requestFullscreen?.();
    else document.exitFullscreen?.();
  }

  const terminalRef = useCallback((node) => {
    termDivRef.current = node;
  }, []);

  useEffect(() => {
    const el = termDivRef.current;
    if (!el || !resource?.vmid) return;

    let isAlive = true;
    let pingInterval = null;
    let isReady = false;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      theme: { background: "#1e1e1e", foreground: "#d4d4d4", cursor: "#ffffff" },
      scrollback: 5000,
      convertEol: true,
    });

    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());
    term.open(el);

    termRef.current     = term;
    fitAddonRef.current = fitAddon;

    setTimeout(() => { try { fitAddon.fit(); } catch {} }, 100);

    const apiUrl  = new URL(import.meta.env.VITE_API_URL || `${window.location.protocol}//${window.location.host}`);
    const proto   = apiUrl.protocol === "https:" ? "wss:" : "ws:";
    const token   = AuthStorage.getAccessToken() ?? "";
    const ws      = new WebSocket(`${proto}//${apiUrl.host}/ws/terminal/${resource.vmid}?token=${encodeURIComponent(token)}`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      pingInterval = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN && isReady) ws.send("2");
      }, 30000);
    };

    ws.onmessage = (e) => {
      if (!isAlive) return;
      const data = typeof e.data === "string" ? e.data : new Uint8Array(e.data);
      const isOK = typeof data === "string" ? data.startsWith("OK") : (data[0] === 79 && data[1] === 75);

      if (!isReady && isOK) {
        isReady = true;
        setStatus("connected");
        const rest = typeof data === "string" ? data.slice(2) : data.slice(2);
        if (rest.length) term.write(rest);
        requestAnimationFrame(() => requestAnimationFrame(() => {
          term.focus();
          try {
            fitAddon.fit();
            ws.send(`1:${term.cols}:${term.rows}:`);
          } catch {}
        }));
      } else {
        term.write(data);
      }
    };

    ws.onerror = () => { if (isAlive) { setStatus("error"); setError("無法連接到後端"); } };
    ws.onclose = (e) => { if (isAlive) { setStatus("disconnected"); setError(e.code === 1000 ? "連接已關閉" : `連接中斷 (${e.reason || e.code})`); } };

    term.onData((d) => {
      if (ws.readyState === WebSocket.OPEN && isReady) {
        ws.send(`0:${new TextEncoder().encode(d).length}:${d}`);
      }
    });

    term.onResize((s) => {
      if (ws.readyState === WebSocket.OPEN && isReady) ws.send(`1:${s.cols}:${s.rows}:`);
    });

    let resizeTimer = null;
    const onResize = () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => { try { fitAddon.fit(); } catch {} }, 50);
    };
    window.addEventListener("resize", onResize);
    const ro = new ResizeObserver(onResize);
    ro.observe(el);

    return () => {
      isAlive = false;
      clearTimeout(resizeTimer);
      clearInterval(pingInterval);
      window.removeEventListener("resize", onResize);
      ro.disconnect();
      term.dispose();
      ws.close();
    };
  }, [resource?.vmid]);

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div ref={dialogRef} className={styles.dialog} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <span className={styles.headerIcon}><MIcon name="terminal" size={18} /></span>
          <span className={styles.headerTitleGroup}>
            <span className={styles.headerTitle}>終端機 — {resource.name}</span>
            <span className={`${styles.statusDot} ${styles[`dot_${status}`]}`} />
            <span className={styles.statusText}>{STATUS_LABEL[status]}</span>
          </span>
          {status === "connected" && (
            <>
              <button type="button" className={`${styles.headerBtn} ${styles.headerBtnDanger}`} title="清除" onClick={() => termRef.current?.clear()}>
                <MIcon name="delete_sweep" size={16} />
              </button>
              <button type="button" className={`${styles.headerBtn} ${styles.headerBtnDanger}`} title="重置" onClick={() => termRef.current?.reset()}>
                <MIcon name="restart_alt" size={16} />
              </button>
            </>
          )}
          <button type="button" className={styles.headerBtn} title={isFullscreen ? "離開全螢幕" : "全螢幕"} onClick={toggleFullscreen}>
            <MIcon name={isFullscreen ? "fullscreen_exit" : "fullscreen"} size={16} />
          </button>
          <button type="button" className={styles.closeBtn} onClick={onClose}>
            <MIcon name="close" size={18} />
          </button>
        </div>

        <div className={styles.terminalArea}>
          <div ref={terminalRef} className={styles.terminalWrap} />
          {status !== "connected" && (
            <div className={styles.terminalOverlay}>
              {status === "connecting" && (
                <>
                  <span className={styles.terminalOverlayIcon}><MIcon name="terminal" size={40} /></span>
                  <span className={styles.terminalOverlayTitle}>正在連接</span>
                  <span className={styles.terminalOverlayDesc}>正在建立終端連線至 {resource.name}…</span>
                </>
              )}
              {status === "error" && (
                <>
                  <span className={`${styles.terminalOverlayIcon} ${styles.terminalOverlayIconError}`}><MIcon name="error_outline" size={40} /></span>
                  <span className={styles.terminalOverlayTitle}>連線失敗</span>
                  <span className={styles.terminalOverlayDesc}>{error}</span>
                </>
              )}
              {status === "disconnected" && (
                <>
                  <span className={`${styles.terminalOverlayIcon} ${styles.terminalOverlayIconMuted}`}><MIcon name="link_off" size={40} /></span>
                  <span className={styles.terminalOverlayTitle}>連線已中斷</span>
                  <span className={styles.terminalOverlayDesc}>{error}</span>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const STATUS_LABEL = {
  connecting:   "連接中",
  connected:    "已連接",
  disconnected: "已中斷",
  error:        "錯誤",
};
