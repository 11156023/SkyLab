/**
 * 拓撲的分組、收合與篩選（純函式，不碰 React）。
 *
 * 機器一多，單欄的拓撲會又長又亂：
 * - 分組：有班級的機器依班級、其他依機器類型，包進同一個群組框，框內格狀排列
 * - 收合：群組收成一張摘要卡，群組內往外的連線併成一條並標條數；群組內彼此的連線先不畫
 * - 篩選：搜尋名稱／IP、只看某個群組、只看有規則的機器，被篩掉的台數另外回報
 *
 * 分組模式下機器位置由這裡算（格狀），不吃後端存的單機位置；
 * 群組框與網際網路節點的拖曳位置由頁面存在瀏覽器本機，透過 positions 傳進來。
 */

import { KIND_META, resolveKind } from "../../../../components/MachineKindBadge/machineKind";
import { GATEWAY_KEY, isOutboundEdge } from "./buildFlow";

/** 機器數達到這個量才預設分組 */
export const GROUP_THRESHOLD = 12;
/** 機器數超過這個量，預設把沒有對外開放的群組收合 */
export const COLLAPSE_THRESHOLD = 20;

const GROUP_PREFIX = "group:";

/* 版面尺寸（與 FirewallPage.module.scss 的 .vmNode／.groupNode 一致） */
export const GROUP_LAYOUT = {
  originX: 160,
  originY: 80,
  gap: 40,
  cols: 4,
  cellW: 210,
  cellH: 96,
  padX: 16,
  header: 58,
  padBottom: 8,
  /* 單機卡（.vmNode）的高度：只有一台的群組不畫框，那台直接排在堆疊裡 */
  cardH: 68,
  collapsedW: 260,
  /* 收合卡：標題列（10＋35）＋摘要行（6＋23）＋下內距 10 ≈ 84（實測），原本 96 底下會空一截 */
  collapsedH: 84,
  gatewayGap: 120,
  gatewaySize: 90,
};

/* 群組節點 id 會再被拿去組合併邊的 id，進而變成 ConnectionEdge 的 marker id（`url(#arrow-<id>)`）：
   班級名稱裡的空白、引號、括號會讓 CSS url() 解析失敗、箭頭畫不出來，所以 key 先完整百分號編碼
   （encodeURIComponent 不會編 !'()*，補上） */
const encodeKey = (key) => encodeURIComponent(key).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
export const groupNodeId = (key) => `${GROUP_PREFIX}${encodeKey(key)}`;
export const isGroupNodeId = (id) => String(id).startsWith(GROUP_PREFIX);
/** 群組節點 id → 群組 key（groupNodeId 的反向） */
export const groupKeyOfId = (id) => decodeURIComponent(String(id).slice(GROUP_PREFIX.length));

/** 機器所屬群組：有班級名稱依班級，否則依機器類型（與 MachineKindBadge 同一套分類） */
export function groupKeyOf(node) {
  if (node?.teaching_class_name) return `class:${node.teaching_class_name}`;
  return `kind:${resolveKind({ kind: node?.machine_kind, classRelation: node?.class_relation })}`;
}

/** 群組 key → 顯示用描述：班級直接用名稱；類型給翻譯 key（components 命名空間的 MachineKind.*）與圖示 */
export function groupLabelOf(key) {
  if (key.startsWith("class:")) return { type: "class", name: key.slice("class:".length), icon: "school" };
  const kind = key.slice("kind:".length);
  const meta = KIND_META[kind] ?? KIND_META.personal;
  return { type: "kind", kind, labelKey: meta.labelKey, icon: meta.icon };
}

/** 拓撲裡出現過的群組 key（篩選下拉選單用），排序同群組框 */
export function listGroupKeys(topologyNodes) {
  const keys = new Set();
  for (const node of topologyNodes ?? []) {
    if (node.node_type === "gateway") continue;
    keys.add(groupKeyOf(node));
  }
  return [...keys].sort(compareGroupKeys);
}

/** 有沒有「規則」：不算每台幾乎都有的上網線（機器 → 網際網路），其餘任何一條都算 */
export function vmidsWithRules(edges) {
  const set = new Set();
  for (const edge of edges ?? []) {
    if (isOutboundEdge(edge)) continue;
    if (edge.source_vmid !== null && edge.source_vmid !== undefined) set.add(String(edge.source_vmid));
    if (edge.target_vmid !== null && edge.target_vmid !== undefined) set.add(String(edge.target_vmid));
  }
  return set;
}

/** 依 key 排序：班級在前（依名稱），類型在後 */
function compareGroupKeys(a, b) {
  const ca = a.startsWith("class:") ? 0 : 1;
  const cb = b.startsWith("class:") ? 0 : 1;
  return ca - cb || a.localeCompare(b, "zh-Hant");
}

/**
 * 把 VM 節點分組並算出摘要。
 * @param {Array} vmNodes buildFlow 產出的 VM 節點（data 為拓撲節點）
 * @returns {Array<{ key, vmNodes, total, running, exposed }>}
 */
