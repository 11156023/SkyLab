import { useTranslation } from "react-i18next";
import EmptyState from "../EmptyState/EmptyState";
import MIcon from "../MIcon";
import styles from "./ErrorState.module.scss";

/**
 * 全站共用的「載入／操作失敗」狀態，文案固定為統一錯誤句（Error.title＋Error.desc）。
 * 有 onRetry 時顯示重試按鈕（全站不放常駐刷新鈕，錯誤狀態的重試是唯一例外）。
 *
 * @param {func}   onRetry   重試 callback（可選）
 * @param {string} className 額外樣式（可選）
 */
export default function ErrorState({ onRetry, className }) {
  const { t } = useTranslation("common");
  return (
    <EmptyState
      icon="error_outline"
      title={t("Error.title")}
      description={t("Error.desc")}
      className={className}
      action={
        onRetry && (
          <button type="button" className={styles.btnSecondary} onClick={onRetry}>
            <MIcon name="refresh" size={16} />
            {t("Error.retry")}
          </button>
        )
      }
    />
  );
}
