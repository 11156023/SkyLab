/**
 * 班級容量預檢的數字格式。
 *
 * 一律保留一位小數：取整數會把「需要 7.5 GB、可用 7.9 GB」顯示成「7 / 7」，
 * 老師看到兩邊一樣卻被判不足，會以為計算錯了。整數就不帶 .0。
 */

const GIB = 1024 ** 3;

function oneDecimal(value) {
  return Number(Number(value).toFixed(1));
}

/** MB → GB 文字（後端容量合計的 memory_mb） */
export function mbToGb(mb) {
  return oneDecimal(Number(mb ?? 0) / 1024);
}

/** bytes → GB 文字；沒有值（節點離線、舊回應）顯示破折號 */
export function bytesToGb(bytes) {
  return bytes == null ? "—" : oneDecimal(Number(bytes) / GIB);
}

/**
 * 預檢回應的 placement_plan 轉成逐節點列，依節點名稱排序。
 * short* 表示該項需要量大於可用量；沒有可用量（節點離線）時不標。
 */
export function capacityNodeRows(placementPlan) {
  return Object.entries(placementPlan ?? {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([node, plan]) => {
      const availableCpu = plan.available_cpu_cores ?? null;
      const availableMemory = plan.available_memory_bytes ?? null;
      return {
        node,
        machines: plan.machines ?? 0,
        cpuNeed: plan.cpu_cores ?? 0,
        cpuAvailable: availableCpu == null ? "—" : oneDecimal(availableCpu),
        memoryNeed: bytesToGb(plan.memory_bytes ?? 0),
        memoryAvailable: bytesToGb(availableMemory),
        shortCpu: availableCpu != null && availableCpu < (plan.cpu_cores ?? 0),
        shortMemory: availableMemory != null && availableMemory < (plan.memory_bytes ?? 0),
      };
    });
}
