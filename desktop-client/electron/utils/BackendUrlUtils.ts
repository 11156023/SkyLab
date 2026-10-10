/**
 * 後端網址的檢查與正規化。主程序存檔前（SettingsService）與設定頁輸入時共用同一套規則，
 * 畫面上即時提示的錯誤跟真正存檔時擋下來的原因才會一致。
 * 純函式、不碰 Node／Electron API，渲染程序也能直接 import。
 */
export type BackendUrlProblem = "required" | "invalid" | "insecure" | "extra";

export type BackendUrlResult =
  { ok: true; url: string } | { ok: false; problem: BackendUrlProblem };

class BackendUrlUtils {
  public static parse(value: string): BackendUrlResult {
    const trimmed = String(value ?? "").trim();
    if (!trimmed) return { ok: false, problem: "required" };
    let url: URL;
    try {
      url = new URL(trimmed);
    } catch {
      return { ok: false, problem: "invalid" };
    }
    const isLocal =
      url.hostname === "localhost" || url.hostname === "127.0.0.1";
    if (url.protocol !== "https:" && !(isLocal && url.protocol === "http:")) {
      return { ok: false, problem: "insecure" };
    }
    if (url.username || url.password || url.search || url.hash) {
      return { ok: false, problem: "extra" };
    }
    return { ok: true, url: url.toString().replace(/\/$/, "") };
  }
}

export default BackendUrlUtils;
