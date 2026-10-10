import { useEffect, useLayoutEffect, useRef, useState } from "react";

/**
 * 清單手動拖移排序：拖過相鄰項目的中線就即時換位，換位時其他項目滑過去。
 * - 滑鼠／觸控筆：按住整列任何地方都能拖（列裡的按鈕、連結、欄位除外）
 * - 觸控：只有按住把手才拖，其餘地方照常捲動頁面（整列都攔下的話手指在清單上就捲不動）
 * 只管指標事件與命中判斷，順序本身由呼叫端的 state 管：onMove(from, to) 收到後自己搬陣列。
 * 鍵盤使用者仍靠上下移按鈕，把手只是提示與觸控用（aria-hidden）。
 *
 *   const { listRef, draggingKey, getItemProps, getHandleProps } = useDragReorder({ onMove, disabled });
 *   <div ref={listRef}>
 *     {items.map((key) => (
 *       <div key={key} {...getItemProps(key)}>
 *         <span {...getHandleProps()}><MIcon name="drag_indicator" /></span> …
 *
 * @param {Function} onMove    (from: number, to: number) => void
 * @param {boolean}  disabled  送出中等不能動的時候
 */
/* 按在這些元素上是要操作它們，不是要拖整列 */
const INTERACTIVE = "button, a, input, select, textarea, label, [contenteditable]";

export default function useDragReorder({ onMove, disabled = false }) {
  const listRef = useRef(null);
  const [draggingKey, setDraggingKey] = useState(null);
  const draggingRef = useRef(null);
  const onMoveRef = useRef(onMove);
  /* 搬完、畫面還沒重排前又來的 pointermove 會拿舊 DOM 算出同一組 from/to，再搬一次就搬回去了 */
  const pendingRef = useRef(false);
  /* 換位前各項的位置，重排後用來補一段滑動（FLIP），不然項目是瞬間跳過去 */
  const rectsRef = useRef(null);
  const cleanupRef = useRef(null);

  useLayoutEffect(() => {
    onMoveRef.current = onMove;
  });

  const itemsInList = () => [...(listRef.current?.querySelectorAll(":scope > [data-drag-key]") ?? [])];

  useLayoutEffect(() => {
    pendingRef.current = false;
    const before = rectsRef.current;
    if (!before) return;
    rectsRef.current = null;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    for (const el of itemsInList()) {
      const top = before.get(el.dataset.dragKey);
      const dy = top == null ? 0 : top - el.getBoundingClientRect().top;
      if (dy && typeof el.animate === "function") {
        el.animate([{ transform: `translateY(${dy}px)` }, { transform: "translateY(0)" }], {
          duration: 150,
          easing: "ease-out",
        });
      }
    }
  });

  useEffect(() => () => cleanupRef.current?.(), []);

  function handleMove(event) {
    if (draggingRef.current == null || pendingRef.current) return;
    const items = itemsInList();
    const from = items.findIndex((el) => el.dataset.dragKey === draggingRef.current);
    if (from < 0) return;
    /* 目標位置＝指標下方還有幾個「中線在指標上方」的其他項目 */
    let to = 0;
    items.forEach((el, index) => {
      if (index === from) return;
      const rect = el.getBoundingClientRect();
      if (event.clientY > rect.top + rect.height / 2) to += 1;
    });
    if (to === from) return;
    rectsRef.current = new Map(items.map((el) => [el.dataset.dragKey, el.getBoundingClientRect().top]));
    pendingRef.current = true;
    onMoveRef.current(from, to);
  }

  function start(key, event) {
    if (disabled || (event.pointerType === "mouse" && event.button !== 0)) return;
    /* 不讓瀏覽器開始選字／捲動；觸控靠把手的 touch-action: none */
    event.preventDefault();
    /* data-* 讀回來一定是字串，數字 key 要轉成字串才比得到 */
    draggingRef.current = String(key);
    setDraggingKey(key);
    const previousCursor = document.body.style.cursor;
    document.body.style.cursor = "grabbing";

    /* 監聽掛在 window：重排時項目會被搬動，掛在把手上的 pointer capture 不保證留得住 */
    const end = () => {
      draggingRef.current = null;
      setDraggingKey(null);
      document.body.style.cursor = previousCursor;
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      cleanupRef.current = null;
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    cleanupRef.current = end;
  }

  return {
    listRef,
    draggingKey,
    getItemProps: (key) => ({
      "data-drag-key": String(key),
      onPointerDown: (event) => {
        if (event.target.closest?.(INTERACTIVE)) return;
        if (event.pointerType === "touch" && !event.target.closest?.("[data-drag-handle]")) return;
        start(key, event);
      },
    }),
    getHandleProps: () => ({ "aria-hidden": true, "data-drag-handle": "" }),
  };
}
