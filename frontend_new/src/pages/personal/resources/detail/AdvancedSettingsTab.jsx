import styles from "./ResourceDetailPage.module.scss";
import MIcon from "../../../../components/MIcon";

export default function AdvancedSettingsTab() {
  return (
    <div className={styles.tabStack}>
      <div className={styles.card}>
        <div className={styles.cardHeader}>
          <div>
            <h2 className={styles.cardTitle}>進階設定</h2>
            <p className={styles.cardDesc}>更多資源層級的進階選項</p>
          </div>
        </div>
        <div className={`${styles.cardBody} ${styles.comingSoon}`}>
          <MIcon name="construction" size={32} />
          <p>即將推出</p>
          <span className={styles.mutedText}>開機順序、網路設定等進階功能規劃中</span>
        </div>
      </div>
    </div>
  );
}
