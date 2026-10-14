/**
 * 批量操作各自適用哪些機器：規則同單台的電源選單（components/PowerMenu），
 * 狀態對不上的先略過，不送去後端撞錯（例如對已關機的機器按關機）。
 */
const APPLIES = {
  start: (r) => (r.status === "stopped" || r.status === "paused") && !r.start_blocked_reason,
  shutdown: (r) => r.status === "running",
  reboot: (r) => r.status === "running",
  reset: (r) => r.status === "running",
  /* starting＝開機卡住時的退路，同單台選單 */
  stop: (r) => r.status === "running" || r.status === "starting",
  delete: () => true,
};

/** 選取的機器裡，這個動作實際要送出的 vmid */
export function applicableVmids(resources, action) {
  const applies = APPLIES[action] ?? (() => true);
  return resources.filter(applies).map((r) => r.vmid);
}
