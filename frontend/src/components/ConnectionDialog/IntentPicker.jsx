/**
 * IntentPicker
 * 對話框第一步：「你要做什麼？」四張卡；呼叫端可用 intents 只留一部分
 * （課程環境模板沒有「上網」與「自己寫規則」）。
 * 選定後收合成一行，留一顆「更改」可以回頭；編輯既有發布時意圖鎖死不給改。
 * data-guide="connection-dialog-endpoints" 是防火牆導覽「表單」步驟的聚光目標（兩種狀態都掛）。
 */

import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { INTENT_META, INTENT_ORDER } from "./intents";
import styles from "./ConnectionDialog.module.scss";

export default function IntentPicker({ value, onChange, locked = false, intents = INTENT_ORDER }) {
  const { t } = useTranslation("components");

  if (value) {
    const meta = INTENT_META[value];
    return (
      <div className={styles.intentChip} data-guide="connection-dialog-endpoints">
        <MIcon name={meta.icon} size={18} />
        <strong>{t(meta.labelKey)}</strong>
        {!locked && (
          <button type="button" className={styles.intentChange} onClick={() => onChange(null)}>
            {t("ConnectionDialog.changeIntent")}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className={styles.field} data-guide="connection-dialog-endpoints">
      <span className={styles.fieldLabel}>{t("ConnectionDialog.intentPrompt")}</span>
      <div className={styles.intentGrid} role="group" aria-label={t("ConnectionDialog.intentPrompt")}>
        {intents.map((intent) => {
          const meta = INTENT_META[intent];
          return (
            <button
              key={intent}
              type="button"
              className={styles.intentCard}
              onClick={() => onChange(intent)}
            >
              <MIcon name={meta.icon} size={22} />
              <strong>{t(meta.labelKey)}</strong>
              <span>{t(meta.descKey)}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
