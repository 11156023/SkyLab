/**
 * NoVncScreen — 直接包 @novnc/novnc 的 RFB 畫面（VNC 主控台與教室觀看共用）。
 *
 * 原本用 react-vnc 的 VncScreen，它有三個讓鍵盤「有時候卡住」的設計：
 * - 滑鼠離開畫面就把 noVNC 畫布 blur、移回來才 focus。noVNC 每次按下滑鼠都會蓋一層
 *   全視窗的捕捉層，Chrome 在放開時就判定游標已離開畫面，所以在遠端畫面點一下或拖曳
 *   之後鍵盤就進不了機器，要等滑鼠再移動才恢復；游標停在標題列、剪貼簿面板或對話框外
 *   也一樣。
 * - 畫布失焦時 noVNC 不會放開按著的鍵（只在整個視窗失焦時放），離開那一刻按著的
 *   Ctrl／Shift 會永遠卡在機器裡。
 * - viewOnly 等屬性只在掛載時套用，老師接管後切成可操作不會生效。
 *
 * 這裡的焦點規則與 PVE 自己的 noVNC 一樣：連上由呼叫端給焦點、點畫面拿焦點，
 * 游標離開不收回；屬性改變即時套到連線上。
 *
 * @param {string} url WebSocket 網址；換了就重連
 * @param {string} password VNC 票證（教室觀看的下游是 security=None，留空）
 * @param {boolean} viewOnly 只看不操作，可隨時切換
 * @param {boolean} scaleViewport 遠端畫面縮放到容器大小
 * @param {string} background 容器底色
 * @param {boolean} focusOnClick 點畫面就取得鍵盤焦點
 * @param ref focus()、blur()、sendCtrlAltDel()、clipboardPaste(text)、disconnect()、rfb
 */
import { useEffect, useImperativeHandle, useRef } from "react";
/* 套件的 exports 只開放根路徑（對應 core/rfb.js） */
import RFB from "@novnc/novnc";

const FORWARDED_EVENTS = ["connect", "disconnect", "clipboard", "securityfailure"];

function applySettings(rfb, { viewOnly, scaleViewport, background, focusOnClick }) {
  rfb.viewOnly = viewOnly;
  rfb.scaleViewport = scaleViewport;
  rfb.background = background;
  rfb.focusOnClick = focusOnClick;
}

export default function NoVncScreen({
  ref,
  url,
  password = "",
  viewOnly = false,
  scaleViewport = true,
  background = "",
  focusOnClick = true,
  onConnect,
  onDisconnect,
  onClipboard,
  onSecurityFailure,
  className,
  style,
}) {
  const containerRef = useRef(null);
  /* 目前的連線：{ rfb, closed }，closed 避免 handle.disconnect() 與卸載各關一次 */
  const connectionRef = useRef(null);
  /* 回呼與屬性每次 render 都可能換新；noVNC 的監聽器只綁一次，一律經 ref 讀最新值 */
  const callbacksRef = useRef({});
  callbacksRef.current = {
    connect: onConnect,
    disconnect: onDisconnect,
    clipboard: onClipboard,
    securityfailure: onSecurityFailure,
  };
  const settingsRef = useRef({});
  settingsRef.current = { viewOnly, scaleViewport, background, focusOnClick };

  useEffect(() => {
    const target = containerRef.current;
    if (!target || !url) return undefined;

    const credentials = { username: "", password, target: "" };
    const rfb = new RFB(target, url, { credentials });
    applySettings(rfb, settingsRef.current);

    const listeners = FORWARDED_EVENTS.map((name) => [
      name,
      (event) => callbacksRef.current[name]?.(event),
    ]);
    /* 建構時已帶 credentials，noVNC 通常不會再問；伺服器真的要求時才會走到這裡 */
    listeners.push(["credentialsrequired", () => rfb.sendCredentials(credentials)]);
    for (const [name, listener] of listeners) rfb.addEventListener(name, listener);

    const connection = { rfb, closed: false };
    connectionRef.current = connection;

    return () => {
      connectionRef.current = null;
      for (const [name, listener] of listeners) rfb.removeEventListener(name, listener);
      if (!connection.closed) {
        connection.closed = true;
        rfb.disconnect();
      }
    };
  }, [url, password]);

  useEffect(() => {
    const rfb = connectionRef.current?.rfb;
    if (rfb) applySettings(rfb, settingsRef.current);
  }, [viewOnly, scaleViewport, background, focusOnClick]);

  useImperativeHandle(
    ref,
    () => ({
      focus: () => connectionRef.current?.rfb.focus({ preventScroll: true }),
      blur: () => connectionRef.current?.rfb.blur(),
      sendCtrlAltDel: () => connectionRef.current?.rfb.sendCtrlAltDel(),
      clipboardPaste: (text) => connectionRef.current?.rfb.clipboardPasteFrom(text),
      disconnect: () => {
        const connection = connectionRef.current;
        if (!connection || connection.closed) return;
        connection.closed = true;
        connection.rfb.disconnect();
      },
      get rfb() {
        return connectionRef.current?.rfb ?? null;
      },
    }),
    [],
  );

  return <div ref={containerRef} className={className} style={style} />;
}
