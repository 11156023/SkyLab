import { useEffect } from "react";

/* 同時可能有多個覆蓋層（抽屜＋彈窗），用計數避免先關的那個把鎖解掉 */
let lockCount = 0;

/**
 * active 為 true 時鎖住 body 捲動（覆蓋層／抽屜開啟時，底下頁面不應該還能滑）。
 * 多個呼叫端共用同一把鎖，全部釋放才恢復捲動。
 */
export default function useBodyScrollLock(active) {
  useEffect(() => {
    if (!active) return undefined;
    lockCount += 1;
    if (lockCount === 1) document.body.style.overflow = "hidden";
    return () => {
      lockCount -= 1;
      if (lockCount === 0) document.body.style.overflow = "";
    };
  }, [active]);
}
