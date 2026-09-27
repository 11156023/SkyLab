/* 我的資源列表的小工具：列的 key 與整列點擊的判斷 */

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
