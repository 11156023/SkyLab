/**
 * courseTopology.js
 * 課程環境拓撲在畫布上怎麼說：編輯器與班級管理的唯讀預覽共用，
 * 兩邊看到的線、標籤與細節面板內容才會一致。純函式，不碰 React。
 */

import { describePort } from "../network/firewall/utils/buildFlow";
import { previewTemplateHostname } from "../../components/ConnectionDialog/connectionPayload";

/** 對外服務在畫布上的標籤：網址顯示範例網域，對外 port 顯示服務名 */
export function publicationLabel(t, publication, zones = []) {
  if (publication.mode === "domain") {
    const zone = zones.find((item) => item.id === publication.zoneId);
    return previewTemplateHostname(publication.hostnamePrefix, zone?.name);
  }
  return t("CourseTemplateEditorPage.publicationEdgeForward", {
    ns: "teaching",
    service: describePort({ port: publication.port, protocol: publication.protocol }),
  });
}

/** 對外服務給細節面板看的 port 物件：模板的網址是樣板、對外 port 開課前沒有號碼 */
export function publicationDetailPort(publication, zones = []) {
  if (publication.mode === "domain") {
    const zone = zones.find((item) => item.id === publication.zoneId);
    return { port: publication.port, protocol: "tcp", mode: "domain", domain: previewTemplateHostname(publication.hostnamePrefix, zone?.name) };
  }
  return { port: publication.port, protocol: publication.protocol, mode: "port_forward" };
}

/** 機器互通那條線的標籤：方向 · 服務 */
export function peerEdgeLabel(directionLabel, edge) {
  return `${directionLabel} · ${describePort({ port: edge.protocol === "any" ? 0 : edge.port, protocol: edge.protocol })}`;
}

/** 機器互通給細節面板看的 port 物件（舊資料的 any 等於不限通訊埠） */
export function peerDetailPort(edge) {
  return { port: edge.protocol === "any" ? 0 : Number(edge.port), protocol: edge.protocol };
}

/** 後端的 publication（snake_case）轉成畫布用的形狀 */
export function normalizePublication(publication, index = 0) {
  return {
    id: String(publication.id ?? `publication-${index + 1}`),
    nodeKey: publication.nodeKey ?? publication.node_key,
    mode: publication.mode === "domain" ? "domain" : "port_forward",
    port: Number(publication.port),
    protocol: publication.protocol ?? "tcp",
    hostnamePrefix: publication.hostnamePrefix ?? publication.hostname_prefix ?? "",
    zoneId: publication.zoneId ?? publication.zone_id ?? "",
  };
}

/* 連線標籤貼在「主體」那台機器旁約 52px 處、往遠離它的方向展開，最寬 170px
   （ConnectionEdge 的 LABEL_GAP 與 .edgeLabel 的 max-width）。左右相鄰的節點至少要隔這麼遠，
   標籤才不會被另一台蓋住；上下相鄰時標籤在線的正下／上方，只佔一行高 */
export const LABEL_CLEARANCE_X = 240;
export const LABEL_CLEARANCE_Y = 90;

/**
 * 唯讀拓撲顯示用的位置：保留原本的前後順序與列／欄關係，只把同一列（或同一欄）裡
 * 靠太近的節點推開到放得下連線標籤。推開的量一路累加到後面所有節點，
 * 上下對齊的欄才不會被拆散。只算顯示位置，不改課程環境裡存的座標。
 *
 * @param {{ id: string, position: { x: number, y: number }, width: number, height: number }[]} boxes
 * @returns {Map<string, { x: number, y: number }>} 依 id 對應推開後的位置
 */
export function spreadForEdgeLabels(boxes, { gapX = LABEL_CLEARANCE_X, gapY = LABEL_CLEARANCE_Y } = {}) {
  const positions = new Map(boxes.map((box) => [box.id, { ...box.position }]));
  const stretch = (axis, gap) => {
    const cross = axis === "x" ? "y" : "x";
    const size = axis === "x" ? "width" : "height";
    const crossSize = axis === "x" ? "height" : "width";
    const order = [...boxes].sort((a, b) => positions.get(a.id)[axis] - positions.get(b.id)[axis]);
    const placed = [];
    let shift = 0;
    for (const box of order) {
      const pos = positions.get(box.id);
      pos[axis] += shift;
      for (const other of placed) {
        const otherPos = positions.get(other.id);
        const sameLine = pos[cross] < otherPos[cross] + other[crossSize]
          && otherPos[cross] < pos[cross] + box[crossSize];
        const need = otherPos[axis] + other[size] + gap;
        if (sameLine && pos[axis] < need) {
          shift += need - pos[axis];
          pos[axis] = need;
        }
      }
      placed.push(box);
    }
  };
  stretch("x", gapX);
  stretch("y", gapY);
  return positions;
}

/** 課程機器節點（.vmNode＋.courseMachineNode）的固定尺寸，推開節點時照這個算間距 */
export const COURSE_MACHINE_NODE_SIZE = { width: 220, height: 68 };

/**
 * 不能拖曳的拓撲（班級上課環境的唯讀圖、已發布的教學環境）顯示用的位置：
 * 機器照 spreadForEdgeLabels 推開，網際網路節點放在最右邊那台右側、跟第一列同高，
 * 同樣留出放標籤的距離。只算畫面位置，不改存的座標。
 *
 * @param {{ id: string, position: { x: number, y: number } }[]} machines 原本（存的）位置
 * @returns {{ positions: Map<string, { x: number, y: number }>, internet: { x: number, y: number } }}
 */
export function frozenTopologyLayout(machines) {
  const positions = spreadForEdgeLabels(
    machines.map((machine) => ({ id: machine.id, position: machine.position, ...COURSE_MACHINE_NODE_SIZE })),
  );
  const placed = [...positions.values()];
  const rightEdge = Math.max(0, ...placed.map((position) => position.x + COURSE_MACHINE_NODE_SIZE.width));
  const topRow = placed.length ? Math.min(...placed.map((position) => position.y)) : 95;
  return { positions, internet: { x: rightEdge + LABEL_CLEARANCE_X, y: topRow } };
}
