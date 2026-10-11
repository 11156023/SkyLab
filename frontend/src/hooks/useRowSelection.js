import { useCallback, useEffect, useRef, useState } from "react";

/* 列裡自己有動作的元素：勾選狀態下點這些照舊，不拿來勾選 */
const ROW_CONTROLS = "a, button, input, select, textarea, label, [role='button'], [role='menuitem']";

/** Shift 連選：在可選清單的順序裡，取上一次點的那筆到這筆之間（含兩端） */
export function rangeBetween(orderedIds, fromId, toId) {
  const from = orderedIds.indexOf(fromId);
  const to = orderedIds.indexOf(toId);
  if (from < 0 || to < 0) return [toId];
  const [start, end] = from < to ? [from, to] : [to, from];
  return orderedIds.slice(start, end + 1);
}

/**
 * 清單勾選（批量操作用）：搭配 components/DataToolbar 的工具列、批量列與勾選框。
 * - 只認畫面上看得到、可以勾的那些（selectableIds，依顯示順序）：篩選換了、翻頁了、
 *   或刷新後某筆消失／變得不能勾，它的勾選自動取消，免得之後切回來才發現它還勾著、被一起操作
 * - Shift＋點：從上一次點的那筆到這筆整段設成同一個狀態
 *
 * @param {Array<string|number>} selectableIds 目前看得到、可以勾的 id（依畫面順序）
 */
export default function useRowSelection(selectableIds) {
  const [selected, setSelected] = useState(() => new Set());
  const lastToggledRef = useRef(null);

  /* 用字串當依賴：每次 render 都是新陣列，內容沒變就不必重算 */
  const key = selectableIds.join("\n");
  useEffect(() => {
    const keep = new Set(selectableIds);
    setSelected((prev) => {
      const next = new Set([...prev].filter((id) => keep.has(id)));
      return next.size === prev.size ? prev : next;
    });
    // selectableIds 的內容由 key 代表；這次 render 的 selectableIds 就是 key 對應的那份
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const count = selectableIds.filter((id) => selected.has(id)).length;
  const allSelected = selectableIds.length > 0 && count === selectableIds.length;
  const someSelected = count > 0 && !allSelected;

  function toggle(id, shiftKey = false) {
    /* 範圍要在這裡先算好：setState 的 updater 會延到 render 才執行，那時 lastToggledRef 已經換成這一筆 */
    const targets = shiftKey && lastToggledRef.current != null
      ? rangeBetween(selectableIds, lastToggledRef.current, id)
      : [id];
    lastToggledRef.current = id;
    setSelected((prev) => {
      const next = new Set(prev);
      const checked = !prev.has(id);
      targets.forEach((target) => (checked ? next.add(target) : next.delete(target)));
      return next;
    });
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(selectableIds));
  }

  /**
   * 勾選狀態下（已勾了至少一筆）點整列也是勾選，Shift＋點一樣連選一段。
   * 掛在列（或列本身那顆開詳情的按鈕）的 onClick：回傳 true＝這次點擊拿來勾選了，呼叫端就不要再開詳情。
   * 列裡其他的按鈕、連結、勾選框照舊；從 portal（選單、對話框）冒泡上來的點擊不算。
   */
  function rowClick(id, event) {
    if (count === 0 || !selectableIds.includes(id)) return false;
    const row = event.currentTarget;
    if (!row.contains(event.target)) return false;
    const control = event.target.closest(ROW_CONTROLS);
    if (control && control !== row && row.contains(control)) return false;
    event.preventDefault();
    toggle(id, event.shiftKey);
    /* Shift＋點會順便反白一段文字，清掉 */
    if (event.shiftKey) window.getSelection?.()?.removeAllRanges();
    return true;
  }

  const clear = useCallback(() => setSelected(new Set()), []);
  /* 批量做完：只留失敗的那幾筆，可以直接再試一次 */
  const replace = useCallback((ids) => setSelected(new Set(ids)), []);

  return {
    selected,
    isSelected: (id) => selected.has(id),
    count,
    /* 勾選狀態：至少勾了一筆，這時點整列也是勾選（rowClick） */
    selecting: count > 0,
    allSelected,
    someSelected,
    toggle,
    toggleAll,
    rowClick,
    clear,
    replace,
  };
}
