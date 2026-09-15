/**
 * 拓撲節點 → 連線對話框的機器清單。
 *
 * 老師的拓撲會帶學生的課堂機（can_manage=true），學生的拓撲會帶自己的課堂機
 * 但不能管（can_manage=false）。連線兩端都要寫規則，所以只有可管理的機器
 * 才進對話框的下拉；不是自己的機器在名稱後面帶擁有者，同一班學生的機器
 * 常常同名（web-01 × 30），沒有擁有者根本分不出來。
 */

export function canManageNode(node) {
  return node?.can_manage !== false;
}

export function nodeLabel(node) {
  if (!node) return "";
  return node.owner_name ? `${node.name} · ${node.owner_name}` : node.name;
}

export function toDialogNodes(topologyNodes) {
  return (topologyNodes ?? [])
    .filter((n) => n.node_type !== "gateway" && n.vmid != null && canManageNode(n))
    .map((n) => ({ key: String(n.vmid), vmid: n.vmid, name: nodeLabel(n) }));
}
