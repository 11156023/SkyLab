import { useTranslation } from "react-i18next";
import EmptyState from "../EmptyState/EmptyState";

/**
 * 資源層級的「找不到」狀態：API 回 404（isNotFound）時用，
 * 與一般錯誤（ErrorState）區隔——資源不存在時重試沒有意義。
 *
 * @param {string} className 額外樣式（可選）
 */
export default function NotFoundState({ className }) {
  const { t } = useTranslation("common");
  return (
    <EmptyState
      icon="search_off"
      title={t("Error.notFoundTitle")}
      description={t("Error.notFoundDesc")}
      className={className}
    />
  );
}
