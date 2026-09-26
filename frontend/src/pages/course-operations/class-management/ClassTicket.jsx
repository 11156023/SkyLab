import { Fragment } from "react";
import { ticketBarcode } from "../../personal/dashboard/ticketBarcode";
import styles from "./ClassTicket.module.scss";

/* 條碼固定畫這麼多條、依比例塗滿：機器只有兩台時也看得出是條碼，不會變成兩大塊色塊 */
const BARS = 24;

/**
 * 班級票券（班級管理列表）：比照學生首頁「目前加入的課堂」的課程票券（CourseTicket），整張票是一顆按鈕。
 * 兩側缺口與撕線把票面（何時上課、學期期間）和票根分開；
 * 票根的條碼是進度——準備中是班級設定步驟，其餘是機器建立。
 *
 * @param {string} status   班級狀態，決定狀態點與條碼的顏色
 * @param {{ key: string, label: string, value: string, note?: string }[]} fields 票面欄位（note 接在標籤後面、用點隔開）
 * @param {{ label: string, done: number, total: number }} progress 票根的進度
 * @param {() => void} onOpen 點票券：準備中回到設定步驟，其餘進入班級
 */
export default function ClassTicket({ id, status, statusLabel, term, title, hint, fields, progress, onOpen }) {
  const ratio = progress.total > 0 ? Math.min(progress.done / progress.total, 1) : 0;
  const bars = ticketBarcode(id, BARS, Math.round(ratio * BARS));

  return (
    <button type="button" className={styles.ticket} data-status={status} onClick={onOpen}>
      <span className={styles.paper}>
        <span className={styles.main}>
          <span className={styles.top}>
            <span className={styles.status}>{statusLabel}</span>
            {term && <span className={styles.term}>{term}</span>}
          </span>
          <strong className={styles.title}>{title}</strong>
          {hint && <span className={styles.hint}>{hint}</span>}
          <span className={styles.fields}>
            {fields.map((field) => (
              <span key={field.key} className={styles.field}>
                <span className={styles.fieldLabel}>
                  {field.label}
                  {field.note && <span className={styles.fieldNote}>{field.note}</span>}
                </span>
                <span className={styles.fieldValue}>{field.value}</span>
              </span>
            ))}
          </span>
        </span>

        <span className={styles.stub}>
          <span className={styles.barcode} aria-hidden="true">
            {bars.map((bar, index) => (
              <Fragment key={index}>
                <span className={bar.done ? styles.barDone : styles.bar} style={{ flexGrow: bar.width }} />
                {bar.gap > 0 && <span style={{ flexGrow: bar.gap }} />}
              </Fragment>
            ))}
          </span>
          <span className={styles.count}>
            <strong>{progress.done}/{progress.total}</strong>
            <small>{progress.label}</small>
          </span>
        </span>
      </span>
    </button>
  );
}
