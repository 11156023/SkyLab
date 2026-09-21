import { useCallback, useEffect, useRef, useState } from "react";

/**
 * useScrollEdges
 * 回報捲動容器的上／下還有沒有被裁掉的內容，交給 CSS 在該側畫漸層淡出。
 * 用在藏了捲軸的區塊（側欄導覽），否則被裁一半的項目會跟相鄰區塊糊在一起，
 * 看不出來「下面還有」。
 *
 * 用法：
 *   const { ref, edges } = useScrollEdges();
 *   <nav ref={ref} data-scroll-edges={edges}>…</nav>
 *
 * @returns {{ ref: object, edges: "none"|"top"|"bottom"|"both" }}
 */
export default function useScrollEdges() {
  const ref = useRef(null);
  const [edges, setEdges] = useState("none");

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    // 縮放與小數列高會讓捲到底時仍差不到 1px，留一點容忍值免得淡出閃爍
    const top = el.scrollTop > 1;
    const bottom = el.scrollTop + el.clientHeight < el.scrollHeight - 1;
    setEdges(top && bottom ? "both" : top ? "top" : bottom ? "bottom" : "none");
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    el.addEventListener("scroll", measure, { passive: true });
    // 群組展開／收合是 grid-template-rows 轉場，結束後高度才定案
    el.addEventListener("transitionend", measure);
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => {
      el.removeEventListener("scroll", measure);
      el.removeEventListener("transitionend", measure);
      observer.disconnect();
    };
  }, [measure]);

  // 項目增減（切換管理員側欄、釘選）只反映在重繪上，每次繪完都重量一次
  useEffect(measure);

  return { ref, edges };
}
