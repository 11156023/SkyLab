/**
 * 批量操作各自適用哪些機器：規則同單台的電源選單（components/PowerMenu），
 * 狀態對不上的先略過，不送去後端撞錯（例如對已關機的機器按關機）。
 * 清單回的 can_control／can_delete 旗標也在這裡看：共享給我的機器可以開關機但不能刪、
 * 課堂機與環境內的機器單台不可刪，批量時一樣略過，真正的授權仍由後端逐台判定。
 */
const controllable = (r) => r.can_control !== false;

const APPLIES = {
  start: (r) => controllable(r) && (r.status === "stopped" || r.status === "paused") && !r.start_blocked_reason,
  shutdown: (r) => controllable(r) && r.status === "running",
  reboot: (r) => controllable(r) && r.status === "running",
  reset: (r) => controllable(r) && r.status === "running",
  /* starting＝開機卡住時的退路，同單台選單 */
  stop: (r) => controllable(r) && (r.status === "running" || r.status === "starting"),
  /* 申請中的佔位列沒有實體可刪 */
  delete: (r) => r.can_delete !== false && !r.is_placeholder,
};

/** 選取的機器裡，這個動作實際要送出的 vmid */
export function applicableVmids(resources, action) {
  const applies = APPLIES[action] ?? (() => true);
  return resources.filter(applies).map((r) => r.vmid);
}
