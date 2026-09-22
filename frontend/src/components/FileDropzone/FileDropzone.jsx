import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { LoadingSpinner } from "../LoadingState/LoadingState";
import useFileDrop from "../../hooks/useFileDrop";
import styles from "./FileDropzone.module.scss";

/**
 * 檔案上傳區塊：虛線框＋雲朵圖示＋說明＋「瀏覽檔案」，點整塊或把檔案拖進來都能選檔。
 * uploading 時內容換成共用的 3D 方塊載入動畫（LoadingSpinner），期間不接受新檔案。
 *
 * 整塊是 <label>，點哪裡都會開檔案選擇器；「瀏覽檔案」只是視覺上的按鈕（span），
 * 避免在可點區塊裡再巢狀一顆真的 button。input 用視覺隱藏而非 display: none，
 * 鍵盤 Tab 仍能聚焦（外框顯示 focus 環），按 Enter／空白鍵開選擇器。
 *
 * compact 是單行版，給表格列、頭像旁這類放不下大區塊的位置。寬度由外層決定
 * （預設滿寬），區塊窄到放不下「瀏覽檔案」時按鈕自動收起，整塊仍可點。
 *
 * @param {(files: File[]) => void} onFiles 選好或拖入檔案時呼叫（multiple 為 false 時只給第一個）
 * @param {string}  [accept]         <input accept>，例如 ".pdf,.png"；拖放不受此限，呼叫端仍要自行驗證
 * @param {boolean} [multiple=false]
 * @param {boolean} [compact=false]  單行精簡版
 * @param {boolean} [disabled=false] 例如已達數量上限
 * @param {boolean} [uploading=false] 上傳進行中：顯示載入動畫並暫停接收
 * @param {{ current: number, total: number }} [progress] 多檔依序上傳的進度，total > 1 時文字帶「（2/5）」
 * @param {string}  [title]          主文字（預設「選擇檔案，或拖曳到這裡」，multiple 時加註可多選）
 * @param {string}  [hint]           格式／大小等說明，顯示在主文字下方
 * @param {string}  [buttonLabel]    按鈕文字（預設「瀏覽檔案」）
 * @param {string}  [uploadingText]  上傳中文字（預設「上傳中…」）
 * @param {string}  [className]      外層位置調整用（寬度、格線位置等）
 */
export default function FileDropzone({
  onFiles,
  accept,
  multiple = false,
  compact = false,
  disabled = false,
  uploading = false,
  progress,
  title,
  hint,
  buttonLabel,
  uploadingText,
  className,
}) {
  const { t } = useTranslation("common");
  const inactive = disabled || uploading;

  const emit = (fileList) => {
    const files = Array.from(fileList ?? []);
    if (files.length === 0) return;
    onFiles(multiple ? files : files.slice(0, 1));
  };

  const { dragging, dropProps } = useFileDrop(emit, { disabled: inactive });

  const zoneClassName = [
    styles.zone,
    compact && styles.compact,
    dragging && styles.dragging,
    disabled && !uploading && styles.disabled,
    uploading && styles.uploading,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <label className={zoneClassName} {...dropProps} aria-busy={uploading}>
      <input
        type="file"
        className={styles.input}
        accept={accept}
        multiple={multiple}
        disabled={inactive}
        onChange={(e) => {
          emit(e.target.files);
          // 清空才能連續選同一個檔案（否則 onChange 不會再觸發）
          e.target.value = "";
        }}
      />

      {uploading ? (
        <span className={styles.status} role="status" aria-live="polite">
          <LoadingSpinner size={compact ? 28 : 44} />
          <span className={styles.title}>
            {uploadingText ??
              (progress?.total > 1
                ? t("FileDropzone.uploadingCount", { current: progress.current, total: progress.total })
                : t("FileDropzone.uploading"))}
          </span>
        </span>
      ) : (
        <>
          <span className={styles.icon}>
            <MIcon name="cloud_upload" size={compact ? 22 : 28} />
          </span>
          <span className={styles.text}>
            <span className={styles.title}>
              {title ?? t(multiple ? "FileDropzone.titleMultiple" : "FileDropzone.title")}
            </span>
            {hint && <span className={styles.hint}>{hint}</span>}
          </span>
          <span className={styles.browse} aria-hidden="true">
            {buttonLabel ?? t("FileDropzone.browse")}
          </span>
        </>
      )}
    </label>
  );
}
