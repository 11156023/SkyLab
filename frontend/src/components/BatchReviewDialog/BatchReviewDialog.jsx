import { useState } from "react";
import MIcon from "../MIcon";
import Modal from "../Modal/Modal";
import styles from "./BatchReviewDialog.module.scss";

/* 名單最多列幾筆，其餘寫「等 N 筆」 */
const NAME_PREVIEW = 5;

/**
 * 批量審核的確認框：批量核准、批量拒絕共用（搭配 components/DataToolbar 的批量列）。
 * 列出這次會處理的項目（最多 5 筆）＋一段共用的審核備註（選填，會寫進每一筆）。
 * 外觀比照共用確認框：拒絕是紅色警告圖示＋危險鈕，核准是主色圖示＋主要鈕。
 * 文案由各頁自己給（單位、用詞各頁不同），這裡不碰語系。
 *
 * 開關動畫沿用 useDialogPresence，由父層決定掛載（同 Modal）。
 *
 * @param {"approve"|"reject"} tone
 * @param {string} title        「核准 3 筆申請？」
 * @param {string} description  「下列申請會被核准：」
 * @param {Array<{ id, icon?: string, name: string, sub?: string }>} items 這次會處理的項目
 * @param {(count: number) => string} moreLabel 超過 5 筆時最後一列的文字，例如「等 4 筆」
 * @param {string} commentLabel／commentPlaceholder 備註欄
 * @param {string} confirmLabel／cancelLabel 按鈕文字
 * @param {(comment: string|null) => void} onSubmit 備註去頭尾空白，沒填給 null
 */
export default function BatchReviewDialog({
  tone = "reject",
  title,
  description,
  items,
  moreLabel,
  commentLabel,
  commentPlaceholder,
  confirmLabel,
  cancelLabel,
  closing,
  onClose,
  onSubmit,
}) {
  const [comment, setComment] = useState("");
  const reject = tone === "reject";
  const preview = items.slice(0, NAME_PREVIEW);
  const rest = items.length - preview.length;

  return (
    <Modal
      as="form"
      onSubmit={(event) => { event.preventDefault(); onSubmit(comment.trim() || null); }}
      closing={closing}
      onClose={onClose}
      role={reject ? "alertdialog" : "dialog"}
      icon={(
        <span className={reject ? styles.iconDanger : styles.iconApprove}>
          <MIcon name={reject ? "warning" : "task_alt"} size={20} />
        </span>
      )}
      title={title}
      description={description}
      actions={(
        <>
          <button type="button" className={styles.btnSecondary} onClick={onClose}>{cancelLabel}</button>
          <button type="submit" className={reject ? styles.btnDanger : styles.btnPrimary}>{confirmLabel}</button>
        </>
      )}
    >
      <ul className={styles.list}>
        {preview.map((item) => (
          <li key={item.id} className={styles.item}>
            {item.icon && <MIcon name={item.icon} size={16} />}
            <span className={styles.itemName}>{item.name}</span>
            {item.sub && <span className={styles.itemSub}>{item.sub}</span>}
          </li>
        ))}
        {rest > 0 && <li className={styles.more}>{moreLabel(rest)}</li>}
      </ul>
      <label className={styles.field}>
        <span>{commentLabel}</span>
        <textarea
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          placeholder={commentPlaceholder}
          maxLength={2000}
        />
      </label>
    </Modal>
  );
}
