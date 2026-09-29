/**
 * 拓撲內容區要顯示哪一種狀態。
 * 已有拓撲時即使正在重新載入也照樣畫圖：新增／刪除連線後的重新整理
 * 不可讓整張圖與開著的規則面板卸載。
 * @returns {"error" | "graph" | "spinner" | null}
 */
export function topologyView({ loading, error, topology }) {
  if (error) return "error";
  if (topology) return "graph";
  if (loading) return "spinner";
  return null;
}

/** 節點 → 佈局儲存 API 的一筆資料 */
export function toLayoutEntry(node, gatewayKey) {
  const isGateway = node.id === gatewayKey;
  return {
    vmid: isGateway ? null : Number(node.id),
    node_type: isGateway ? "gateway" : "vm",
    position_x: Math.round(node.position.x),
    position_y: Math.round(node.position.y),
  };
}

/**
 * 把這次拖曳的節點併進待存佈局（依節點 id 覆寫）。
 * debounce 期間連續拖了不同節點時，前一次的位置也要一起送出，不能只存最後一次。
 */
export function mergePendingLayout(pending, draggedNodes, gatewayKey) {
  for (const node of draggedNodes ?? []) {
    pending.set(node.id, toLayoutEntry(node, gatewayKey));
  }
  return pending;
}
