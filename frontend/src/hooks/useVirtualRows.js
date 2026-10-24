import { useLayoutEffect, useState } from "react";
import { useWindowVirtualizer } from "@tanstack/react-virtual";

/** 列數少於這個值時不虛擬化：小清單直接全部渲染，行為與以前完全相同 */
export const VIRTUALIZE_FROM_ROWS = 60;
/** 表格列的預估高度（px），真正的高度由 measureElement 量回來 */
export const ROW_HEIGHT_ESTIMATE = 56;

/**
 * 以整個視窗為捲動容器的表格列虛擬化（頁面本身捲動，表格沒有固定高度）。
 *
 * 只渲染視窗內（加 overscan）的 `<tr>`，上下各補一列空白把總高度撐住。
 * 用法：
 *   const rows = useVirtualRows({ count: items.length });
 *   <tbody ref={rows.attachBody}>
 *     {rows.paddingTop > 0 && <tr aria-hidden style={{ height: rows.paddingTop }} />}
 *     {rows.items.map((item) => <Row key=… rowRef={rows.measureElement} rowIndex={item.index} … />)}
 *     {rows.paddingBottom > 0 && <tr aria-hidden style={{ height: rows.paddingBottom }} />}
 *   </tbody>
 * `rows.enabled` 為 false（列數不到門檻）時 `items` 仍涵蓋全部列，呼叫端不必分兩套寫法。
 *
 * 在元件裡請先解構（`const { attachBody, items, … } = useVirtualRows(…)`），不要在 render 讀 `rows.xxx`：
 * React Compiler 會把「有欄位被傳給 ref」的物件整個當成 ref，讀它的其他欄位就整個元件略過。
 */
export default function useVirtualRows({
  count,
  threshold = VIRTUALIZE_FROM_ROWS,
  estimateSize = ROW_HEIGHT_ESTIMATE,
  overscan = 12,
}) {
  "use no memo"; // virtualizer 是可變實例，getVirtualItems() 不能被 React Compiler 依實例快取
  /* tbody 用 callback ref（setState）接，不用 useRef：回傳物件裡帶著 ref 物件的話，
     呼叫端在 render 讀這個物件的任何欄位都會被 React Compiler 當成「render 時讀 ref」而整個元件略過 */
  const [bodyEl, attachBody] = useState(null);
  const [scrollMargin, setScrollMargin] = useState(0);
  const enabled = count >= threshold;

  /* tbody 距文件頂端的距離：上方的群組列展開／收合、表格上方的內容變高變矮、視窗縮放都會讓它變。
     ResizeObserver 盯整張表（任何列高度變化都會改表的高度），也盯 body：表格上方的工具列、提示
     長出來或收起來時表格本身大小不變，但整頁高度會變。 */
  useLayoutEffect(() => {
    const el = bodyEl;
    if (!el || !enabled) return undefined;
    const update = () => {
      setScrollMargin(Math.round(el.getBoundingClientRect().top + (window.scrollY || 0)));
    };
    update();
    const table = el.closest("table") ?? el;
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(update) : null;
    observer?.observe(table);
    observer?.observe(document.body);
    window.addEventListener("resize", update);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", update);
    };
  }, [bodyEl, enabled]);

  const virtualizer = useWindowVirtualizer({
    count: enabled ? count : 0,
    estimateSize: () => estimateSize,
    overscan,
    scrollMargin,
  });

  if (!enabled) {
    return {
      enabled,
      attachBody,
      items: Array.from({ length: count }, (_, index) => ({ index, key: index })),
      paddingTop: 0,
      paddingBottom: 0,
      measureElement: undefined,
    };
  }

  const items = virtualizer.getVirtualItems();
  const total = virtualizer.getTotalSize();
  /* v3 的 item.start 含 scrollMargin，換算回 tbody 內的位移 */
  const paddingTop = items.length ? items[0].start - scrollMargin : 0;
  const paddingBottom = items.length ? total - (items[items.length - 1].end - scrollMargin) : 0;
  return {
    enabled,
    attachBody,
    items,
    paddingTop: Math.max(0, paddingTop),
    paddingBottom: Math.max(0, paddingBottom),
    measureElement: virtualizer.measureElement,
  };
}
