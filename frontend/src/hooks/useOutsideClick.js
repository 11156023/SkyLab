import { useEffect } from "react";

/**
 * 點到彈窗（ref）與觸發鈕（triggerRef）以外的地方就呼叫 onClose；
 * escape=true 時按 Esc 也關閉。側欄彈窗與各種列操作選單共用。
 */
export default function useOutsideClick(ref, triggerRef, onClose, { escape = false } = {}) {
  useEffect(() => {
    const handlePointerDown = (e) => {
      if (!ref.current?.contains(e.target) && !triggerRef?.current?.contains(e.target)) onClose();
    };
    const handleKeyDown = (e) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", handlePointerDown);
    if (escape) document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      if (escape) document.removeEventListener("keydown", handleKeyDown);
    };
  }, [ref, triggerRef, onClose, escape]);
}
