/**
 * 拓撲節點 → 連線對話框的機器清單。
 *
 * 節點有三種身分（後端 can_manage / can_connect 決定）：
 * - 可管理：自己的機器、老師看到的學生課堂機。連線的任一端都能當。
 * - 唯讀：學生看到自己的課堂機。看得到規則、不能當連線任一端。
 * - 可連線不可管理（peerOnly）：老師開放給班級的機器。只能當「連到哪台」，
 *   埠限老師開放的那幾個，方向只能單向。
 *
 * 不是自己的機器在名稱後面帶擁有者，同一班學生的機器常常同名
 * （web-01 × 30），沒有擁有者根本分不出來。
 */

import { KIND_META, resolveKind } from "../MachineKindBadge/machineKind";

export function canManageNode(node) {
  return node?.can_manage !== false;
}

export function canConnectNode(node) {
  return node?.can_connect !== false;
}

/** 老師開放給班級的機器：可以連、不能管 */
export function isPeerNode(node) {
  return Boolean(node) && !canManageNode(node) && canConnectNode(node);
}

export function nodeLabel(node) {
  if (!node) return "";
  return node.owner_name ? `${node.name} · ${node.owner_name}` : node.name;
}

/** 機器來源的翻譯鍵：與資源列表、拓撲節點的徽章同一套判斷 */
export function nodeKindLabelKey(node) {
  const kind = resolveKind({
    kind: isPeerNode(node) ? "teacher_open" : node?.machine_kind,
    classRelation: node?.class_relation,
  });
  return KIND_META[kind].labelKey;
}

export function toDialogNodes(topologyNodes) {
  return (topologyNodes ?? [])
    .filter((n) => n.node_type !== "gateway" && n.vmid != null && canConnectNode(n))
    .map((n) => ({
      key: String(n.vmid),
      vmid: n.vmid,
      name: nodeLabel(n),
      kindLabelKey: nodeKindLabelKey(n),
      peerOnly: isPeerNode(n),
      allowedPorts: isPeerNode(n) ? (n.allowed_ports ?? []) : null,
    }));
}
