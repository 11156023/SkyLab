import { useEffect, useRef } from "react";

/** 瀏覽器支援 Keyboard Lock（Chromium 系）：全螢幕時連 Ctrl+W 等保留快捷鍵都能交給主控台 */
export function isKeyboardLockSupported() {
  return typeof navigator !== "undefined" && typeof navigator.keyboard?.lock === "function";
}

function detectMac() {
  if (typeof navigator === "undefined") return false;
  return /Mac|iPhone|iPad|iPod/.test(navigator.userAgentData?.platform ?? navigator.platform ?? "");
}

/**
 * 這個按鍵事件的瀏覽器預設動作要不要擋掉。
 * Chrome 讓網頁以 preventDefault 覆蓋除了保留鍵（Ctrl+W/T/N、Ctrl+Shift+W/T/N、
 * Ctrl+Tab、Ctrl+PageUp/PageDown）以外的所有快捷鍵，這裡把會搶走主控台按鍵的那些都擋下；
 * 擋的只是瀏覽器動作，事件照常送到 xterm／noVNC。
 */
export function shouldBlockBrowserShortcut(event, isMac = detectMac()) {
  // 輸入法組字中的按鍵交給輸入法
  if (event.isComposing || event.keyCode === 229) return false;
  // Cmd／Win 組合是作業系統與 macOS 的編輯快捷鍵，不是主控台的輸入
  if (event.metaKey) return false;
  if (/^F([1-9]|1[0-2])$/.test(event.key)) return true;
  // Windows 的 AltGr 就是 Ctrl+Alt，要留給 keypress 打出 @、€ 等字元
  if (event.getModifierState?.("AltGraph") || (event.ctrlKey && event.altKey)) return false;
  if (event.ctrlKey) return true;
  // macOS 的 Option 用來組合特殊字元；其他平台的 Alt 會開瀏覽器選單（單按放開、Alt+D、Alt+F…）
  if (!isMac && (event.altKey || (event.type === "keyup" && event.key === "Alt"))) return true;
  return false;
}

/**
 * 主控台（終端機、遠端桌面）的鍵盤接管，active 時生效：
 * - 焦點在 surfaceRef 裡時，擋掉 Chrome 可覆蓋的瀏覽器快捷鍵（見 shouldBlockBrowserShortcut）；
 *   allowDefault(event) 回 true 的按鍵保留瀏覽器動作（例如終端機的 Ctrl+Shift+V 貼上）
 * - 包含 surfaceRef 的元素進入全螢幕時以 Keyboard Lock 鎖住鍵盤，保留鍵也送進主控台，長按 Esc 離開全螢幕
 * - 非全螢幕時保留鍵擋不住，離開頁面前先確認，避免在 bash／vim 按 Ctrl+W 直接關掉分頁
 */
export default function useConsoleKeyboard(surfaceRef, { active = true, allowDefault } = {}) {
  const allowDefaultRef = useRef(allowDefault);
  allowDefaultRef.current = allowDefault;

  useEffect(() => {
    if (!active) return undefined;
    const isMac = detectMac();

    const onKey = (event) => {
      if (!surfaceRef.current?.contains(event.target)) return;
      if (allowDefaultRef.current?.(event)) return;
      if (shouldBlockBrowserShortcut(event, isMac)) event.preventDefault();
    };

    const syncKeyboardLock = () => {
      if (!isKeyboardLockSupported()) return;
      const fullscreenEl = document.fullscreenElement;
      if (fullscreenEl && surfaceRef.current && fullscreenEl.contains(surfaceRef.current)) {
        navigator.keyboard.lock().catch((err) => {
          console.warn("[console] keyboard lock failed; reserved browser shortcuts stay with the browser", err);
        });
      } else {
        navigator.keyboard.unlock();
      }
    };

    const onBeforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = "";
    };

    // capture 階段在 xterm／noVNC 自己的監聽器之前先跑
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("keyup", onKey, true);
    window.addEventListener("beforeunload", onBeforeUnload);
    document.addEventListener("fullscreenchange", syncKeyboardLock);
    syncKeyboardLock();

    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("keyup", onKey, true);
      window.removeEventListener("beforeunload", onBeforeUnload);
      document.removeEventListener("fullscreenchange", syncKeyboardLock);
      if (isKeyboardLockSupported()) navigator.keyboard.unlock();
    };
  }, [active, surfaceRef]);
}
