/**
 * wsUrl.js
 * WebSocket 連線的 base URL：依 VITE_API_URL（未設定則用目前頁面位置）組出
 * ws://host 或 wss://host。教室信令、觀看、VNC、終端機、任務推播與課程進度都共用這一份。
 */
export function wsBaseUrl() {
  const apiUrl = new URL(
    import.meta.env.VITE_API_URL || `${window.location.protocol}//${window.location.host}`,
  );
  const proto = apiUrl.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${apiUrl.host}`;
}
