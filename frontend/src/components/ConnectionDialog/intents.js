/**
 * intents.js
 * ConnectionDialog 的「意圖」：使用者先說要做什麼，連線方向由意圖決定，
 * 不再自己排來源與目標。
 *
 * 純函式：三個入口（拓撲頁拉線、資源頁鎖定機器、規則面板）帶進來的初始值
 * 都在這裡推導成同一組狀態，對話框本身不用知道是誰開的。
 */

export const INTERNET_KEY = "internet";

export const INTENT = {
  PUBLISH: "publish",   // 網際網路 → VM：開放服務給外部
  OUTBOUND: "outbound", // VM → 網際網路：讓機器能上網
  PEER: "peer",         // VM → VM：兩台機器互通
  RULE: "rule",         // 直接寫一條 Proxmox 規則（進階）
};

export const INTENT_ORDER = [INTENT.PUBLISH, INTENT.OUTBOUND, INTENT.PEER, INTENT.RULE];

export const INTENT_META = {
  [INTENT.PUBLISH]:  { icon: "public",     labelKey: "ConnectionDialog.intentPublish",  descKey: "ConnectionDialog.intentPublishDesc" },
  [INTENT.OUTBOUND]: { icon: "north_east", labelKey: "ConnectionDialog.intentOutbound", descKey: "ConnectionDialog.intentOutboundDesc" },
  [INTENT.PEER]:     { icon: "sync_alt",   labelKey: "ConnectionDialog.intentPeer",     descKey: "ConnectionDialog.intentPeerDesc" },
  [INTENT.RULE]:     { icon: "tune",       labelKey: "ConnectionDialog.intentRule",     descKey: "ConnectionDialog.intentRuleDesc" },
};

export const isVmKey = (key) => Boolean(key) && key !== INTERNET_KEY;

/**
 * 從呼叫端給的初始值推導意圖與機器。
 *
 * 拉線帶入兩端就能直接決定意圖，跳過選意圖那一步；只給一端或什麼都沒給，
 * 就停在選意圖，但機器欄位先填好，選完意圖不必再選一次。
 * 有 fixedKey 時機器一律是它；互通則它固定當來源，另一端自由選。
 */
export function deriveInitialState({
  initialSource,
  initialTarget,
  initialTab,
  fixedKey = null,
  editing = false,
}) {
  const srcVm = isVmKey(initialSource) ? initialSource : null;
  const tgtVm = isVmKey(initialTarget) ? initialTarget : null;
  /* 給定一台機器後，「另一台」是兩端裡不等於它的那個 */
  const other = (key) => [tgtVm, srcVm].find((k) => k && k !== key) ?? "";
  const peerSource = fixedKey ?? srcVm ?? "";

  const base = {
    intent: null,
    vmKey: fixedKey ?? tgtVm ?? srcVm ?? "",
    peerSourceKey: peerSource,
    peerTargetKey: other(peerSource),
  };

  if (editing) return { ...base, intent: INTENT.PUBLISH, vmKey: fixedKey ?? tgtVm ?? "" };
  if (initialTab === "rule") return { ...base, intent: INTENT.RULE };

  if (initialSource === INTERNET_KEY && tgtVm) {
    return { ...base, intent: INTENT.PUBLISH, vmKey: tgtVm };
  }
  if (srcVm && initialTarget === INTERNET_KEY) {
    return { ...base, intent: INTENT.OUTBOUND, vmKey: srcVm };
  }
  if (srcVm && tgtVm && srcVm !== tgtVm) {
    return { ...base, intent: INTENT.PEER };
  }
  return base;
}

/** 意圖 + 選好的機器 → 實際送出用的兩端 */
export function endsOf(intent, { vmKey, peerSourceKey, peerTargetKey }) {
  switch (intent) {
    case INTENT.PUBLISH:
      return { sourceKey: INTERNET_KEY, targetKey: vmKey };
    case INTENT.OUTBOUND:
      return { sourceKey: vmKey, targetKey: INTERNET_KEY };
    case INTENT.PEER:
      return { sourceKey: peerSourceKey, targetKey: peerTargetKey };
    default:
      return { sourceKey: "", targetKey: "" };
  }
}
