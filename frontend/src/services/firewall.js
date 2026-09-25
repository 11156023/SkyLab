/**
 * firewall.js
 * 防火牆相關 API 封裝。
 * 端點參考：c:/git/SkyLab/frontend/src/client/compat.ts
 */

import { apiDelete, apiGet, apiPost, apiPut, apiDeleteJson } from "./api";

/** 取得拓撲（節點 + 連線） */
export function getTopology(options) {
  return apiGet("/api/v1/firewall/topology", options);
}

/**
 * 建立連線
 * @param {{ source_vmid: number|null, target_vmid: number|null, ports: PortSpec[], direction?: string }} data
 */
export function createConnection(data) {
  return apiPost("/api/v1/firewall/connections", data);
}

/**
 * 刪除連線（或特定 port）
 * @param {{ source_vmid: number|null, target_vmid: number|null, ports?: PortSpec[]|null }} data
 */
export function deleteConnection(data) {
  return apiDeleteJson("/api/v1/firewall/connections", data);
}

/** 儲存節點佈局位置 */
export function saveLayout(nodes) {
  return apiPut("/api/v1/firewall/layout", { nodes });
}

/** 取得指定 VM 的防火牆規則 */
export function getVmRules(vmid) {
  return apiGet(`/api/v1/firewall/${vmid}/rules`);
}

/** 取得指定 VM 的防火牆選項（啟用狀態、預設策略） */
export function getVmOptions(vmid) {
  return apiGet(`/api/v1/firewall/${vmid}/options`);
}

/** 在指定 VM 上新增一條自訂防火牆規則（body: FirewallRuleCreate） */
export function createVmRule(vmid, rule) {
  return apiPost(`/api/v1/firewall/${vmid}/rules`, rule);
}

/** 更新指定 VM 的一條規則（SkyLab 受管規則不可改） */
export function updateVmRule(vmid, pos, rule) {
  return apiPut(`/api/v1/firewall/${vmid}/rules/${pos}`, rule);
}

/** 刪除指定 VM 的一條規則（SkyLab 受管規則不可刪） */
export function deleteVmRule(vmid, pos) {
  return apiDelete(`/api/v1/firewall/${vmid}/rules/${pos}`);
}

/* ── 單台 VM：迷你拓撲與對外服務 ── */

/** 以這台 VM 為中心的迷你拓撲（Internet、這台 VM、有連線的其他 VM） */
export function getVmTopology(vmid) {
  return apiGet(`/api/v1/firewall/${vmid}/topology`);
}

/** 這台 VM 的對外服務清單（對外網址 / port 轉發 / 僅開放） */
export function listPublishedServices(vmid) {
  return apiGet(`/api/v1/firewall/${vmid}/services`);
}

/**
 * 發布一條對外服務
 * @param {{ port:number, protocol?:string, mode:"domain"|"port_forward"|"firewall_only", domain?:string, enable_https?:boolean, external_port?:number }} data
 */
export function publishService(vmid, data) {
  return apiPost(`/api/v1/firewall/${vmid}/services`, data);
}

/** 換掉一條服務的發布方式（current: {port, protocol}，replacement 同 publishService） */
export function replacePublishedService(vmid, current, replacement) {
  return apiPut(`/api/v1/firewall/${vmid}/services`, { current, replacement });
}

/** 撤下一條對外服務 */
export function unpublishService(vmid, { port, protocol = "tcp" }) {
  return apiDeleteJson(`/api/v1/firewall/${vmid}/services`, { port, protocol });
}