export function buildGroups(vmNodes, rawEdges = []) {
  const map = new Map();
  for (const node of vmNodes) {
    const key = groupKeyOf(node.data);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(node);
  }
  return [...map.keys()].sort(compareGroupKeys).map((key) => {
    const members = map.get(key).sort((a, b) => String(a.data.name).localeCompare(String(b.data.name), "zh-Hant"));
    const ids = new Set(members.map((n) => n.id));
    return {
      key,
      vmNodes: members,
      total: members.length,
      running: members.filter((n) => n.data.status === "running").length,
      exposed: members.reduce((sum, n) => sum + (n.data.exposed_count ?? 0), 0),
      /* 規則數：扣掉上網線，群組內任一台沾到的連線都算（跟「只看有規則」同一套定義） */
      rules: rawEdges.filter((e) => !isOutboundEdge(e)
        && (ids.has(String(e.source_vmid)) || ids.has(String(e.target_vmid)))).length,
    };
  });
}

/** 預設要不要收合：機器夠多時收起來，但有對外開放（暴露面）的群組保持展開 */
export function defaultCollapsed(group, totalVms) {
  return totalVms > COLLAPSE_THRESHOLD && group.exposed === 0;
}

/** 搜尋字串比對名稱或 IP（不分大小寫） */
function matchesQuery(node, query) {
  if (!query) return true;
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [node.data.name, node.data.ip_address, String(node.data.vmid ?? "")]
    .some((value) => String(value ?? "").toLowerCase().includes(q));
}

/** 依篩選條件留下要顯示的 VM 節點；onlyExposed＝只留有對外開放（暴露面）的機器 */
export function filterVmNodes(vmNodes, _rawEdges, { query = "", groupKey = "", onlyExposed = false } = {}) {
  return vmNodes.filter((node) =>
    (!groupKey || groupKeyOf(node.data) === groupKey)
    && (!onlyExposed || (node.data.exposed_count ?? 0) > 0)
    && matchesQuery(node, query),
  );
}

/** 同一對節點之間的多條邊重新編號，畫的時候各走一側（與 buildFlow 的 parallelLanes 同一套） */
function relane(edges) {
  const counts = new Map();
  const keyed = edges.map((edge) => {
    const key = edge.source < edge.target ? `${edge.source}|${edge.target}` : `${edge.target}|${edge.source}`;
    const index = counts.get(key) ?? 0;
    counts.set(key, index + 1);
    return { edge, key, index };
  });
  return keyed.map(({ edge, key, index }) => ({
    ...edge,
    data: { ...edge.data, laneIndex: index, laneCount: counts.get(key) ?? 1 },
  }));
}

function groupSize(group, collapsed) {
  const L = GROUP_LAYOUT;
  if (collapsed) return { width: L.collapsedW, height: L.collapsedH };
  const cols = Math.max(1, Math.min(L.cols, group.total));
  const rows = Math.ceil(group.total / cols);
  return {
    width: L.padX * 2 + cols * L.cellW,
    height: L.header + rows * L.cellH + L.padBottom,
  };
}

/**
 * 依分組／收合／篩選，把 buildFlow 的結果轉成畫面上要畫的節點與邊。
 *
 * @param {{ nodes, edges }} flow buildFlow 的輸出
 * @param {Array} rawEdges 拓撲原始邊（篩「只看有規則」用）
 * @param {object} options
 * @param {boolean} options.grouped
 * @param {(group) => boolean} options.isCollapsed 群組是否收合（搜尋中由這裡強制展開）
 * @param {object} options.filter { query, groupKey, onlyExposed }
 * @param {object} options.positions 分組模式的拖曳位置 { groups: { [key]: {x,y} }, gateway: {x,y} }
 * @param {(key) => void} options.onExpandGroup 點收合後的合併連線時展開群組
 * @param {(key) => void} options.onToggleGroup 群組標題的收合切換
 * @returns {{ nodes, edges, groups, totalVms, visibleVms }}
 */
