import styles from "./Classroom.module.scss";
import MIcon from "../MIcon";

/** 學生端：老師開始直播時顯示的橫幅，點擊開啟觀看視窗。 */
export default function LiveBanner({ onWatch, onDismiss }) {
  return (
    <div className={styles.liveBanner}>
      <span className={styles.liveDot} />
      <MIcon name="sensors" size={16} />
      <span>老師正在直播畫面</span>
      <button type="button" className={styles.liveWatchBtn} onClick={onWatch}>
        觀看直播
      </button>
      <button
        type="button"
        className={styles.liveDismiss}
        onClick={onDismiss}
        title="關閉提示"
      >
        <MIcon name="close" size={16} />
      </button>
    </div>
  );
}
