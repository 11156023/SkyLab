import { useEffect } from "react";

/* 同時可能有多個覆蓋層（抽屜＋彈窗），用計數避免先關的那個把鎖解掉 */
let lockCount = 0;

function lockScroll() {
  lockCount += 1;
  if (lockCount !== 1) return;
  const root = document.documentElement;
  /* 原本有捲軸才保留捲軸寬度：不然捲軸一消失，整頁內容和靠右的浮動元件會往右跳 */
  if (window.innerWidth - root.clientWidth > 0) root.style.scrollbarGutter = "stable";
  root.style.overflow = "hidden";
}

function unlockScroll() {
  lockCount -= 1;
  if (lockCount !== 0) return;
  const root = document.documentElement;
  root.style.overflow = "";
  root.style.scrollbarGutter = "";
}

/**
 * active 為 true 時鎖住頁面捲動（覆蓋層／抽屜開啟時，底下頁面不應該還能滑）。
 * 多個呼叫端共用同一把鎖，全部釋放才恢復捲動。
 */
export default function useBodyScrollLock(active) {
  useEffect(() => {
    if (!active) return undefined;
    lockScroll();
    return unlockScroll;
  }, [active]);
}

const MODAL_SELECTOR = '[aria-modal="true"]';

/**
 * 全站只掛一次：畫面上有任何 aria-modal 的彈窗就鎖住底下頁面捲動。
 * 各彈窗不用自己呼叫鎖定，只要照規範在對話框本體標 role="dialog"／"alertdialog" 加 aria-modal="true"。
 */
export function useModalScrollLock() {
  useEffect(() => {
    let locked = false;
    const sync = () => {
      const hasModal = document.querySelector(MODAL_SELECTOR) != null;
      if (hasModal === locked) return;
      locked = hasModal;
      if (hasModal) lockScroll();
      else unlockScroll();
    };
    const observer = new MutationObserver(sync);
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["aria-modal"],
    });
    sync();
    return () => {
      observer.disconnect();
      if (locked) unlockScroll();
    };
  }, []);
}
