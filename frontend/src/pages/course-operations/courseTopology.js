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
