/**
 * 拓撲資料 → ReactFlow 的節點與邊。
 *
 * 標籤以「用途」為主而非只印埠號：有網域就顯示網域，有對外埠顯示埠對應，
 * 其餘盡量翻成服務名（22 → SSH），讓人不必點開就知道這條線在開什麼。
 * 三種入站模式的判斷方式與後端 PortSpec 一致（domain > external_port > 僅防火牆）。
 */

const GATEWAY_KEY = "gateway";

/* 節點四面都有連接點，線走哪一側由兩端的相對位置決定。
   尺寸與 FirewallPage.module.scss 的 .vmNode / .gwNode 一致，
   只在 ReactFlow 量到實際尺寸前當備援。 */
const NODE_SIZE = {
  vm: { w: 180, h: 60 },
  gateway: { w: 90, h: 90 },
};

export const HANDLE = {
  SOURCE: { top: "s-top", right: "s-right", bottom: "s-bottom", left: "s-left" },
  TARGET: { top: "t-top", right: "t-right", bottom: "t-bottom", left: "t-left" },
};

function centerOf(node) {
  const fallback = NODE_SIZE[node.type] ?? NODE_SIZE.vm;
  const w = node.measured?.width ?? fallback.w;
  const h = node.measured?.height ?? fallback.h;
  return {
    x: (node.position?.x ?? 0) + w / 2,
    y: (node.position?.y ?? 0) + h / 2,
  };
}

/**
 * 選出兩節點之間最短的連接側。
 * 水平距離較大就左右相接，否則上下相接，避免上下相鄰的節點也要繞一圈。
 */
export function pickHandles(sourceNode, targetNode) {
  if (!sourceNode || !targetNode) {
    return [HANDLE.SOURCE.right, HANDLE.TARGET.left];
  }
  const a = centerOf(sourceNode);
  const b = centerOf(targetNode);
  const dx = b.x - a.x;
  const dy = b.y - a.y;

  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0
      ? [HANDLE.SOURCE.right, HANDLE.TARGET.left]
      : [HANDLE.SOURCE.left, HANDLE.TARGET.right];
  }
  return dy >= 0
    ? [HANDLE.SOURCE.bottom, HANDLE.TARGET.top]
    : [HANDLE.SOURCE.top, HANDLE.TARGET.bottom];
}

/** 依目前節點位置重算每條邊該走哪一側；節點拖動時即時套用 */
export function routeEdges(edges, nodes) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  return edges.map((edge) => {
    const [sourceHandle, targetHandle] = pickHandles(
      byId.get(edge.source),
      byId.get(edge.target),
    );
    if (edge.sourceHandle === sourceHandle && edge.targetHandle === targetHandle) {
      return edge;
    }
    return { ...edge, sourceHandle, targetHandle };
  });
}

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
  /* 課程模板的對外 port 在開課前沒有 external_port，只能靠明講的 mode */
  if (port?.mode) return port.mode;
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

/**
 * 同一對節點之間的邊編號。
 * 一台機器同時有入站與出站時，兩條邊的端點完全相同，不編號就會疊成一條，
 * 看不出有兩條規則，標籤也會互相蓋住。以無向的節點對分組，畫的時候各走一側。
 */
function parallelLanes(edges) {
  const counts = new Map();
  const assigned = edges.map((edge) => {
    const a = String(edge.source_vmid ?? GATEWAY_KEY);
    const b = String(edge.target_vmid ?? GATEWAY_KEY);
    const key = a < b ? `${a}|${b}` : `${b}|${a}`;
    const index = counts.get(key) ?? 0;
    counts.set(key, index + 1);
    return { key, index };
  });
  return assigned.map(({ key, index }) => ({ index, count: counts.get(key) ?? 1 }));
}

/**
 * 上網線：從機器連出去到網際網路的邊（對外連線）。
 * 幾乎每台機器都有一條，全畫出來會把內部互通與對外開放淹掉，
 * 所以頁面預設把它們藏起來；要看再開。
 * 反方向「網際網路 → 機器」是對外開放，那是暴露面，一律照畫。
 */
export function isOutboundEdge(edge) {
  return edge?.source_vmid !== null && edge?.source_vmid !== undefined
    && edge?.target_vmid === null;
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

export function buildFlow(
  topology,
  { onSelectEdge, showLabel, showInternet = true, selectedEdgeId } = {},
) {
  const rawEdges = topology.edges ?? [];
  const exposure = exposureByVmid(rawEdges);

  const nodes = (topology.nodes ?? []).map((node) => ({
    id: node.node_type === "gateway" ? GATEWAY_KEY : String(node.vmid),
    type: node.node_type === "gateway" ? "gateway" : "vm",
    position: { x: node.position_x, y: node.position_y },
    data: { ...node, exposed_count: exposure.get(node.vmid) ?? 0 },
  }));

  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const lanes = parallelLanes(rawEdges);

  const edges = rawEdges.map((edge, i) => {
    const srcKey = edge.source_vmid === null ? GATEWAY_KEY : String(edge.source_vmid);
    const tgtKey = edge.target_vmid === null ? GATEWAY_KEY : String(edge.target_vmid);
    const id = `edge-${i}-${srcKey}-${tgtKey}`;
    const [sourceHandle, targetHandle] = pickHandles(
      nodeById.get(srcKey),
      nodeById.get(tgtKey),
    );
    return {
      id,
      source: srcKey,
      target: tgtKey,
      sourceHandle,
      targetHandle,
      type: "connection",
      hidden: !showInternet && isOutboundEdge(edge),
      data: {
        label: edgeLabel(edge.ports),
        showLabel,
        selected: id === selectedEdgeId,
        laneIndex: lanes[i].index,
        laneCount: lanes[i].count,
        edge,
        onSelect: onSelectEdge,
      },
    };
  });

  return { nodes, edges };
}
