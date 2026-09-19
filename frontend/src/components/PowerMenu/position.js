/* PowerMenu 的定位計算——抽成純函式方便單元測試，不碰 DOM */

/** 需與 PowerMenu.module.scss 的 .powerMenu width 一致 */
export const MENU_WIDTH = 240;
/** 選單與錨點按鈕的間距 */
export const ANCHOR_GAP = 6;
/** 與視窗邊緣的最小留白 */
export const EDGE_MARGIN = 8;

/**
 * 依錨點按鈕的視窗座標算出選單的 fixed 位置。
 * 下方放不下且上方較寬裕時往上翻，最後再夾進視窗範圍避免溢出。
 *
 * @param {{top: number, bottom: number, right: number}} rect 錨點的 getBoundingClientRect()
 * @param {number} menuHeight 選單實際高度
 * @param {{width: number, height: number}} viewport 視窗尺寸
 */
export function computePosition(rect, menuHeight, viewport, menuWidth = MENU_WIDTH) {
  const spaceBelow = viewport.height - rect.bottom - EDGE_MARGIN;
  const spaceAbove = rect.top - EDGE_MARGIN;
  const openUp = spaceBelow < menuHeight + ANCHOR_GAP && spaceAbove > spaceBelow;

  const rawTop  = openUp ? rect.top - ANCHOR_GAP - menuHeight : rect.bottom + ANCHOR_GAP;
  const rawLeft = rect.right - menuWidth; // 右緣對齊按鈕
  const maxTop  = viewport.height - menuHeight - EDGE_MARGIN;
  const maxLeft = viewport.width - menuWidth - EDGE_MARGIN;

  return {
    top:  Math.max(EDGE_MARGIN, Math.min(rawTop, maxTop)),
    left: Math.max(EDGE_MARGIN, Math.min(rawLeft, maxLeft)),
    openUp,
  };
}

/** 錨點被捲出視窗外（選單只會卡在邊緣，該收起來） */
export function isAnchorOffscreen(rect, viewport) {
  return rect.bottom < 0 || rect.top > viewport.height;
}
