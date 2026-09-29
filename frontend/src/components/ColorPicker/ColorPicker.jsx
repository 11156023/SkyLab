import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { normalizeHex } from "../../utils/theme/derivePrimaryShades";
import styles from "./ColorPicker.module.scss";

/** 色碼欄：失焦或 Enter 時套用，無效輸入還原 */
function HexField({ value, onChange, label }) {
  const [draft, setDraft] = useState(value);
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!focused) setDraft(value);
  }, [value, focused]);

  function commit() {
    try {
      onChange(normalizeHex(draft));
    } catch {
      setDraft(value);
    }
  }

  return (
    <label className={styles.hexField}>
      <span>{label}</span>
      <input
        type="text"
        value={draft}
        onFocus={() => setFocused(true)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          setFocused(false);
          commit();
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
        maxLength={7}
        spellCheck={false}
      />
    </label>
  );
}

/**
 * 全站共用的顏色選擇：彩虹圈色點（中間是目前顏色）＋「選擇顏色」文字，整顆點了開系統調色盤；色碼欄留給進階使用者。
 * 不熟前端的人看不出原生色票格加色碼框是在選顏色，所以主要操作做成看得懂的色點。
 * 可另外傳入常用色（presets）排在前面，選中打勾加外圈；此時彩虹圈改叫「自訂顏色」。目前各處都不放常用色。
 *
 * @param {string}   value     目前顏色（#rrggbb）
 * @param {Function} onChange  (hex) => void
 * @param {string}   ariaLabel 整組的無障礙名稱（例如「選擇主色」）
 * @param {Array}    presets   選填，[{ label, value }]
 * @param {string}   className 選填，加在最外層（例如使用端要控制換行）
 */
export default function ColorPicker({ value, onChange, ariaLabel, presets = [], className = "" }) {
  const { t } = useTranslation("components");
  const current = (value ?? "").toLowerCase();
  const hasPresets = presets.length > 0;
  const isPreset = presets.some((preset) => preset.value === current);
  const pickLabel = hasPresets ? t("ColorPicker.custom") : t("ColorPicker.pick");

  return (
    <div className={`${styles.picker} ${className}`.trim()}>
      <div className={styles.swatches} role="group" aria-label={ariaLabel}>
        {presets.map((preset) => {
          const selected = preset.value === current;
          const name = preset.label;
          return (
            <button
              key={preset.value}
              type="button"
              className={`${styles.swatch} ${selected ? styles.swatchSelected : ""}`}
              style={{ "--swatch": preset.value }}
              aria-pressed={selected}
              aria-label={name}
              title={name}
              onClick={() => onChange(preset.value)}
            >
              {selected && <MIcon name="check" size={16} />}
            </button>
          );
        })}

        {/* 彩虹圈：整顆（含文字）都能點，打開系統調色盤；目前顏色不在常用色裡時，中間顯示該顏色。
            有常用色時才需要「＋」與勾勾來區分，沒有常用色時只顯示顏色本身 */}
        <label className={`${styles.custom} ${!isPreset ? styles.customSelected : ""}`} title={pickLabel}>
          <span className={styles.customSwatch} style={!isPreset ? { "--swatch": current } : undefined} aria-hidden="true">
            {hasPresets && <MIcon name={isPreset ? "add" : "check"} size={16} />}
          </span>
          {pickLabel}
          <input
            type="color"
            className={styles.nativeInput}
            value={current || "#000000"}
            onChange={(e) => onChange(e.target.value)}
            aria-label={ariaLabel ? `${ariaLabel}：${pickLabel}` : pickLabel}
          />
        </label>
      </div>

      <HexField value={current} onChange={onChange} label={t("ColorPicker.hexLabel")} />
    </div>
  );
}
