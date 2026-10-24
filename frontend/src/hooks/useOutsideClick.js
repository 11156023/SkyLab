import { useEffect } from "react";

/**
 * 點到彈窗（ref）與觸發鈕（triggerRef）以外的地方就呼叫 onClose；
 * escape=true 時按 Esc 也關閉，並把這個 Esc 標成已處理（preventDefault）：外層同樣聽 Esc 的元件（批量列清除選取、
 * Modal 關閉）看到 defaultPrevented 就不再動作。關閉會同步重繪，外層在同一個事件傳到 window 前就可能把監聽掛回去，
 * 只靠「選單開著時不聽 Esc」擋不住。側欄彈窗與各種列操作選單共用。
 */
export default function useOutsideClick(ref, triggerRef, onClose, { escape = false } = {}) {
  useEffect(() => {
    const handlePointerDown = (e) => {
      if (!ref.current?.contains(e.target) && !triggerRef?.current?.contains(e.target)) onClose();
    };
    const handleKeyDown = (e) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      onClose();
    };
    document.addEventListener("mousedown", handlePointerDown);
    if (escape) document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      if (escape) document.removeEventListener("keydown", handleKeyDown);
    };
  }, [ref, triggerRef, onClose, escape]);
}
