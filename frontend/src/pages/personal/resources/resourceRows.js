/* 我的資源列表的小工具：列的 key、整列點擊的判斷，以及電源狀態與規格標籤 */

export const LIVE_STATUSES = new Set(["running", "starting", "stopped", "paused"]);
/* 有機器開機中時縮短輪詢，開完機後主控台按鈕能盡快亮起 */
export const BOOTING_POLL_INTERVAL = 5_000;

/* reboot / reset 之後機器仍是開著的；原本一律當成 stopped 會讓列上的狀態說謊。
   start / reboot 會重新跑開機 task，先標 starting（主控台停用），由後端輪詢確認開完機。 */
export function statusAfterAction(action) {
  if (action === "stop" || action === "shutdown") return "stopped";
  return action === "start" || action === "reboot" ? "starting" : "running";
}

export function machineSpecLabel(machine) {
  const parts = [];
  if (machine.cpu) parts.push(`${machine.cpu} CPU`);
  if (machine.memoryBytes) parts.push(`${Math.round(machine.memoryBytes / 1024 ** 3)} GB`);
  return parts.join(" · ");
}

/* 列的 key 必須穩定：主控台、選單等狀態都放在列元件裡，key 一變整列重掛，開著的 VNC／終端機就會斷線。
   實際機器用 vmid（遷移會換節點，所以不含 node）、佔位列用 request_id，兩者都沒有才退回用索引 */
export function resourceRowKey(resource, index) {
  if (resource.vmid > 0) return `vm:${resource.vmid}`;
  if (resource.request_id != null) return `req:${resource.request_id}`;
  const parts = [
    resource.type || "resource",
    resource.node || "unknown-node",
    resource.name ?? "unknown",
  ];
  return `${parts.join(":")}:${index}`;
}

/* 整列點擊要不要處理：選單用 portal 掛在 body，但 React 事件仍會沿元件樹冒泡回列上，
   點選單標題或空白處不能被當成點了這一列；列內的按鈕／連結等互動元素各自處理自己的點擊 */
export function isRowBackgroundClick(event, interactiveSelector = "button, a, input, select, label") {
  const target = event.target;
  if (!(target instanceof Node) || !event.currentTarget.contains(target)) return false;
  const element = target instanceof Element ? target : target.parentElement;
  return !element?.closest(interactiveSelector);
}
