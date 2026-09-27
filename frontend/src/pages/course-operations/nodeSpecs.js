/* 課程機器規格合計：課程環境選項、課程環境清單與班級機器頁共用同一種算法。
   節點有兩種形狀：課程環境正規化後的 memory（GB）／disk，與班級 machine_nodes 的 memory_mb／disk_gb。 */

const toNumber = (value) => Number(value) || 0;

/** 原始合計，記憶體以 GB 計且不四捨五入；要乘上學生數時先乘再在顯示前取整 */
export function sumNodeSpecs(nodes) {
  return (nodes ?? []).reduce(
    (sum, node) => ({
      cpu: sum.cpu + toNumber(node.cpu),
      memoryGb: sum.memoryGb + (node.memory != null ? toNumber(node.memory) : toNumber(node.memory_mb) / 1024),
      disk: sum.disk + toNumber(node.disk ?? node.disk_gb),
    }),
    { cpu: 0, memoryGb: 0, disk: 0 },
  );
}

/** 顯示用合計；students 給幾就乘幾（每位學生一套），記憶體最後才取整成 GB */
export function environmentSpecs(nodes, students = 1) {
  const totals = sumNodeSpecs(nodes);
  return {
    cpu: totals.cpu * students,
    memory: Math.round(totals.memoryGb * students),
    disk: totals.disk * students,
  };
}
