import { useState } from "react";

/* 只對拖進來的檔案反應：拖選取的文字或頁面內的元素不該跳出放置提示 */
const hasFiles = (e) => Array.from(e.dataTransfer?.types ?? []).includes("Files");

/**
 * useFileDrop
 * 讓任一容器可以拖放檔案：回傳拖曳中狀態，以及要展開到容器上的拖放事件。
 * FileDropzone 與 FileDropOverlay（對話區整塊可拖檔）共用這套判斷。
 *
 * 用法：
 *   const { dragging, dropProps } = useFileDrop(onFiles, { disabled });
 *   <div {...dropProps}>…</div>
 *
 * disabled 時照樣攔下拖放（preventDefault），只是不高亮也不交出檔案；
 * 不攔的話瀏覽器會直接開啟丟進來的檔案，整個頁面被換掉。
 *
 * @param {(files: File[]) => void} onFiles
 * @param {{ disabled?: boolean }} [options]
 * @returns {{ dragging: boolean, dropProps: object }}
 */
export default function useFileDrop(onFiles, { disabled = false } = {}) {
  const [dragging, setDragging] = useState(false);

  /* dragover 一定要 preventDefault，瀏覽器才會把容器當成可放置目標 */
  const handleDragOver = (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    if (!disabled) setDragging(true);
  };

  /* 游標移到子元素上也會觸發 dragleave，只有真的離開整塊才取消高亮 */
  const handleDragLeave = (e) => {
    if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false);
  };

  const handleDrop = (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    setDragging(false);
    if (disabled) return;
    const files = Array.from(e.dataTransfer.files ?? []);
    if (files.length > 0) onFiles(files);
  };

  return {
    dragging,
    dropProps: {
      onDragEnter: handleDragOver,
      onDragOver: handleDragOver,
      onDragLeave: handleDragLeave,
      onDrop: handleDrop,
    },
  };
}
