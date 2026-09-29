import MIcon from "../../../components/MIcon";
import styles from "./Pagination.module.scss";

/**
 * 清單底部的「共 N 筆・第 x / y 頁」＋上一頁／下一頁。
 * 純呈現：page 從 0 起算，分頁邏輯（前端切片或後端 skip/limit）與文案 i18n 由各頁自行決定，
 * 這裡只負責版面與按鈕的停用判斷。使用者、稽核日誌、IP 管理三頁共用。
 */
export default function Pagination({ page, totalPages, info, prevLabel, nextLabel, onChange }) {
  return (
    <div className={styles.pagination}>
      <span className={styles.paginationInfo}>{info}</span>
      <div className={styles.paginationBtns}>
        <button
          type="button"
          className={styles.btnSecondary}
          disabled={page <= 0}
          onClick={() => onChange(Math.max(page - 1, 0))}
        >
          <MIcon name="chevron_left" size={16} />
          {prevLabel}
        </button>
        <button
          type="button"
          className={styles.btnSecondary}
          disabled={page + 1 >= totalPages}
          onClick={() => onChange(page + 1)}
        >
          {nextLabel}
          <MIcon name="chevron_right" size={16} />
        </button>
      </div>
    </div>
  );
}
