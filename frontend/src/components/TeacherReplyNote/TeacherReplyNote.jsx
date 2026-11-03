import MIcon from "../MIcon";
import styles from "./TeacherReplyNote.module.scss";

/**
 * 老師回覆提示框：學生週次頁（完整內容）與學生首頁本週卡（摘要）共用同一組外觀，
 * 用主色淡底跟自動檢查結果、一般內容區塊分開；是「玻璃卡內容區塊一律白底」規則明列的提示框例外。
 *
 * @param {string}  title     標題（「老師回覆」）
 * @param {string}  children  回覆內容，原樣保留換行
 * @param {number}  [clamp]   只顯示前 n 行（首頁摘要用 2），超過以省略號截斷；不傳就完整顯示
 * @param {string}  [className]
 * 其餘屬性（data-guide 等）直接落到外層 article
 */
export default function TeacherReplyNote({ title, children, clamp, className, ...rest }) {
  const classes = [styles.note, className].filter(Boolean).join(" ");
  const textStyle = clamp ? { WebkitLineClamp: clamp } : undefined;
  return (
    <article className={classes} {...rest}>
      <span className={styles.avatar} aria-hidden="true"><MIcon name="forum" size={18} /></span>
      <div className={styles.body}>
        <strong className={styles.title}>{title}</strong>
        <p className={clamp ? styles.textClamped : styles.text} style={textStyle}>{children}</p>
      </div>
    </article>
  );
}
