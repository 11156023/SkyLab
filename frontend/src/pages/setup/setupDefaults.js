/**
 * 測試連線成功後的預設節點：原本選的節點仍在這次偵測到的節點裡才保留，
 * 否則改用主節點（換了主機再測時，舊主機的節點名稱不能被悄悄存進新連線）。
 */
export function pickDefaultNode(prevDefaultNode, nodes) {
  const list = nodes ?? [];
  if (prevDefaultNode && list.some((n) => n.name === prevDefaultNode)) {
    return prevDefaultNode;
  }
  const primary = list.find((n) => n.is_primary) ?? list[0];
  return primary?.name || "";
}
