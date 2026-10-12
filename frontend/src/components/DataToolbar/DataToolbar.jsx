import { useLayoutEffect, useRef, useState } from "react";
import styles from "./DataToolbar.module.scss";

/**
 * 清單／表格上方的工具列（批量操作慣例，規格見 docs/frontend-style-guide「清單工具列與批量操作」）。
 * 一直都在、至少 56px 高：平常放狀態切換與搜尋（沒有底色，控制項對齊清單左右緣）；
 * 有勾選時原地換成批量列（淡主色底的浮起卡片，黏在頂端），清單不會被推動。
 * 窄螢幕上篩選排成兩行時，批量列沿用同一個高度，切換時清單一樣不會跳。
 *
 * 搭配：hooks/useRowSelection（勾選狀態）、SelectCheckbox（每列與全選的勾選框）、
 * BatchBar／BatchAction（勾選時的內容）、ToolbarSearch（搜尋框）、utils/runBatch（逐筆送出）。
 *
 * @param {boolean}   selecting 有勾選：換成 batch 的內容
 * @param {ReactNode} batch     勾選時顯示的批量列（<BatchBar>）
 * @param {ReactNode} children  平常顯示的篩選、搜尋
 * @param {string}    className 額外樣式（例如跟下方清單的間距）
 */
export default function DataToolbar({ selecting = false, batch, children, className }) {
  const ref = useRef(null);
  /* 平常那條的高度（篩選換行時會比 56 高），勾選時拿來當最小高度 */
  const [idleHeight, setIdleHeight] = useState(0);

  useLayoutEffect(() => {
    const el = ref.current;
    if (selecting || !el) return undefined;
    const update = () => setIdleHeight(el.offsetHeight);
    update();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [selecting]);

  return (
    <div
      ref={ref}
      className={`${styles.toolbar}${selecting ? ` ${styles.toolbarSelecting}` : ""}${className ? ` ${className}` : ""}`}
      style={selecting && idleHeight ? { minHeight: idleHeight } : undefined}
    >
      {selecting ? batch : children}
    </div>
  );
}
