/**
 * 拓撲資料 → ReactFlow 的節點與邊。
 *
 * 標籤以「用途」為主而非只印埠號：有網域就顯示網域，有對外埠顯示埠對應，
 * 其餘盡量翻成服務名（22 → SSH），讓人不必點開就知道這條線在開什麼。
 * 三種入站模式的判斷方式與後端 PortSpec 一致（domain > external_port > 僅防火牆）。
 */

const GATEWAY_KEY = "gateway";

/* 常用埠 → 服務名。專有名詞，不進 i18n */
const PORT_SERVICE = {
  20: "FTP",
  21: "FTP",
  22: "SSH",
  23: "Telnet",
  25: "SMTP",
  53: "DNS",
  80: "HTTP",
  110: "POP3",
  143: "IMAP",
  389: "LDAP",
  443: "HTTPS",
  445: "SMB",
  636: "LDAPS",
  1433: "MSSQL",
  3000: "HTTP",
  3306: "MySQL",
  3389: "RDP",
  5432: "PostgreSQL",
  5900: "VNC",
  6379: "Redis",
  8000: "HTTP",
  8080: "HTTP",
  8443: "HTTPS",
  8888: "HTTP",
  27017: "MongoDB",
};

/** 入站發布模式：與後端 PortSpec 的三態對應 */
export const PORT_MODE = {
  DOMAIN: "domain",
  FORWARD: "port_forward",
  FIREWALL: "firewall_only",
};

export function portMode(port) {
  if (port?.domain) return PORT_MODE.DOMAIN;
  if (port?.external_port) return PORT_MODE.FORWARD;
  return PORT_MODE.FIREWALL;
}

export function serviceName(port) {
  if (!port?.port) return null;
  return PORT_SERVICE[port.port] ?? null;
}

/** 單一 port 的人類可讀描述 */
export function describePort(port) {
  if (!port) return "";
  if (port.domain) return port.domain;

  const proto = port.protocol ?? "tcp";
  /* port=0 是無埠協定（icmp/esp…），只有協定本身可講 */
  if (!port.port) return proto.toUpperCase();
  if (port.external_port) return `${port.external_port} → ${port.port}`;

  const name = serviceName(port);
  return name ? `${name} (${port.port})` : `${port.port}/${proto}`;
}

/** 完整列出所有 port，給刪除確認這類需要全貌的地方 */
export function portLabel(ports) {
  if (!ports?.length) return "";
  return ports.map(describePort).join(", ");
}

/** 邊上的短標籤：只留前幾項，其餘收成 +N，避免長標籤蓋住圖 */
export function edgeLabel(ports, maxItems = 2) {
  if (!ports?.length) return "";
  const shown = ports.slice(0, maxItems).map(describePort);
  const rest = ports.length - shown.length;
  return rest > 0 ? `${shown.join(", ")} +${rest}` : shown.join(", ");
}

/** 每台 VM 的對外暴露量：以網際網路為來源、指向該 VM 的 port 數 */
function exposureByVmid(edges) {
  const counts = new Map();
  for (const edge of edges) {
    if (edge.source_vmid !== null || edge.target_vmid === null) continue;
    /* 無埠協定的邊沒有 ports，仍算一條暴露面 */
    const n = edge.ports?.length || 1;
    counts.set(edge.target_vmid, (counts.get(edge.target_vmid) ?? 0) + n);
  }
  return counts;
}

export function buildFlow(topology, { onSelectEdge, showLabel, selectedEdgeId } = {}) {
  const rawEdges = topology.edges ?? [];
  const exposure = exposureByVmid(rawEdges);

  const nodes = (topology.nodes ?? []).map((node) => ({
    id: node.node_type === "gateway" ? GATEWAY_KEY : String(node.vmid),
    type: node.node_type === "gateway" ? "gateway" : "vm",
    position: { x: node.position_x, y: node.position_y },
    data: { ...node, exposed_count: exposure.get(node.vmid) ?? 0 },
  }));

  const edges = rawEdges.map((edge, i) => {
    const srcKey = edge.source_vmid === null ? GATEWAY_KEY : String(edge.source_vmid);
    const tgtKey = edge.target_vmid === null ? GATEWAY_KEY : String(edge.target_vmid);
    const id = `edge-${i}-${srcKey}-${tgtKey}`;
    return {
      id,
      source: srcKey,
      target: tgtKey,
      type: "connection",
      data: {
        label: edgeLabel(edge.ports),
        showLabel,
        selected: id === selectedEdgeId,
        edge,
        onSelect: onSelectEdge,
      },
    };
  });

  return { nodes, edges };
}
