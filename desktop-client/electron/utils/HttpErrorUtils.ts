const MAX_MESSAGE_LENGTH = 200;

/* 把後端非 200 的回應轉成一行短訊息，放進 BACKEND_ERROR 的 detail：
   FastAPI 的 {"detail": "..."} 取 detail；HTML（例如 nginx 504 頁）、空內容
   或沒有 detail 字串的 JSON 只回報狀態碼；其餘純文字壓成一行並截斷。
   畫面依錯誤碼顯示在地化文案（src/utils/errors.ts），這段只進記錄檔與 DevTools，
   免得記錄檔被整頁 HTML 塞滿 */
class HttpErrorUtils {
  public static describe(status: number, body: string): string {
    const fallback = `Server returned ${status}.`;
    const text = (body || "").trim();
    if (!text || HttpErrorUtils.looksLikeHtml(text)) return fallback;

    let message = text;
    try {
      const data = JSON.parse(text);
      if (typeof data?.detail !== "string") return fallback;
      message = data.detail;
    } catch {
      // 不是 JSON，當純文字顯示
    }
    message = message.replace(/\s+/g, " ").trim();
    if (!message) return fallback;
    return message.length > MAX_MESSAGE_LENGTH
      ? `${message.slice(0, MAX_MESSAGE_LENGTH - 1)}…`
      : message;
  }

  private static looksLikeHtml(text: string): boolean {
    return text.startsWith("<") || /<(html|head|body)[\s>]/i.test(text);
  }
}

export default HttpErrorUtils;
