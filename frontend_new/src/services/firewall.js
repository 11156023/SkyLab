/**
 * firewall.js
 * 防火牆相關 API 封裝。
 * 端點參考：c:/git/SkyLab/frontend/src/client/compat.ts
 */

import { apiGet, apiPost, apiPut, apiDeleteJson } from "./api";

/** 取得拓撲（節點 + 連線） */
export function getTopology() {
  return apiGet("/api/v1/firewall/topology");
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
