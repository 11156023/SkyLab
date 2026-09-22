/**
 * 依序上傳多個檔案（後端一次只收一個）。每個檔開始前回報進度，
 * 單一檔失敗就記下來、繼續傳下一個，最後把失敗清單交回給呼叫端合併提示。
 *
 * @param {File[]} files
 * @param {(file: File) => Promise<unknown>} uploadOne 上傳單一檔案，成功後的畫面更新也放這裡
 * @param {(progress: { current: number, total: number }) => void} [onProgress]
 * @returns {Promise<{ failed: string[], lastError: unknown }>} failed 為失敗的檔名
 */
export async function uploadSequentially(files, uploadOne, onProgress) {
  const failed = [];
  let lastError = null;
  for (const [idx, file] of files.entries()) {
    onProgress?.({ current: idx + 1, total: files.length });
    try {
      await uploadOne(file);
    } catch (error) {
      failed.push(file.name);
      lastError = error;
    }
  }
  return { failed, lastError };
}
