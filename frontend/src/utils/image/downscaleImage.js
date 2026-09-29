/**
 * downscaleImage.js
 * 用 canvas 把圖片等比縮小到 maxSize（最長邊）內並轉成 JPEG。
 * 頭像上傳（縮小後傳後端）與自訂背景（轉 data URL 存本地）共用。
 * 錯誤訊息會被頁面直接 toast，所以依目前語系產生。
 */
import i18n from "../../i18n";

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve(img);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error(i18n.t("image.readFailed", { ns: "services" })));
    };
    img.src = url;
  });
}

/**
 * @param {File} file 使用者選擇的圖片檔
 * @param {{ maxSize?: number, quality?: number }} options
 * @returns {Promise<{ blob: Blob, dataUrl: string, width: number, height: number }>}
 */
export async function downscaleImage(file, { maxSize = 1920, quality = 0.85 } = {}) {
  const img = await loadImage(file);
  const scale = Math.min(1, maxSize / Math.max(img.naturalWidth, img.naturalHeight));
  const width = Math.max(1, Math.round(img.naturalWidth * scale));
  const height = Math.max(1, Math.round(img.naturalHeight * scale));

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  // JPEG 沒有透明度，先鋪白底避免 PNG 透明區變黑
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(img, 0, 0, width, height);

  const dataUrl = canvas.toDataURL("image/jpeg", quality);
  const blob = await new Promise((resolve, reject) =>
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error(i18n.t("image.convertFailed", { ns: "services" })))),
      "image/jpeg",
      quality
    )
  );
  return { blob, dataUrl, width, height };
}
