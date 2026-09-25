import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { computePosition, isAnchorOffscreen } from "../components/PowerMenu/position";
import useOutsideClick from "./useOutsideClick";

/**
 * portal 到 body、position: fixed 的錨定選單共用邏輯：
 * 依錨點定位、捲動／縮放時重算、錨點捲出視窗就關閉、外點與 Esc 關閉。
 * 回傳 { ref, pos }：ref 掛在選單元素上，pos 為 null 時表示還沒量到高度、先保持隱形。
 * width 對應 computePosition 的選單寬度（不傳就用 position.js 的預設值）。
 */
export default function useAnchoredMenu({ anchorRef, onClose, width }) {
  const ref = useRef(null);
  const [pos, setPos] = useState(null);

  // 捲動時要判斷是否該關閉，但 onClose 每次 render 都是新的 function，
  // 存進 ref 才不會讓監聽反覆解綁重綁
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; });

  const reposition = useCallback(() => {
    const anchor = anchorRef?.current;
    const menu = ref.current;
    if (!anchor || !menu) return;

    const rect = anchor.getBoundingClientRect();
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    // 錨點被捲出視窗後選單只會卡在邊緣，直接收起來
    if (isAnchorOffscreen(rect, viewport)) {
      onCloseRef.current();
      return;
    }
    setPos(computePosition(rect, menu.offsetHeight, viewport, width));
  }, [anchorRef, width]);

  // 要先量到選單實際高度才知道往上或往下翻，定位完成前保持隱形
  useLayoutEffect(() => { reposition(); }, [reposition]);

  // fixed 選單不會跟著錨點捲動，捲動／縮放時重算（capture 才收得到內層容器的 scroll）
  useEffect(() => {
    const opts = { passive: true, capture: true };
    window.addEventListener("scroll", reposition, opts);
    window.addEventListener("resize", reposition);
    return () => {
      window.removeEventListener("scroll", reposition, opts);
      window.removeEventListener("resize", reposition);
    };
  }, [reposition]);

  useOutsideClick(ref, anchorRef, onClose, { escape: true });

  return { ref, pos };
}
