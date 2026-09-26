import { useTranslation } from "react-i18next";
import EmptyState from "../EmptyState/EmptyState";
import MIcon from "../MIcon";
import styles from "./ErrorState.module.scss";

/**
 * 資源層級的「找不到」狀態：API 回 404（isNotFound）時用，
 * 與一般錯誤（ErrorState）區隔——資源不存在時重試沒有意義。
 *
 * @param {func}   onBack    返回列表 callback（可選）
 * @param {string} className 額外樣式（可選）
 */
export default function NotFoundState({ onBack, className }) {
  const { t } = useTranslation("common");
  return (
    <EmptyState
      icon="search_off"
      title={t("Error.notFoundTitle")}
      description={t("Error.notFoundDesc")}
      className={className}
      action={
        onBack && (
          <button type="button" className={styles.btnSecondary} onClick={onBack}>
            <MIcon name="arrow_back" size={16} />
            {t("Error.backToList")}
          </button>
        )
      }
    />
  );
}