export function applyView(flow, rawEdges, {
  grouped = false,
  isCollapsed = () => false,
  filter = {},
  positions = {},
  onExpandGroup,
  onToggleGroup,
} = {}) {
  const vmNodes = flow.nodes.filter((n) => n.type === "vm");
  const gateway = flow.nodes.find((n) => n.type === "gateway");
  const visible = filterVmNodes(vmNodes, rawEdges, filter);
  const visibleIds = new Set(visible.map((n) => n.id));
  const summary = { totalVms: vmNodes.length, visibleVms: visible.length };

  /* 篩光了：網際網路節點也一起收掉，畫布讓給「沒有符合條件的機器」的空狀態 */
  if (vmNodes.length > 0 && visible.length === 0) {
    return { ...summary, groups: [], nodes: [], edges: [] };
  }

  /* 不分組：沿用後端存的單機位置，只把篩掉的機器與它們的線拿掉 */
  if (!grouped) {
    const keep = (id) => id === GATEWAY_KEY || visibleIds.has(id);
    return {
      ...summary,
      groups: [],
      nodes: [...(gateway ? [gateway] : []), ...visible],
      edges: flow.edges.filter((e) => keep(e.source) && keep(e.target)),
    };
  }

  const L = GROUP_LAYOUT;
  /* 搜尋中：命中的機器一定要看得到，所在群組強制展開 */
  const searching = Boolean(filter.query?.trim());
  /* 本來就只有一台的群組（看全部機器，不看篩選後）不畫框：框只會多佔一層、沒有分組意義。
     篩選後只剩一台的仍保留框，才看得出它屬於哪個班級 */
  const singleKeys = new Set(buildGroups(vmNodes).filter((g) => g.total === 1).map((g) => g.key));
  const groups = buildGroups(visible, rawEdges).map((group) => ({
    ...group,
    single: singleKeys.has(group.key),
    collapsed: searching || singleKeys.has(group.key) ? false : Boolean(isCollapsed(group)),
  }));

  const nodes = [];
  const owner = new Map(); // vm id → 收合時代表它的群組節點 id
  let cursorY = L.originY;
  let maxWidth = 0;

  for (const group of groups) {
    if (group.single) {
      /* 單機：對齊群組框內第一欄的卡片位置，不可拖（分組模式的位置都由這裡算） */
      nodes.push({
        ...group.vmNodes[0],
        draggable: false,
        position: { x: L.originX + L.padX, y: cursorY },
      });
      cursorY += L.cardH + L.gap;
      maxWidth = Math.max(maxWidth, L.padX * 2 + L.cellW);
      continue;
    }
    const id = groupNodeId(group.key);
    const { width, height } = groupSize(group, group.collapsed);
    const saved = positions.groups?.[group.key];
    const position = saved ?? { x: L.originX, y: cursorY };
    cursorY += height + L.gap;
    maxWidth = Math.max(maxWidth, width);

    /* 父節點必須排在子節點前面（ReactFlow 的規定） */
    nodes.push({
      id,
      type: "vmGroup",
      position,
      style: { width, height },
      data: {
        groupKey: group.key,
        label: groupLabelOf(group.key),
        total: group.total,
        running: group.running,
        exposed: group.exposed,
        rules: group.rules,
        collapsed: group.collapsed,
        /* 搜尋中群組被強制展開，這時切收合只會寫進覆寫、清掉搜尋後才突然收起：先不給切 */
        onToggle: searching ? undefined : onToggleGroup,
      },
      selectable: false,
      zIndex: -1,
    });

    group.vmNodes.forEach((vm, i) => {
      if (group.collapsed) {
        owner.set(vm.id, id);
        return;
      }
      nodes.push({
        ...vm,
        /* 框標題已寫明班級／類型，框內的卡不再掛來源徽章 */
        data: { ...vm.data, inGroup: true },
        parentId: id,
        extent: "parent",
        draggable: false,
        position: {
          x: L.padX + (i % L.cols) * L.cellW,
          y: L.header + Math.floor(i / L.cols) * L.cellH,
        },
      });
    });
  }

  if (gateway) {
    const totalHeight = Math.max(cursorY - L.gap - L.originY, L.gatewaySize);
    nodes.push({
      ...gateway,
      position: positions.gateway ?? {
        x: L.originX + maxWidth + L.gatewayGap,
        y: L.originY + (totalHeight - L.gatewaySize) / 2,
      },
    });
  }

  /* 邊：端點落在收合群組裡就改接到群組節點；同一對端點的併成一條並記條數 */
  const resolve = (id) => (id === GATEWAY_KEY ? id : visibleIds.has(id) ? owner.get(id) ?? id : null);
  const merged = new Map();
  const edges = [];
  for (const edge of flow.edges) {
    const source = resolve(edge.source);
    const target = resolve(edge.target);
    if (!source || !target) continue; // 一端被篩掉
    if (source === target) continue; // 同一個收合群組內部的連線
    if (!isGroupNodeId(source) && !isGroupNodeId(target)) {
      edges.push(edge);
      continue;
    }
    const key = `${source}->${target}`;
    const hit = merged.get(key);
    if (hit) {
      hit.members.push(edge);
      continue;
    }
    merged.set(key, { source, target, members: [edge] });
  }

  for (const { source, target, members } of merged.values()) {
    const first = members[0];
    const groupId = isGroupNodeId(source) ? source : target;
    const groupKey = groupKeyOfId(groupId);
    edges.push({
      ...first,
      id: `agg-${source}-${target}`,
      source,
      target,
      /* 只有每一條都是上網線時才跟著上網線開關藏起來 */
      hidden: members.every((m) => m.hidden),
      data: {
        ...first.data,
        /* 代表邊：方向（入站／出站／內部）與第一條相同，顏色與箭頭照舊 */
        edge: { ...first.data.edge, ports: members.length === 1 ? first.data.edge.ports : [] },
        aggregateCount: members.length,
        selected: false,
        onSelect: onExpandGroup ? () => onExpandGroup(groupKey) : undefined,
      },
    });
  }

  return { ...summary, groups, nodes, edges: relane(edges) };
}
