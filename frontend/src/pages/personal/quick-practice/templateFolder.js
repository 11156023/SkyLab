/* 快速練習資料夾（QuickTemplateFolder）的純邏輯：夾裡放哪幾張紙、合計資源、外框路徑。
   不含畫面，方便單獨測試。 */

/** 夾裡最多露出幾張紙；機器更多時前三張照順序，最靠前蓋那張寫「+N 台」 */
export const MAX_SHEETS = 4;

/**
 * 一台機器一張紙。depth 0 是緊貼前蓋的那張，數字越大越靠後、位置越高。
 * @returns {{ key: string, depth: number, node?: object, more?: number }[]}
 */
export function folderSheets(nodes) {
  const list = nodes ?? [];
  const shown = list.length > MAX_SHEETS ? list.slice(0, MAX_SHEETS - 1) : list;
  const items = shown.map((node, index) => ({ key: node.id ?? node.node_key ?? String(index), node }));
  if (list.length > shown.length) items.push({ key: "more", more: list.length - shown.length });
  return items.map((item, index) => ({ ...item, depth: items.length - 1 - index }));
}

/** 角色只在跟名稱不同時才寫，免得一張紙上同一個字出現兩次 */
export function sheetRole(node) {
  const role = node?.role?.trim();
  return role && role !== node.name ? role : "";
}

/** GB，最多一位小數（1536 MB → 1.5） */
export function formatGb(mb) {
  return Math.round((Number(mb) || 0) / 102.4) / 10;
}

/** 每位學生會拿到的合計 CPU／記憶體；API 的 per_student 優先，沒有就自己加總 */
export function folderTotals(template) {
  const nodes = template?.nodes ?? [];
  const perStudent = template?.per_student;
  const cpu = perStudent?.cpu_cores ?? nodes.reduce((sum, node) => sum + (Number(node.cpu) || 0), 0);
  const memoryMb = perStudent?.memory_mb ?? nodes.reduce((sum, node) => sum + (Number(node.memory_mb) || 0), 0);
  return { cpu, memoryGb: formatGb(memoryMb) };
}

/* 外框依實際寬高重畫，圓角與分頁不會被拉伸。
   造型沿用復刻的 folder-card：後板上緣中間一片圓角分頁、前蓋上緣中間對應凹下，
   兩側往下微微收窄。 */
const round = (value) => Math.round(value * 10) / 10;

export function folderBackPath(width, height) {
  const tabHeight = 10, corner = 24, bottomCorner = 18, taper = 8, tab = 104, slope = 16;
  const w = round(width), h = round(height);
  const a = round(w / 2 - tab / 2), b = round(w / 2 + tab / 2);
  return [
    `M${corner} ${tabHeight}`, `L${a - slope} ${tabHeight}`,
    `C${a - slope / 2} ${tabHeight} ${a - slope / 2} 0 ${a} 0`, `L${b} 0`,
    `C${b + slope / 2} 0 ${b + slope / 2} ${tabHeight} ${b + slope} ${tabHeight}`,
    `L${w - corner} ${tabHeight}`, `Q${w} ${tabHeight} ${w} ${tabHeight + corner}`,
    `L${w - taper} ${h - bottomCorner}`, `Q${w - taper} ${h} ${w - taper - bottomCorner} ${h}`,
    `L${taper + bottomCorner} ${h}`, `Q${taper} ${h} ${taper} ${h - bottomCorner}`,
    `L0 ${tabHeight + corner}`, `Q0 ${tabHeight} ${corner} ${tabHeight}`, "Z",
  ].join(" ");
}

export function folderFrontPath(width, height) {
  const dip = 8, corner = 10, bottomCorner = 18, top = 3, taper = 8, notch = 96, slope = 14;
  const w = round(width), h = round(height);
  const a = round(w / 2 - notch / 2), b = round(w / 2 + notch / 2);
  return [
    `M${top + corner} 0`, `L${a - slope} 0`,
    `C${a - slope / 2} 0 ${a - slope / 2} ${dip} ${a} ${dip}`, `L${b} ${dip}`,
    `C${b + slope / 2} ${dip} ${b + slope / 2} 0 ${b + slope} 0`,
    `L${w - top - corner} 0`, `Q${w - top} 0 ${w - top} ${corner}`,
    `L${w - taper} ${h - bottomCorner}`, `Q${w - taper} ${h} ${w - taper - bottomCorner} ${h}`,
    `L${taper + bottomCorner} ${h}`, `Q${taper} ${h} ${taper} ${h - bottomCorner}`,
    `L${top} ${corner}`, `Q${top} 0 ${top + corner} 0`, "Z",
  ].join(" ");
}
