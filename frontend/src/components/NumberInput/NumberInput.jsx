import { useState } from "react";
import { snapToRange } from "../../utils/quotaLimits";

/**
 * 可鍵入的數字框：編輯中不夾值，離開欄位（blur／Enter）才夾進 min～max 並對齊 step。
 *
 * 之前是在 onChange 就 clamp：全選後打「3」會被立刻壓成下限 20，再打「0」就變 200，
 * 使用者怎麼打都打不出 30。所以這裡用字串草稿接住輸入過程，允許暫時是空白或超出範圍，
 * 等使用者離開欄位再一次定稿。草稿存在時外部 value 的變化不會蓋掉正在打的字。
 *
 * - value：目前定稿值（與 min／max／step 同單位）
 * - onCommit(n)：離開欄位時收到夾過、對齊步進後的數字；清空或亂打就回到原值、不呼叫
 * - 其餘 props（className、disabled、aria-label…）直接落到 <input>
 */
export default function NumberInput({
  value, min, max, step = 1, onCommit, onBlur, onKeyDown, ...props
}) {
  const [draft, setDraft] = useState(null);

  const commit = () => {
    if (draft == null) return;
    setDraft(null);
    const n = Number(draft);
    if (draft.trim() === "" || !Number.isFinite(n)) return;
    const snapped = snapToRange(n, { min, max, step });
    if (snapped !== Number(value)) onCommit(snapped);
  };

  return (
    <input
      {...props}
      type="number"
      min={min}
      max={max}
      step={step}
      value={draft ?? value}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={(e) => { commit(); onBlur?.(e); }}
      onKeyDown={(e) => {
        /* Enter 只定稿、不送出表單；真正送出要按送出鈕 */
        if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); }
        onKeyDown?.(e);
      }}
    />
  );
}
