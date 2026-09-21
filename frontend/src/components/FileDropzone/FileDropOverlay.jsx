import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import styles from "./FileDropzone.module.scss";

/**
 * 大容器（例如 AI 對話區）的放置提示：平常不佔空間，拖檔案進容器時才浮出虛線框，
 * 外觀與 FileDropzone 拖曳中一致。拖放事件由容器用 useFileDrop 接，這裡只負責畫面；
 * 只在拖曳中掛上（每次掛上都重播淡入），容器本身要 position: relative。
 *
 * 用法：
 *   const { dragging, dropProps } = useFileDrop(onFiles, { disabled });
 *   <div className={styles.panel} {...dropProps}>
 *     …
 *     {dragging && <FileDropOverlay />}
 *   </div>
 *
 * @param {string} [label] 提示文字（預設「放開以加入檔案」）
 */
export default function FileDropOverlay({ label }) {
  const { t } = useTranslation("common");
  return (
    <div className={styles.overlay} aria-hidden="true">
      <MIcon name="cloud_upload" size={32} />
      <span>{label ?? t("FileDropzone.dropToAdd")}</span>
    </div>
  );
}
