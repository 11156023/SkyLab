import styles from "./Classroom.module.scss";
import MIcon from "../MIcon";

/** 學生自己的主控台：老師接管中時覆蓋顯示，提示輸入已被鎖定。 */
export default function TakeoverOverlay() {
  return (
    <div className={styles.takeoverOverlay}>
      <div className={styles.takeoverBox}>
        <span className={styles.takeoverIcon}>
          <MIcon name="back_hand" size={24} />
        </span>
        <p className={styles.takeoverTitle}>老師正在接管此畫面</p>
        <p className={styles.takeoverDesc}>你的鍵盤與滑鼠操作暫時停用</p>
      </div>
    </div>
  );
}
